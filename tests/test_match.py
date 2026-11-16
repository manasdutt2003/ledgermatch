from datetime import date

import pytest

from ledgermatch.bench import baseline, generate, run_benchmark, score
from ledgermatch.cli import main
from ledgermatch.match import Config, _subsets, detect_flipped_signs, reconcile
from ledgermatch.parse import make_txn
from ledgermatch.report import summarise


def bank(id, day, amount, desc=""):
    return make_txn(id, date(2026, 10, day), round(amount * 100), desc, "", "bank")


def ledger(id, day, amount, desc="", ref=""):
    return make_txn(id, date(2026, 10, day), round(amount * 100), desc, ref, "ledger")


def links(result):
    return sorted(result.links())


def test_repeated_amounts_are_paired_by_who_they_are_from():
    # Three customers pay the same 499.00. Dates alone would pair them wrongly.
    b = [bank("B1", 5, 499, "NEFT CR CEDAR FOODS 111"), bank("B2", 5, 499, "UPI/APEX MOTORS 222"), bank("B3", 6, 499, "IMPS/LOTUS PAPERS 333")]
    l = [ledger("L1", 4, 499, "Receipt from Apex Motors Pvt Ltd"), ledger("L2", 5, 499, "Receipt from Lotus Papers Pvt Ltd"), ledger("L3", 5, 499, "Receipt from Cedar Foods Pvt Ltd")]
    assert links(reconcile(b, l)) == [("B1", "L3"), ("B2", "L1"), ("B3", "L2")]
    assert sorted(baseline(b, l).links()) != [("B1", "L3"), ("B2", "L1"), ("B3", "L2")]


def test_a_shared_reference_beats_a_closer_date():
    b = [bank("B1", 9, 750, "ACH CR 20455")]
    l = [ledger("L1", 9, 750, "Receipt from Someone"), ledger("L2", 3, 750, "Receipt from Other", "INV-20455")]
    assert links(reconcile(b, l)) == [("B1", "L2")]


def test_truncated_bank_names_still_match():
    b = [bank("B1", 3, 120, "NEFT CR EVERGREEN PHAR 9912"), bank("B2", 3, 120, "NEFT CR MERIDIAN TEXT 8841")]
    l = [ledger("L1", 2, 120, "Receipt from Meridian Textiles Pvt Ltd"), ledger("L2", 2, 120, "Receipt from Evergreen Pharma Pvt Ltd")]
    assert links(reconcile(b, l)) == [("B1", "L2"), ("B2", "L1")]


def test_dates_outside_the_window_do_not_match():
    result = reconcile([bank("B1", 28, 100, "X")], [ledger("L1", 1, 100, "X")])
    assert result.matches == [] and len(result.unmatched_bank) == len(result.unmatched_ledger) == 1


def test_batch_deposit_is_matched_to_the_receipts_inside_it():
    b = [bank("B1", 6, 1750.50, "CASH DEPOSIT SLIP 88121")]
    l = [ledger("L1", 5, 1000, "Receipt from A"), ledger("L2", 5, 500.50, "Receipt from B"), ledger("L3", 5, 250, "Receipt from C"), ledger("L4", 5, 333, "Receipt from D")]
    result = reconcile(b, l)
    assert links(result) == [("B1", "L1"), ("B1", "L2"), ("B1", "L3")]
    assert result.matches[0].kind == "batch"
    assert [t.id for t, _ in result.unmatched_ledger] == ["L4"]


def test_split_payment_is_matched_to_its_instalments():
    b = [bank("B1", 5, -400, "TRF TO ZENITH BUILDERS"), bank("B2", 6, -350, "TRF TO ZENITH BUILDERS"), bank("B3", 6, -90, "ATM CASH WITHDRAWAL")]
    l = [ledger("L1", 5, -750, "Payment to Zenith Builders Pvt Ltd")]
    result = reconcile(b, l)
    assert links(result) == [("B1", "L1"), ("B2", "L1")] and result.matches[0].kind == "split"


def test_an_ambiguous_group_is_reported_not_guessed():
    # 300 can be made as 100+200 or 150+150, on the same day, with nothing to tell them apart.
    b = [bank("B1", 6, 300, "CASH DEPOSIT")]
    l = [ledger("L1", 5, 100, "Receipt from A"), ledger("L2", 5, 200, "Receipt from B"), ledger("L3", 5, 150, "Receipt from C"), ledger("L4", 5, 150, "Receipt from D")]
    result = reconcile(b, l)
    assert result.matches == []
    assert any("B1" in note and "left unmatched for review" in note for note in result.notes)


def test_solving_one_group_disambiguates_another():
    # B2 has two possible make-ups until B1's only solution removes L2 from the pool.
    b = [bank("B1", 6, 900, "CASH DEPOSIT"), bank("B2", 6, 300, "CASH DEPOSIT")]
    l = [ledger("L1", 5, 700, "Receipt from A"), ledger("L2", 5, 200, "Receipt from B"), ledger("L3", 5, 100, "Receipt from C"), ledger("L4", 5, 150, "Receipt from D"), ledger("L5", 5, 150, "Receipt from E")]
    result = reconcile(b, l)
    assert links(result) == [("B1", "L1"), ("B1", "L2"), ("B2", "L4"), ("B2", "L5")]


def test_processor_fee_is_matched_and_the_fee_reported():
    b = [bank("B1", 7, 970.70, "PAYOUT HARBOR SYSTEMS 5512")]
    l = [ledger("L1", 5, 1000, "Receipt from Harbor Systems Pvt Ltd")]
    result = reconcile(b, l)
    assert result.matches[0].kind == "fee" and result.matches[0].diff_cents == -2930


def test_a_close_amount_alone_is_not_treated_as_a_fee():
    result = reconcile([bank("B1", 7, 970.70, "PAYOUT SOMEBODY ELSE")], [ledger("L1", 5, 1000, "Receipt from Harbor Systems Pvt Ltd")])
    assert result.matches == []


def test_keying_error_is_suggested_but_never_counted_as_matched():
    b = [bank("B1", 8, 4997.07, "UPI/PIONEER LOGISTICS INV20284")]
    l = [ledger("L1", 8, 4979.07, "Receipt from Pioneer Logistics Pvt Ltd", "INV-20284")]
    result = reconcile(b, l)
    assert result.matches == []
    assert [(x.id, y.id) for x, y, _ in result.suggestions] == [("B1", "L1")]
    assert "two adjacent digits swapped" in result.suggestions[0][2]
    assert "see suggestions" in result.unmatched_bank[0][1]


def test_leftovers_are_given_a_likely_reason():
    b = [bank("B1", 3, 100, "NEFT CR APEX"), bank("B2", 15, -25, "MONTHLY SERVICE CHARGE"), bank("B3", 28, -10, "ATM CASH WITHDRAWAL")]
    l = [ledger("L1", 3, 100, "Receipt from Apex"), ledger("L2", 3, 100, "Receipt from Apex"), ledger("L3", 27, -800, "Payment to Vendor"), ledger("L4", 2, -60, "Payment to Old")]
    result = reconcile(b, l)
    reasons = {t.id: why for t, why in result.unmatched_bank + result.unmatched_ledger}
    assert "bank charge" in reasons["B2"]
    assert reasons["B3"] == "on the statement but not in the ledger"
    assert "possible duplicate" in next(why for id_, why in reasons.items() if id_ in {"L1", "L2"})
    assert "not cleared" in reasons["L3"]
    assert reasons["L4"] == "in the ledger but not on the statement"


def test_subsets_respects_size_and_finds_every_combination():
    items = [ledger(f"L{i}", 1, amount) for i, amount in enumerate([5, 4, 3, 2, 1])]
    groups, exhaustive = _subsets(600, items, max_size=3)
    assert {tuple(sorted(t.cents // 100 for t in group)) for group in groups} == {(1, 5), (2, 4), (1, 2, 3)}
    assert exhaustive
    assert not _subsets(600, items, max_size=3, limit=2)[1]


def test_flipped_ledger_signs_are_detected():
    b = [bank(f"B{i}", 1, amount) for i, amount in enumerate([10, 20, -30, 40, -50, 60])]
    flipped = [ledger(f"L{i}", 1, -amount) for i, amount in enumerate([10, 20, -30, 40, -50, 60])]
    assert detect_flipped_signs(b, flipped)
    assert not detect_flipped_signs(b, [ledger(f"L{i}", 1, t.cents / 100) for i, t in enumerate(b)])


@pytest.mark.parametrize("seed", range(200, 212))
@pytest.mark.parametrize("difficulty", ["easy", "hard"])
def test_every_cent_of_the_difference_is_always_explained(seed, difficulty):
    scenario = generate(seed, 150, difficulty)
    result = reconcile(scenario.bank, scenario.ledger)
    summary = summarise(scenario.bank, scenario.ledger, result)
    assert summary["balanced"]
    # Nothing is matched twice, and nothing is both matched and unmatched.
    used = [t.id for m in result.matches for t in m.bank + m.ledger]
    left = [t.id for t, _ in result.unmatched_bank + result.unmatched_ledger]
    assert len(used) == len(set(used)) and not set(used) & set(left)
    assert len(used) + len(left) == len(scenario.bank) + len(scenario.ledger)
    # Only fee matches may differ in amount.
    assert all(m.diff_cents == 0 for m in result.matches if m.kind != "fee")


def test_accuracy_does_not_regress():
    medium = next(row for row in run_benchmark(range(300, 304), 300) if row["difficulty"] == "medium")
    assert medium["engine"]["precision"] >= 0.96
    assert medium["engine"]["recall"] >= 0.88
    assert medium["engine"]["recall"] > medium["baseline"]["recall"] + 0.3


def test_score_treats_identical_duplicates_as_interchangeable():
    truth = {("B1", "L1")}
    assert score({("B1", "L2")}, truth)["precision"] == 0.0
    assert score({("B1", "L2")}, truth, {"L2": "L1"})["precision"] == 1.0


def test_cli_demo_then_reconcile(tmp_path, capsys):
    assert main(["demo", str(tmp_path), "--seed", "3", "--entries", "80"]) == 0
    out = tmp_path / "out"
    assert main(["reconcile", str(tmp_path / "bank.csv"), str(tmp_path / "ledger.csv"), "-o", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "(balanced)" in printed
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "Every cent of the difference is accounted for" in report
    assert (out / "matches.csv").exists() and (out / "unmatched_bank.csv").exists() and (out / "unmatched_ledger.csv").exists()


def test_cli_reports_a_bad_file_without_a_traceback(tmp_path, capsys):
    (tmp_path / "bank.csv").write_text("Narration,Amount\nx,1\n")
    (tmp_path / "ledger.csv").write_text("Date,Amount\n2026-10-01,1\n")
    assert main(["reconcile", str(tmp_path / "bank.csv"), str(tmp_path / "ledger.csv"), "-o", str(tmp_path / "o")]) == 2
    assert "no date column" in capsys.readouterr().err
