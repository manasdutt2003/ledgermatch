from datetime import date

import pytest

from ledgermatch.parse import ParseError, load_csv, parse_amount, parse_dates, ref_tokens, text_tokens


@pytest.mark.parametrize(
    "raw,cents",
    [
        ("1234.50", 123450),
        ("1,234.50", 123450),
        ("$1,234.50", 123450),
        ("INR 200", 20000),
        ("₹ 1,00,000.00", 10000000),  # Indian digit grouping
        ("(120.00)", -12000),
        ("-45.5", -4550),
        ("45.50-", -4550),
        ("300.00 DR", -30000),
        ("300.00 CR", 30000),
        ("1.234,50", 123450),  # European
        ("12,50", 1250),
        ("1,234", 123400),
        ("7", 700),
        ("0.07", 7),
    ],
)
def test_parse_amount(raw, cents):
    assert parse_amount(raw) == cents


@pytest.mark.parametrize("raw", ["", "  ", "-"])
def test_blank_amount_is_none(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize("raw", ["abc", "12.345"])
def test_bad_amount_is_rejected(raw):
    with pytest.raises(ParseError):
        parse_amount(raw)


def test_dates_use_one_format_for_the_whole_column():
    # 13/10 can only be day/month, which settles the ambiguous 03/04 as well.
    dates, note = parse_dates(["03/04/2026", "13/10/2026"])
    assert dates == [date(2026, 4, 3), date(2026, 10, 13)]
    assert note == ""


def test_ambiguous_dates_are_flagged_and_can_be_forced():
    dates, note = parse_dates(["03/04/2026"])
    assert dates == [date(2026, 4, 3)] and "ambiguous" in note
    assert parse_dates(["03/04/2026"], dayfirst=False)[0] == [date(2026, 3, 4)]


def test_unreadable_date_names_the_row():
    with pytest.raises(ParseError, match="data row 2"):
        parse_dates(["2026-10-01", "sometime"])


def test_tokens_ignore_payment_jargon_and_keep_references():
    assert text_tokens("NEFT CR ACME TRADERS PVT LTD 00123456") == {"ACME", "TRADERS"}
    assert ref_tokens("INV-0020187 paid, chq 123") == {"20187"}


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_load_bank_with_debit_and_credit_columns(tmp_path):
    path = write(tmp_path / "bank.csv", "Value Date,Narration,Withdrawal,Deposit\n01/10/2026,RENT,\"1,500.00\",\n15/10/2026,SALE,,250.00\n")
    txns, notes = load_csv(path, "bank")
    assert [(t.id, t.date, t.cents, t.desc) for t in txns] == [
        ("B00001", date(2026, 10, 1), -150000, "RENT"),
        ("B00002", date(2026, 10, 15), 25000, "SALE"),
    ]
    assert notes == []


def test_load_ledger_with_single_amount_column_and_ids(tmp_path):
    path = write(tmp_path / "ledger.csv", "Entry No,Date,Particulars,Doc No,Amount\nL7,2026-10-01,Rent,CHQ-1001,(1500.00)\n,,,,\nL8,2026-10-02,Sale,INV-2002,INR 250\n")
    txns, _ = load_csv(path, "ledger")
    assert [(t.id, t.cents, t.ref) for t in txns] == [("L7", -150000, "CHQ-1001"), ("L8", 25000, "INV-2002")]
    assert txns[0].refs == {"1001"}


def test_cash_book_debit_is_a_receipt(tmp_path):
    path = write(tmp_path / "ledger.csv", "Date,Details,Debit,Credit\n2026-10-01,Sale,250.00,\n")
    txns, _ = load_csv(path, "ledger", debit_is_inflow=True)
    assert txns[0].cents == 25000


def test_explicit_column_mapping(tmp_path):
    path = write(tmp_path / "bank.csv", "Posted On,What,How Much\n2026-10-01,Sale,10\n")
    txns, _ = load_csv(path, "bank", {"date": "Posted On", "desc": "What", "amount": "How Much"})
    assert txns[0].cents == 1000


@pytest.mark.parametrize(
    "text,message",
    [
        ("Narration,Amount\nx,1\n", "no date column"),
        ("Date,Narration\n2026-10-01,x\n", "no amount column"),
        ("Date,Amount\n", "no data rows"),
        ("Date,Amount\n2026-10-01,oops\n", "data row 1"),
    ],
)
def test_bad_files_get_a_clear_error(tmp_path, text, message):
    with pytest.raises(ParseError, match=message):
        load_csv(write(tmp_path / "f.csv", text), "bank")
