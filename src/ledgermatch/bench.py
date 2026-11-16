"""A synthetic month of bank and ledger activity with known answers, for measuring accuracy.

The generator builds a ledger, then derives the bank statement from it the way
real statements differ from books: clearing delays, bank-style descriptions,
receipts deposited together, payments made in parts, processor fees, bank
charges the books do not have yet, cheques that have not cleared, duplicated
entries and mistyped amounts. Because it builds both sides, it knows exactly
which lines belong together.

This is a model of real data, not real data. Results on it show how the engine
handles these specific situations; they are not a measurement on any real company's books.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from .match import Config, Match, Result, reconcile
from .parse import Txn, make_txn

FIRST = "Apex Blue Cedar Delta Evergreen Falcon Granite Harbor Indus Jade Kestrel Lotus Meridian Nimbus Orchid Pioneer Quartz Radiant Summit Tidal Unity Vertex Willow Zenith".split()
SECOND = "Traders Logistics Foods Textiles Systems Pharma Motors Builders Exports Papers Plastics Electricals Agencies Hardware Fabrics Chemicals".split()
PRICE_POINTS = [4900, 9900, 19900, 49900, 99900, 150000, 250000]  # repeated amounts, e.g. subscriptions
BANK_PREFIX_IN = ["NEFT CR ", "IMPS/", "UPI/", "ACH CR ", "RTGS CR ", ""]
BANK_PREFIX_OUT = ["NEFT DR ", "IMPS/", "ACH DR ", "CHQ PAID ", "TRF TO ", ""]

DIFFICULTY = {
    #          share of bank lines carrying the reference, share of repeated amounts, longest clearing delay, name length kept
    "easy": dict(ref=0.75, repeated=0.15, max_lag=3, keep=22),
    "medium": dict(ref=0.40, repeated=0.35, max_lag=5, keep=14),
    "hard": dict(ref=0.15, repeated=0.55, max_lag=8, keep=9),
}


@dataclass
class Scenario:
    bank: list[Txn]
    ledger: list[Txn]
    truth: set[tuple[str, str]]  # (bank id, ledger id) pairs that really belong together
    keying_errors: set[tuple[str, str]]  # same transaction, different amounts: should be suggested, not matched
    kinds: dict[str, str]  # ledger id -> what happened to it
    twins: dict[str, str]  # duplicate ledger id -> the identical entry it copies


def generate(seed: int = 1, entries: int = 400, difficulty: str = "medium") -> Scenario:
    rng = random.Random(seed)
    knobs = DIFFICULTY[difficulty]
    start = date(2026, 10, 1)
    parties = [f"{a} {b}" for a in FIRST for b in SECOND]
    rng.shuffle(parties)
    customers, vendors = parties[:45], parties[45:75]

    bank: list[Txn] = []
    ledger: list[Txn] = []
    truth: set[tuple[str, str]] = set()
    keying: set[tuple[str, str]] = set()
    kinds: dict[str, str] = {}
    twins: dict[str, str] = {}
    counters = {"b": 0, "l": 0, "doc": 20000}

    def new_ledger(day: int, cents: int, party: str, kind: str) -> Txn:
        counters["l"] += 1
        counters["doc"] += rng.randint(1, 9)
        label = "INV" if cents > 0 else rng.choice(["BILL", "CHQ"])
        ref = f"{label}-{counters['doc']}"
        text = f"{'Receipt from' if cents > 0 else 'Payment to'} {party} Pvt Ltd"
        txn = make_txn(f"L{counters['l']:05d}", start + timedelta(days=day), cents, text, ref, "ledger")
        ledger.append(txn)
        kinds[txn.id] = kind
        return txn

    def new_bank(when: date, cents: int, text: str) -> Txn:
        counters["b"] += 1
        txn = make_txn(f"B{counters['b']:05d}", when, cents, text, "", "bank")
        bank.append(txn)
        return txn

    def bank_text(entry: Txn, party: str) -> str:
        prefix = rng.choice(BANK_PREFIX_IN if entry.cents > 0 else BANK_PREFIX_OUT)
        name = party.upper()[: knobs["keep"]].strip()
        tail = f" {entry.ref.replace('-', '')}" if rng.random() < knobs["ref"] else f" {rng.randint(100000000, 999999999)}"
        return f"{prefix}{name}{tail}"

    def cleared(entry: Txn) -> date:
        lag = rng.choices(range(-1, knobs["max_lag"] + 1), weights=[1] + [6, 8] + [3] * (knobs["max_lag"] - 1))[0]
        return entry.date + timedelta(days=lag)

    def amount(inflow: bool) -> int:
        if rng.random() < knobs["repeated"]:
            cents = rng.choice(PRICE_POINTS)
        else:
            cents = rng.randint(1500, 900000)
        return cents if inflow else -cents

    made = 0
    while made < entries:
        roll = rng.random()
        inflow = rng.random() < 0.6
        party = rng.choice(customers if inflow else vendors)
        day = rng.randint(0, 27)

        if roll < 0.62:  # ordinary: one ledger entry, one bank line
            entry = new_ledger(day, amount(inflow), party, "one_to_one")
            truth.add((new_bank(cleared(entry), entry.cents, bank_text(entry, party)).id, entry.id))
            made += 1
        elif roll < 0.72:  # several receipts banked as one deposit
            size = rng.randint(2, 5)
            group = [new_ledger(day, amount(True), rng.choice(customers), "batch") for _ in range(size)]
            line = new_bank(group[0].date + timedelta(days=rng.randint(0, 2)), sum(t.cents for t in group), f"CASH DEPOSIT BR {rng.randint(100, 999)} SLIP {rng.randint(10000, 99999)}")
            truth.update((line.id, t.id) for t in group)
            made += size
        elif roll < 0.78:  # one payment made in instalments
            parts = rng.randint(2, 3)
            total = -rng.randint(60000, 900000)
            entry = new_ledger(day, total, party, "split")
            cuts = sorted(rng.sample(range(1000, -total - 1000), parts - 1))
            pieces = [b - a for a, b in zip([0] + cuts, cuts + [-total])]
            for index, piece in enumerate(pieces):
                line = new_bank(entry.date + timedelta(days=index * rng.randint(0, 2)), -piece, bank_text(entry, party))
                truth.add((line.id, entry.id))
            made += 1
        elif roll < 0.85:  # card receipt, paid out net of the processor's fee
            entry = new_ledger(day, rng.randint(5000, 400000), party, "fee")
            fee = round(entry.cents * 0.029) + 30
            line = new_bank(entry.date + timedelta(days=rng.randint(1, 3)), entry.cents - fee, f"PAYOUT {party.upper()[: knobs['keep']]} {rng.randint(1000000, 9999999)}")
            truth.add((line.id, entry.id))
            made += 1
        elif roll < 0.90:  # recorded in the books, not yet through the bank
            new_ledger(rng.randint(22, 27), amount(False), party, "outstanding")
            made += 1
        elif roll < 0.94:  # the same entry keyed twice
            entry = new_ledger(day, amount(inflow), party, "one_to_one")
            truth.add((new_bank(cleared(entry), entry.cents, bank_text(entry, party)).id, entry.id))
            copy = make_txn(f"L{counters['l'] + 1:05d}", entry.date, entry.cents, entry.desc, entry.ref, "ledger")
            counters["l"] += 1
            ledger.append(copy)
            kinds[copy.id] = "duplicate"
            twins[copy.id] = entry.id
            made += 2
        elif roll < 0.97:  # amount mistyped in the ledger
            real = rng.randint(12000, 900000)
            digits = list(str(real))
            spots = [i for i in range(len(digits) - 1) if digits[i] != digits[i + 1]]
            if not spots:
                continue
            i = rng.choice(spots)
            digits[i], digits[i + 1] = digits[i + 1], digits[i]
            wrong = int("".join(digits))
            sign = 1 if inflow else -1
            entry = new_ledger(day, sign * wrong, party, "keying_error")
            prefix = rng.choice(BANK_PREFIX_IN if inflow else BANK_PREFIX_OUT)
            line = new_bank(cleared(entry), sign * real, f"{prefix}{party.upper()[: max(knobs['keep'], 12)]} {entry.ref.replace('-', '')}")
            keying.add((line.id, entry.id))
            made += 1
        else:  # bank-only lines
            text = rng.choice(["MONTHLY SERVICE CHARGE", "SMS ALERT CHARGES GST", "INTEREST CREDIT", "NEFT CHARGES", "ATM CASH WITHDRAWAL"])
            cents = rng.randint(500, 60000) * (1 if "INTEREST" in text else -1)
            new_bank(start + timedelta(days=day), cents, text)

    rng.shuffle(bank)
    rng.shuffle(ledger)
    return Scenario(bank, ledger, truth, keying, kinds, twins)


def baseline(bank: list[Txn], ledger: list[Txn], cfg: Config | None = None) -> Result:
    """What a spreadsheet lookup does: same amount, nearest date, first come first served."""
    cfg = cfg or Config()
    result = Result()
    free = {t.id: t for t in ledger}
    for b in sorted(bank, key=lambda t: t.date):
        options = [l for l in free.values() if l.cents == b.cents and -cfg.early_days <= (b.date - l.date).days <= cfg.late_days]
        if options:
            choice = min(options, key=lambda l: (abs((b.date - l.date).days), l.id))
            del free[choice.id]
            result.matches.append(Match("one_to_one", [b], [choice], 1.0, "same amount, nearest date"))
    return result


def score(links: set[tuple[str, str]], truth: set[tuple[str, str]], twins: dict[str, str] | None = None) -> dict[str, float]:
    """Precision and recall of the asserted links.

    A duplicated ledger entry is identical to its original in every field, so
    nothing can tell them apart: matching the bank line to either one counts as correct.
    """
    twins = twins or {}
    links = {(b, twins.get(l, l)) for b, l in links}
    correct = len(links & truth)
    precision = correct / len(links) if links else 1.0
    recall = correct / len(truth) if truth else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "asserted": len(links), "correct": correct, "true": len(truth)}


def run_benchmark(seeds: range = range(1, 11), entries: int = 400) -> list[dict]:
    rows = []
    for difficulty in DIFFICULTY:
        totals = {"engine": [0, 0, 0], "baseline": [0, 0, 0]}  # correct, asserted, true
        caught = possible = 0
        by_kind: dict[str, list[int]] = {}
        for seed in seeds:
            scenario = generate(seed, entries, difficulty)
            result = reconcile(scenario.bank, scenario.ledger)
            for name, links in (("engine", result.links()), ("baseline", baseline(scenario.bank, scenario.ledger).links())):
                s = score(links, scenario.truth, scenario.twins)
                totals[name][0] += s["correct"]
                totals[name][1] += s["asserted"]
                totals[name][2] += s["true"]
            suggested = {(b.id, l.id) for b, l, _ in result.suggestions}
            caught += len(suggested & scenario.keying_errors)
            possible += len(scenario.keying_errors)
            found = {(b, scenario.twins.get(l, l)) for b, l in result.links()}
            for bank_id, ledger_id in scenario.truth:
                tally = by_kind.setdefault(scenario.kinds[ledger_id], [0, 0])
                tally[1] += 1
                tally[0] += (bank_id, ledger_id) in found
        row = {"difficulty": difficulty, "keying_caught": caught, "keying_total": possible, "by_kind": {k: v[0] / v[1] for k, v in sorted(by_kind.items())}}
        for name, (correct, asserted, true) in totals.items():
            row[name] = {"precision": correct / asserted if asserted else 1.0, "recall": correct / true if true else 1.0}
        rows.append(row)
    return rows
