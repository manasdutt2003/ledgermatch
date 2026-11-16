"""Match bank transactions to ledger entries.

The engine works in passes. Each pass handles one way that the two records can
describe the same money, and removes what it matches before the next pass runs:

1. one-to-one   same amount, close dates; the best overall pairing is chosen per amount
2. batch        one bank line equals the sum of several ledger entries (a deposit of many receipts)
3. split        one ledger entry equals the sum of several bank lines (a payment made in parts)
4. fee          a receipt that arrived short by a payment processor's fee
5. suggestions  likely keying errors; reported for a human, never counted as matched

A group (batch or split) is accepted only when exactly one combination adds up,
or one is clearly better than the rest. Ambiguous groups are reported, not guessed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from .parse import Txn


@dataclass
class Config:
    early_days: int = 3  # the bank line may be dated this many days before the ledger entry
    late_days: int = 10  # ... or this many days after it
    min_score: float = 0.15  # a one-to-one pair needs at least this score
    max_group: int = 6  # most items in a batch or split
    group_span_days: int = 6  # the items in a group must fall within this many days
    group_candidates: int = 40  # most candidates considered for one group; beyond this the group is left for review
    batch_days: int = 3  # receipts are banked within this many days
    fee_percent: float = 3.5  # largest processor fee, as a percentage ...
    fee_fixed_cents: int = 50  # ... plus this fixed part
    outstanding_days: int = 10  # ledger entries this close to the statement end may simply not have cleared


@dataclass
class Match:
    kind: str  # one_to_one | batch | split | fee
    bank: list[Txn]
    ledger: list[Txn]
    score: float
    reason: str
    ambiguous: bool = False

    @property
    def diff_cents(self) -> int:
        """Bank total minus ledger total. Non-zero only for fee matches."""
        return sum(t.cents for t in self.bank) - sum(t.cents for t in self.ledger)


@dataclass
class Result:
    matches: list[Match] = field(default_factory=list)
    unmatched_bank: list[tuple[Txn, str]] = field(default_factory=list)  # (transaction, likely reason)
    unmatched_ledger: list[tuple[Txn, str]] = field(default_factory=list)
    suggestions: list[tuple[Txn, Txn, str]] = field(default_factory=list)  # (bank, ledger, why)
    notes: list[str] = field(default_factory=list)

    def links(self) -> set[tuple[str, str]]:
        """Every (bank id, ledger id) pair the engine asserts."""
        return {(b.id, l.id) for m in self.matches for b in m.bank for l in m.ledger}


def text_similarity(a: Txn, b: Txn) -> float:
    """Share of the shorter description's words found in the other, allowing bank truncation."""
    if not a.tokens or not b.tokens:
        return 0.0
    short, long = (a.tokens, b.tokens) if len(a.tokens) <= len(b.tokens) else (b.tokens, a.tokens)
    hits = 0
    for word in short:
        if word in long or (len(word) >= 4 and any(other.startswith(word) or word.startswith(other) for other in long if len(other) >= 4)):
            hits += 1
    return hits / len(short)


def pair_score(bank: Txn, ledger: Txn, cfg: Config) -> float | None:
    """Score a same-amount pair, or None if the dates are too far apart."""
    lag = (bank.date - ledger.date).days
    if lag < -cfg.early_days or lag > cfg.late_days:
        return None
    window = max(cfg.early_days, cfg.late_days)
    date_score = 1 - min(abs(lag), window) / window
    score = 0.4 * date_score + 0.6 * text_similarity(bank, ledger)
    if bank.refs & ledger.refs:
        score += 1.0  # a shared reference number outweighs everything else
    return score


def _one_to_one(bank: list[Txn], ledger: list[Txn], cfg: Config) -> list[Match]:
    by_amount: dict[int, tuple[list[Txn], list[Txn]]] = defaultdict(lambda: ([], []))
    for txn in bank:
        by_amount[txn.cents][0].append(txn)
    for txn in ledger:
        by_amount[txn.cents][1].append(txn)

    matches = []
    for cents, (bs, ls) in by_amount.items():
        if not bs or not ls:
            continue
        scores = np.full((len(bs), len(ls)), -1.0)
        for i, b in enumerate(bs):
            for j, l in enumerate(ls):
                score = pair_score(b, l, cfg)
                if score is not None and score >= cfg.min_score:
                    scores[i, j] = score
        # Choose the pairing with the highest total score across everything sharing this amount.
        rows, cols = linear_sum_assignment(np.where(scores < 0, -1e6, scores), maximize=True)
        for i, j in zip(rows, cols):
            score = scores[i, j]
            if score < 0:
                continue
            # Ambiguous if either side had another candidate scoring almost as well.
            rivals = [scores[i, k] for k in range(len(ls)) if k != j] + [scores[k, j] for k in range(len(bs)) if k != i]
            ambiguous = any(r >= 0 and score - r < 0.05 for r in rivals)
            lag = (bs[i].date - ls[j].date).days
            why = "same amount" + (", shared reference" if bs[i].refs & ls[j].refs else "") + f", bank {lag:+d} day(s)"
            matches.append(Match("one_to_one", [bs[i]], [ls[j]], float(score), why, ambiguous))
    return matches


def _subsets(target: int, items: list[Txn], max_size: int, limit: int = 40, budget: int = 150_000) -> tuple[list[list[Txn]], bool]:
    """Subsets of two or more ``items`` whose amounts sum to ``target`` (all amounts share its sign).

    Also returns whether the search was exhaustive. If it was cut short, there may be
    combinations it never saw, so a single result cannot be called the only one.
    """
    pool = sorted(items, key=lambda t: -abs(t.cents))
    amounts = [abs(t.cents) for t in pool]
    suffix = [0] * (len(amounts) + 1)
    for i in range(len(amounts) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + amounts[i]
    found: list[list[Txn]] = []
    steps = 0

    def search(start: int, remaining: int, chosen: list[int]) -> None:
        nonlocal steps
        steps += 1
        if steps > budget or len(found) >= limit:
            return
        if remaining == 0:
            if len(chosen) >= 2:
                found.append([pool[i] for i in chosen])
            return
        if len(chosen) == max_size or start == len(amounts) or suffix[start] < remaining:
            return
        for i in range(start, len(amounts)):
            if amounts[i] > remaining:
                continue
            if amounts[i] * (max_size - len(chosen)) < remaining:
                break  # amounts are descending: nothing from here on can reach the target in the slots left
            chosen.append(i)
            search(i + 1, remaining - amounts[i], chosen)
            chosen.pop()

    search(0, abs(target), [])
    return found, steps <= budget and len(found) < limit


def _group_matches(singles: list[Txn], pool: list[Txn], kind: str, cfg: Config, notes: list[str]) -> list[Match]:
    """Match each of ``singles`` to a group from ``pool`` that sums to it."""
    matches, used, done = [], set(), set()
    by_sign = {1: [t for t in pool if t.cents > 0], -1: [t for t in pool if t.cents < 0]}
    undecided: dict[str, int] = {}
    progress = True
    while progress:
        # Each accepted group removes candidates, which can leave another group with only one way to add up.
        progress = False
        for single in sorted(singles, key=lambda t: -abs(t.cents)):
            if single.id in done:
                continue
            outcome = _solve_group(single, by_sign[1 if single.cents > 0 else -1], used, kind, cfg)
            if isinstance(outcome, Match):
                matches.append(outcome)
                used.update(t.id for t in (outcome.ledger if kind == "batch" else outcome.bank))
                done.add(single.id)
                undecided.pop(single.id, None)
                progress = True
            elif outcome:
                undecided[single.id] = outcome
    by_id = {t.id: t for t in singles}
    for single_id, count in undecided.items():
        single = by_id[single_id]
        notes.append(f"{single.id} ({single.cents / 100:,.2f}) could be made up in several ways ({count} candidates or combinations); left unmatched for review")
    return matches


def _solve_group(single: Txn, same_sign: list[Txn], used: set, kind: str, cfg: Config) -> Match | int | None:
    """The group that adds up to ``single``, or the number of rival combinations if it is unclear."""
    candidates = []
    for other in same_sign:
        if other.id in used or abs(other.cents) >= abs(single.cents):
            continue
        if kind == "batch":
            # Receipts are recorded first and banked together shortly after.
            lag = (single.date - other.date).days
            in_window = 0 <= lag <= cfg.batch_days
        else:
            # Instalments of one payment name the same counterparty, which keeps the pool small.
            lag = (other.date - single.date).days
            in_window = -cfg.early_days <= lag <= cfg.late_days and (bool(single.refs & other.refs) or text_similarity(single, other) >= 0.5)
        if in_window:
            candidates.append((abs(lag), other))
    if len(candidates) < 2:
        return None
    if len(candidates) > cfg.group_candidates:
        return len(candidates)  # too many possibilities to search exhaustively, so no claim is made
    nearby = [other for _, other in candidates]

    scored = []
    groups, exhaustive = _subsets(single.cents, nearby, cfg.max_group)
    for group in groups:
        dates = [t.date for t in group]
        span = (max(dates) - min(dates)).days
        if span > cfg.group_span_days:
            continue
        closeness = 1 - span / (cfg.group_span_days + 1)
        wording = sum(text_similarity(single, t) for t in group) / len(group)
        shared_ref = any(single.refs & t.refs for t in group)
        scored.append((0.5 * closeness + 0.5 * wording + (1.0 if shared_ref else 0.0), group))
    if not scored:
        return None
    scored.sort(key=lambda pair: -pair[0])
    best_score, best = scored[0]
    if not exhaustive or (len(scored) > 1 and best_score - scored[1][0] < 0.05):
        return max(len(scored), 2)
    if kind == "batch":
        return Match("batch", [single], best, best_score, f"{len(best)} ledger entries add up to this bank line")
    return Match("split", best, [single], best_score, f"{len(best)} bank lines add up to this ledger entry")


def _fee_matches(bank: list[Txn], ledger: list[Txn], cfg: Config) -> list[Match]:
    """Receipts that reached the bank short by a plausible processor fee."""
    options = []
    for b in bank:
        if b.cents <= 0:
            continue
        for l in ledger:
            fee = l.cents - b.cents
            if l.cents <= 0 or fee <= 0 or fee > l.cents * cfg.fee_percent / 100 + cfg.fee_fixed_cents:
                continue
            lag = (b.date - l.date).days
            if lag < -cfg.early_days or lag > cfg.late_days:
                continue
            wording = text_similarity(b, l)
            shared_ref = bool(b.refs & l.refs)
            if wording < 0.5 and not shared_ref:
                continue  # an amount that is merely close is not evidence on its own
            options.append((wording + (1.0 if shared_ref else 0.0) - fee / max(l.cents, 1), b, l, fee))
    options.sort(key=lambda option: -option[0])
    matches, used = [], set()
    for score, b, l, fee in options:
        if b.id in used or l.id in used:
            continue
        used.update((b.id, l.id))
        rival = any((ob.id == b.id) != (ol.id == l.id) and score - other < 0.05 for other, ob, ol, _ in options)
        matches.append(Match("fee", [b], [l], float(score), f"bank is {fee / 100:,.2f} short ({fee / l.cents:.1%}), consistent with a processing fee", rival))
    return matches


def _keying_error(a: int, b: int) -> str | None:
    """Describe how two amounts could be the same figure mistyped, if they could."""
    x, y = str(abs(a)), str(abs(b))
    if a == b or (a > 0) != (b > 0):
        return None
    if len(x) == len(y):
        differing = [i for i in range(len(x)) if x[i] != y[i]]
        if len(differing) == 2 and differing[1] == differing[0] + 1 and x[differing[0]] == y[differing[1]] and x[differing[1]] == y[differing[0]]:
            return "two adjacent digits swapped"
        if len(differing) == 1:
            return "one digit different"
    if abs(a) == abs(b) * 10 or abs(b) == abs(a) * 10 or abs(a) == abs(b) * 100 or abs(b) == abs(a) * 100:
        return "decimal point shifted"
    return None


def _suggestions(bank: list[Txn], ledger: list[Txn], cfg: Config) -> list[tuple[Txn, Txn, str]]:
    found, used = [], set()
    for b in bank:
        for l in ledger:
            if l.id in used:
                continue
            lag = (b.date - l.date).days
            if lag < -cfg.early_days or lag > cfg.late_days:
                continue
            how = _keying_error(b.cents, l.cents)
            if how and (b.refs & l.refs or text_similarity(b, l) >= 0.6):
                found.append((b, l, f"likely the same transaction with {how}: bank {b.cents / 100:,.2f}, ledger {l.cents / 100:,.2f}"))
                used.add(l.id)
                break
    return found


BANK_CHARGE_WORDS = {"FEE", "FEES", "CHARGE", "CHARGES", "INTEREST", "COMMISSION", "GST", "TAX", "MAINTENANCE", "SERVICE"}


def reconcile(bank: list[Txn], ledger: list[Txn], cfg: Config | None = None) -> Result:
    cfg = cfg or Config()
    result = Result()
    open_bank = {t.id: t for t in bank}
    open_ledger = {t.id: t for t in ledger}

    def take(matches: list[Match]) -> None:
        for match in matches:
            result.matches.append(match)
            for t in match.bank:
                del open_bank[t.id]
            for t in match.ledger:
                del open_ledger[t.id]

    take(_one_to_one(list(open_bank.values()), list(open_ledger.values()), cfg))
    take(_group_matches(list(open_bank.values()), list(open_ledger.values()), "batch", cfg, result.notes))
    take(_group_matches(list(open_ledger.values()), list(open_bank.values()), "split", cfg, result.notes))
    # Keying errors first: a mistyped amount can sit close enough to the real one to look like a fee.
    result.suggestions = _suggestions(list(open_bank.values()), list(open_ledger.values()), cfg)
    flagged = {b.id for b, _, _ in result.suggestions} | {l.id for _, l, _ in result.suggestions}
    take(
        _fee_matches(
            [t for t in open_bank.values() if t.id not in flagged],
            [t for t in open_ledger.values() if t.id not in flagged],
            cfg,
        )
    )

    suggested_bank = {b.id: why for b, _, why in result.suggestions}
    suggested_ledger = {l.id: why for _, l, why in result.suggestions}
    statement_end = max((t.date for t in bank), default=None)

    seen = defaultdict(list)
    for t in ledger:
        seen[(t.cents, t.date, t.tokens)].append(t.id)

    for t in open_bank.values():
        if t.id in suggested_bank:
            reason = "amount differs from a similar ledger entry (see suggestions)"
        elif {w.upper() for w in t.desc.replace("/", " ").split()} & BANK_CHARGE_WORDS:
            reason = "bank charge or interest not yet recorded in the ledger"
        else:
            reason = "on the statement but not in the ledger"
        result.unmatched_bank.append((t, reason))

    for t in open_ledger.values():
        twins = [other for other in seen[(t.cents, t.date, t.tokens)] if other != t.id]
        if t.id in suggested_ledger:
            reason = "amount differs from a similar bank line (see suggestions)"
        elif twins and any(other not in open_ledger for other in twins):
            reason = f"possible duplicate of {twins[0]}, which is already matched"
        elif statement_end and (statement_end - t.date).days <= cfg.outstanding_days:
            reason = "recent entry; probably not cleared by the statement date"
        else:
            reason = "in the ledger but not on the statement"
        result.unmatched_ledger.append((t, reason))

    result.matches.sort(key=lambda m: (m.bank[0].date, m.bank[0].id))
    return result


def detect_flipped_signs(bank: list[Txn], ledger: list[Txn]) -> bool:
    """True if the ledger looks like it uses the opposite sign convention to the bank."""
    bank_amounts = defaultdict(int)
    for t in bank:
        bank_amounts[t.cents] += 1
    same = sum(1 for t in ledger if bank_amounts.get(t.cents))
    opposite = sum(1 for t in ledger if bank_amounts.get(-t.cents))
    return opposite > 2 * same and opposite >= 5
