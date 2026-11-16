"""Command-line interface for ledgermatch."""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .bench import DIFFICULTY, generate, run_benchmark
from .match import Config, detect_flipped_signs, reconcile
from .parse import ParseError, load_csv
from .report import money, write


def _mapping(pairs: list[str] | None) -> dict[str, str]:
    mapping = {}
    for pair in pairs or []:
        name, sep, column = pair.partition("=")
        if not sep or name not in {"date", "amount", "debit", "credit", "desc", "ref", "id"}:
            raise ParseError(f"bad column mapping {pair!r}; use field=Column with field one of date, amount, debit, credit, desc, ref, id")
        mapping[name] = column
    return mapping


def cmd_reconcile(args) -> int:
    dayfirst = True if args.dayfirst else False if args.monthfirst else None
    bank, bank_notes = load_csv(args.bank, "bank", _mapping(args.bank_col), dayfirst)
    ledger, ledger_notes = load_csv(args.ledger, "ledger", _mapping(args.ledger_col), dayfirst, debit_is_inflow=True)
    notes = bank_notes + ledger_notes

    flip = args.flip_ledger
    if not flip and not args.no_auto_flip and detect_flipped_signs(bank, ledger):
        flip = True
        notes.append("the ledger's signs looked opposite to the bank's, so they were flipped. Use --no-auto-flip to stop this.")
    if flip:
        ledger = [replace(t, cents=-t.cents) for t in ledger]

    cfg = Config(early_days=args.early_days, late_days=args.late_days)
    result = reconcile(bank, ledger, cfg)
    summary = write(args.out, bank, ledger, result, notes)

    print(f"Bank lines:     {summary['bank_lines']:>6}   matched {summary['matched_bank']}, unmatched {len(result.unmatched_bank)}")
    print(f"Ledger entries: {summary['ledger_entries']:>6}   matched {summary['matched_ledger']}, unmatched {len(result.unmatched_ledger)}")
    for kind, count in sorted(summary["by_kind"].items()):
        print(f"  {kind:<11} {count}")
    if summary["ambiguous"]:
        print(f"  {summary['ambiguous']} match(es) flagged ambiguous")
    if result.suggestions:
        print(f"  {len(result.suggestions)} suggested correction(s) for likely keying errors")
    print(f"Difference between bank and ledger: {money(summary['difference'])}")
    print(f"Explained by the unmatched items and fees: {money(summary['explained'])}  ({'balanced' if summary['balanced'] else 'NOT BALANCED'})")
    for note in notes + result.notes:
        print(f"note: {note}")
    print(f"Report written to {Path(args.out) / 'report.md'}")
    return 0 if summary["balanced"] else 3


def cmd_demo(args) -> int:
    scenario = generate(args.seed, args.entries, args.difficulty)
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    # Written in two deliberately different, messy layouts to exercise the parser.
    with open(out / "bank.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Txn ID", "Value Date", "Narration", "Withdrawal", "Deposit"])
        for t in sorted(scenario.bank, key=lambda t: (t.date, t.id)):
            amount = f"{abs(t.cents) / 100:,.2f}"
            writer.writerow([t.id, t.date.strftime("%d/%m/%Y"), t.desc, amount if t.cents < 0 else "", amount if t.cents > 0 else ""])
    with open(out / "ledger.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Entry No", "Date", "Particulars", "Doc No", "Amount"])
        for t in sorted(scenario.ledger, key=lambda t: (t.date, t.id)):
            amount = f"{abs(t.cents) / 100:,.2f}"
            writer.writerow([t.id, t.date.isoformat(), t.desc, t.ref, f"({amount})" if t.cents < 0 else f"INR {amount}"])
    with open(out / "truth.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bank_id", "ledger_id"])
        writer.writerows(sorted(scenario.truth))
    print(f"Wrote bank.csv ({len(scenario.bank)} lines), ledger.csv ({len(scenario.ledger)} entries) and truth.csv to {out}")
    return 0


def cmd_bench(args) -> int:
    rows = run_benchmark(range(args.first_seed, args.first_seed + args.seeds), args.entries)
    print(f"{args.seeds} generated months per difficulty, about {args.entries} ledger entries each\n")
    print(f"{'difficulty':<11}{'engine precision':>17}{'engine recall':>15}{'lookup precision':>18}{'lookup recall':>15}{'keying errors caught':>23}")
    for row in rows:
        e, b = row["engine"], row["baseline"]
        print(
            f"{row['difficulty']:<11}{e['precision']:>17.1%}{e['recall']:>15.1%}{b['precision']:>18.1%}{b['recall']:>15.1%}"
            f"{str(row['keying_caught']) + ' of ' + str(row['keying_total']):>23}"
        )
    print("\nShare of true links found by the engine, by situation:")
    kinds = sorted({k for row in rows for k in row["by_kind"]})
    print(f"{'difficulty':<11}" + "".join(f"{k:>13}" for k in kinds))
    for row in rows:
        print(f"{row['difficulty']:<11}" + "".join(f"{row['by_kind'].get(k, 0):>13.1%}" for k in kinds))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledgermatch", description="Reconcile a bank statement against a ledger and explain every difference.")
    parser.add_argument("--version", action="version", version=f"ledgermatch {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("reconcile", help="match a bank statement CSV against a ledger CSV")
    rec.add_argument("bank", help="bank statement CSV")
    rec.add_argument("ledger", help="ledger or cash book CSV")
    rec.add_argument("-o", "--out", default="reconciliation", help="folder for the report and CSVs (default: reconciliation)")
    rec.add_argument("--bank-col", action="append", metavar="FIELD=COLUMN", help="name a bank column, e.g. --bank-col date='Posted On'")
    rec.add_argument("--ledger-col", action="append", metavar="FIELD=COLUMN", help="name a ledger column")
    order = rec.add_mutually_exclusive_group()
    order.add_argument("--dayfirst", action="store_true", help="read 03/04/2026 as 3 April")
    order.add_argument("--monthfirst", action="store_true", help="read 03/04/2026 as 4 March")
    rec.add_argument("--flip-ledger", action="store_true", help="reverse the sign of every ledger amount")
    rec.add_argument("--no-auto-flip", action="store_true", help="do not flip ledger signs automatically")
    rec.add_argument("--early-days", type=int, default=3, help="bank line may be this many days before the ledger entry (default: 3)")
    rec.add_argument("--late-days", type=int, default=10, help="bank line may be this many days after the ledger entry (default: 10)")
    rec.set_defaults(func=cmd_reconcile)

    demo = sub.add_parser("demo", help="write a generated month of bank and ledger data to try the tool on")
    demo.add_argument("dir")
    demo.add_argument("--seed", type=int, default=1)
    demo.add_argument("--entries", type=int, default=400)
    demo.add_argument("--difficulty", choices=list(DIFFICULTY), default="medium")
    demo.set_defaults(func=cmd_demo)

    bench = sub.add_parser("bench", help="measure accuracy on generated data with known answers")
    bench.add_argument("--seeds", type=int, default=10)
    bench.add_argument("--entries", type=int, default=400)
    bench.add_argument("--first-seed", type=int, default=1, help="seed of the first generated month (default: 1)")
    bench.set_defaults(func=cmd_bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ParseError as error:
        print(f"ledgermatch: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
