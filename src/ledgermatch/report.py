"""Turn a reconciliation result into files a person can check."""

from __future__ import annotations

import csv
from pathlib import Path

from .match import Result
from .parse import Txn

KIND_LABELS = {
    "one_to_one": "One bank line to one ledger entry",
    "batch": "One bank line to several ledger entries",
    "split": "Several bank lines to one ledger entry",
    "fee": "Matched net of a processing fee",
}


def money(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def summarise(bank: list[Txn], ledger: list[Txn], result: Result) -> dict:
    """Totals, plus the proof that every cent of the difference is accounted for."""
    bank_total = sum(t.cents for t in bank)
    ledger_total = sum(t.cents for t in ledger)
    bank_only = sum(t.cents for t, _ in result.unmatched_bank)
    ledger_only = sum(t.cents for t, _ in result.unmatched_ledger)
    fees = sum(m.diff_cents for m in result.matches)
    matched_bank = sum(len(m.bank) for m in result.matches)
    matched_ledger = sum(len(m.ledger) for m in result.matches)
    by_kind: dict[str, int] = {}
    for m in result.matches:
        by_kind[m.kind] = by_kind.get(m.kind, 0) + 1
    difference = bank_total - ledger_total
    explained = bank_only - ledger_only + fees
    return {
        "bank_lines": len(bank),
        "ledger_entries": len(ledger),
        "matched_bank": matched_bank,
        "matched_ledger": matched_ledger,
        "by_kind": by_kind,
        "ambiguous": sum(1 for m in result.matches if m.ambiguous),
        "bank_total": bank_total,
        "ledger_total": ledger_total,
        "difference": difference,
        "bank_only": bank_only,
        "ledger_only": ledger_only,
        "fees": fees,
        "explained": explained,
        "balanced": difference == explained,
    }


def render(summary: dict, result: Result, notes: list[str]) -> str:
    s = summary
    lines = [
        "# Bank reconciliation",
        "",
        f"- Bank lines: {s['bank_lines']}, of which {s['matched_bank']} matched and {len(result.unmatched_bank)} unmatched",
        f"- Ledger entries: {s['ledger_entries']}, of which {s['matched_ledger']} matched and {len(result.unmatched_ledger)} unmatched",
        f"- Matches flagged as ambiguous (another candidate scored almost as well): {s['ambiguous']}",
        "",
        "## Matches by type",
        "",
        "| Type | Matches |",
        "| --- | ---: |",
        *(f"| {KIND_LABELS[kind]} | {count} |" for kind, count in sorted(s["by_kind"].items())),
        "",
        "## Reconciliation statement",
        "",
        "| | Amount |",
        "| --- | ---: |",
        f"| Net movement per bank statement | {money(s['bank_total'])} |",
        f"| Net movement per ledger | {money(s['ledger_total'])} |",
        f"| **Difference to explain** | **{money(s['difference'])}** |",
        f"| Bank lines not in the ledger | {money(s['bank_only'])} |",
        f"| Less: ledger entries not on the statement | {money(-s['ledger_only'])} |",
        f"| Processing fees on matched receipts | {money(s['fees'])} |",
        f"| **Difference explained** | **{money(s['explained'])}** |",
        "",
        "Every cent of the difference is accounted for by the items listed below."
        if s["balanced"]
        else "WARNING: the explained amount does not equal the difference. This is a bug; please report it.",
    ]

    def section(title: str, rows: list[tuple[Txn, str]]) -> None:
        lines.extend(["", f"## {title} ({len(rows)})", ""])
        if not rows:
            lines.append("None.")
            return
        lines.extend(["| ID | Date | Amount | Description | Likely reason |", "| --- | --- | ---: | --- | --- |"])
        for t, reason in sorted(rows, key=lambda row: (row[0].date, row[0].id)):
            lines.append(f"| {t.id} | {t.date} | {money(t.cents)} | {t.desc.replace('|', '/')} | {reason} |")

    section("Bank lines not matched", result.unmatched_bank)
    section("Ledger entries not matched", result.unmatched_ledger)

    lines.extend(["", f"## Suggested corrections ({len(result.suggestions)})", ""])
    if result.suggestions:
        lines.append("These are not counted as matched. Each needs a person to confirm and correct the entry.")
        lines.append("")
        for b, l, why in result.suggestions:
            lines.append(f"- {b.id} and {l.id}: {why}")
    else:
        lines.append("None.")

    all_notes = notes + result.notes
    if all_notes:
        lines.extend(["", "## Notes", "", *(f"- {note}" for note in all_notes)])
    return "\n".join(lines) + "\n"


def write(out_dir: str | Path, bank: list[Txn], ledger: list[Txn], result: Result, notes: list[str]) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = summarise(bank, ledger, result)

    with open(out / "matches.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["match", "type", "ambiguous", "score", "reason", "side", "id", "date", "amount", "description", "reference"])
        for number, m in enumerate(result.matches, start=1):
            for t in m.bank + m.ledger:
                writer.writerow([number, m.kind, "yes" if m.ambiguous else "", f"{m.score:.2f}", m.reason, t.side, t.id, t.date, f"{t.cents / 100:.2f}", t.desc, t.ref])

    for name, rows in (("unmatched_bank.csv", result.unmatched_bank), ("unmatched_ledger.csv", result.unmatched_ledger)):
        with open(out / name, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "date", "amount", "description", "reference", "likely_reason"])
            for t, reason in sorted(rows, key=lambda row: (row[0].date, row[0].id)):
                writer.writerow([t.id, t.date, f"{t.cents / 100:.2f}", t.desc, t.ref, reason])

    (out / "report.md").write_text(render(summary, result, notes), encoding="utf-8")
    return summary
