"""Read messy bank and ledger CSV files into clean transactions.

Amounts are held as integer cents so that sums are exact.
Sign convention after loading: positive means money came in, negative means money went out.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path


class ParseError(ValueError):
    """A problem with an input file that the user can fix."""


@dataclass(frozen=True)
class Txn:
    id: str
    date: date
    cents: int
    desc: str
    ref: str = ""
    side: str = ""  # "bank" or "ledger"
    tokens: frozenset = field(default=frozenset(), compare=False)
    refs: frozenset = field(default=frozenset(), compare=False)


# Words that say how money moved, not who it moved to. They are ignored when comparing descriptions.
NOISE = frozenset(
    """NEFT IMPS RTGS UPI ACH POS TRF TRANSFER PAYMENT PAYMENTS PAID PAY RECEIPT RECEIVED FROM TO BY CR DR
    LTD LIMITED PVT PRIVATE INC LLC LLP CORP CO COMPANY THE AND OF FOR INVOICE INV BILL CHQ CHEQUE CHECK NO REF
    ONLINE BANKING DEPOSIT WITHDRAWAL DEBIT CREDIT TXN TRANSACTION""".split()
)

_REF = re.compile(r"\d{4,}")
_WORD = re.compile(r"[A-Z]{2,}")


def text_tokens(text: str) -> frozenset:
    return frozenset(w for w in _WORD.findall(text.upper()) if w not in NOISE)


def ref_tokens(text: str) -> frozenset:
    """Reference numbers: runs of four or more digits, without leading zeros."""
    return frozenset(m.lstrip("0") or "0" for m in _REF.findall(text))


def make_txn(id: str, when: date, cents: int, desc: str, ref: str = "", side: str = "") -> Txn:
    desc = " ".join(str(desc).split())
    return Txn(id, when, cents, desc, ref, side, text_tokens(desc), ref_tokens(f"{desc} {ref}"))


_AMOUNT_JUNK = re.compile(r"[^\d,.\-]")


def parse_amount(raw: str) -> int | None:
    """Parse a money string to cents. Returns None for a blank cell.

    Handles thousands separators, currency symbols and codes, parentheses or a
    trailing minus or "DR" for negatives, and European decimal commas.
    """
    text = str(raw).strip()
    if not text or text in {"-", "--"}:
        return None
    upper = text.upper()
    negative = (text.startswith("(") and text.endswith(")")) or upper.endswith("DR") or text.endswith("-") or text.startswith("-")
    digits = _AMOUNT_JUNK.sub("", text).replace("-", "")
    if not any(ch.isdigit() for ch in digits):
        raise ParseError(f"not an amount: {raw!r}")

    last_dot, last_comma = digits.rfind("."), digits.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        decimal = "." if last_dot > last_comma else ","
    elif last_comma >= 0:
        # A lone comma is a decimal mark only when exactly two digits follow it ("12,50").
        decimal = "," if len(digits) - last_comma - 1 == 2 and digits.count(",") == 1 else ""
    elif last_dot >= 0:
        # "1.234" with no other separator is ambiguous; treat three trailing digits after several dots as thousands.
        decimal = "" if digits.count(".") > 1 else "."
    else:
        decimal = ""

    if decimal:
        whole, _, fraction = digits.rpartition(decimal)
    else:
        whole, fraction = digits, ""
    whole = whole.replace(",", "").replace(".", "")
    fraction = fraction.replace(",", "").replace(".", "")
    if len(fraction) > 2:
        raise ParseError(f"more than two decimal places: {raw!r}")
    cents = int(whole or "0") * 100 + int((fraction + "00")[:2])
    return -cents if negative else cents


_DATE_FORMATS = {
    "iso": ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"],
    "dmy": ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%d %B %Y"],
    "mdy": ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y", "%b %d %Y", "%b %d, %Y", "%B %d, %Y"],
}


def _try_formats(text: str, formats: list[str]) -> date | None:
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_dates(values: list[str], dayfirst: bool | None = None) -> tuple[list[date], str]:
    """Parse a whole column of dates with one consistent format.

    Returns the dates and a note saying how an ambiguous column was read.
    """
    cleaned = [str(v).strip() for v in values]
    orders = {True: ["iso", "dmy"], False: ["iso", "mdy"], None: ["iso", "dmy", "mdy"]}[dayfirst]

    attempts = {order: [_try_formats(v, _DATE_FORMATS[order]) for v in cleaned] for order in orders}
    working = [(order, parsed) for order, parsed in attempts.items() if all(p is not None for p in parsed)]
    if not working:
        # Prefer the row nothing can read; otherwise the row that breaks the format most of the column uses.
        unreadable = [i for i in range(len(cleaned)) if all(parsed[i] is None for parsed in attempts.values())]
        if unreadable:
            index = unreadable[0]
            raise ParseError(f"could not read the date {cleaned[index]!r} (data row {index + 1})")
        best = max(attempts.values(), key=lambda parsed: sum(p is not None for p in parsed))
        index = next(i for i, p in enumerate(best) if p is None)
        raise ParseError(f"the date {cleaned[index]!r} (data row {index + 1}) is in a different format from the rest of the column")
    note = ""
    if len(working) > 1 and working[0][1] != working[1][1]:
        note = f"dates are ambiguous; read as {'day/month' if working[0][0] == 'dmy' else 'month/day'}. Use --dayfirst or --monthfirst to choose."
    return working[0][1], note


SYNONYMS = {
    "date": ["date", "txn date", "transaction date", "posting date", "value date", "posted", "entry date"],
    "amount": ["amount", "amt", "value", "net amount", "transaction amount"],
    "debit": ["debit", "withdrawal", "withdrawals", "dr", "money out", "paid out", "debit amount"],
    "credit": ["credit", "deposit", "deposits", "cr", "money in", "paid in", "credit amount"],
    "desc": ["description", "narration", "details", "particulars", "memo", "payee", "name", "transaction details"],
    "ref": ["reference", "ref", "ref no", "reference no", "cheque no", "check no", "document no", "doc no", "invoice", "voucher"],
    "id": ["id", "txn id", "transaction id", "entry id", "entry no", "line"],
}


def detect_columns(header: list[str], mapping: dict[str, str] | None = None) -> dict[str, str]:
    """Map our field names to the file's column names."""
    by_lower = {h.strip().lower(): h for h in header}
    found: dict[str, str] = {}
    for field_name, column in (mapping or {}).items():
        if column.strip().lower() not in by_lower:
            raise ParseError(f"column {column!r} not found; the file has: {', '.join(header)}")
        found[field_name] = by_lower[column.strip().lower()]
    for field_name, names in SYNONYMS.items():
        if field_name in found:
            continue
        for name in names:
            if name in by_lower:
                found[field_name] = by_lower[name]
                break
    if "date" not in found:
        raise ParseError(f"no date column found among: {', '.join(header)}")
    if "amount" not in found and not ("debit" in found or "credit" in found):
        raise ParseError(f"no amount column (or debit/credit columns) found among: {', '.join(header)}")
    return found


def load_csv(
    path: str | Path,
    side: str,
    mapping: dict[str, str] | None = None,
    dayfirst: bool | None = None,
    debit_is_inflow: bool = False,
) -> tuple[list[Txn], list[str]]:
    """Load a CSV as transactions. Returns the transactions and any notes about how the file was read.

    With separate debit and credit columns, credit counts as money in. Set
    ``debit_is_inflow`` for a cash book, where a debit is a receipt.
    """
    path = Path(path)
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            rows = [row for row in csv.DictReader(handle) if any((v or "").strip() for v in row.values())]
            header = list(rows[0].keys()) if rows else []
    except OSError as error:
        raise ParseError(f"cannot read {path}: {error}") from error
    if not rows:
        raise ParseError(f"{path.name} has no data rows")
    columns = detect_columns(header, mapping)
    notes = []

    try:
        dates, note = parse_dates([row[columns["date"]] for row in rows], dayfirst)
    except ParseError as error:
        raise ParseError(f"{path.name}: {error}") from error
    if note:
        notes.append(f"{path.name}: {note}")

    prefix = "B" if side == "bank" else "L"
    txns, skipped = [], 0
    for index, (row, when) in enumerate(zip(rows, dates), start=1):
        try:
            if "amount" in columns:
                cents = parse_amount(row[columns["amount"]])
            else:
                debit = parse_amount(row.get(columns.get("debit", ""), "") or "") or 0
                credit = parse_amount(row.get(columns.get("credit", ""), "") or "") or 0
                cents = abs(credit) - abs(debit)
                if debit_is_inflow:
                    cents = -cents
                if not debit and not credit:
                    cents = None
        except ParseError as error:
            raise ParseError(f"{path.name}, data row {index}: {error}") from error
        if cents is None:
            skipped += 1
            continue
        given_id = (row.get(columns.get("id", ""), "") or "").strip()
        txns.append(
            make_txn(
                given_id or f"{prefix}{index:05d}",
                when,
                cents,
                row.get(columns.get("desc", ""), "") or "",
                (row.get(columns.get("ref", ""), "") or "").strip(),
                side,
            )
        )
    if skipped:
        notes.append(f"{path.name}: skipped {skipped} row(s) with no amount")
    if len({t.id for t in txns}) != len(txns):
        raise ParseError(f"{path.name}: the id column has duplicate values")
    return txns, notes
