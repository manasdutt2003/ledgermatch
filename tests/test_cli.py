import pytest

from ledgermatch.cli import main


@pytest.fixture
def demo_dir(tmp_path):
    assert main(["demo", str(tmp_path), "--seed", "3", "--entries", "60"]) == 0
    return tmp_path


def reconcile_args(folder, out):
    return ["reconcile", str(folder / "bank.csv"), str(folder / "ledger.csv"), "-o", str(out)]


def test_env_var_sets_the_output_folder(demo_dir, monkeypatch):
    out = demo_dir / "from_env"
    monkeypatch.setenv("LEDGERMATCH_OUT", str(out))
    assert main(["reconcile", str(demo_dir / "bank.csv"), str(demo_dir / "ledger.csv")]) == 0
    assert (out / "report.md").exists()


def test_non_numeric_env_value_is_a_clean_error(demo_dir, monkeypatch, capsys):
    monkeypatch.setenv("LEDGERMATCH_LATE_DAYS", "abc")
    assert main(reconcile_args(demo_dir, demo_dir / "o")) == 2
    assert "LEDGERMATCH_LATE_DAYS" in capsys.readouterr().err


def test_negative_day_window_is_rejected(demo_dir, capsys):
    assert main(reconcile_args(demo_dir, demo_dir / "o") + ["--early-days", "-1"]) == 2
    assert "cannot be negative" in capsys.readouterr().err


def test_demo_and_bench_reject_non_positive_sizes(tmp_path, capsys):
    assert main(["demo", str(tmp_path), "--entries", "0"]) == 2
    assert main(["bench", "--seeds", "0"]) == 2
    assert capsys.readouterr().err.count("must be at least 1") == 2


def test_verbose_logs_to_stderr_and_leaves_stdout_alone(demo_dir, capsys):
    assert main(["-v"] + reconcile_args(demo_dir, demo_dir / "o")) == 0
    captured = capsys.readouterr()
    assert "loaded" in captured.err
    assert "loaded" not in captured.out


def test_quiet_by_default(demo_dir, capsys):
    assert main(reconcile_args(demo_dir, demo_dir / "o")) == 0
    assert capsys.readouterr().err == ""


def test_bad_log_level_is_a_clean_error(demo_dir, monkeypatch, capsys):
    monkeypatch.setenv("LEDGERMATCH_LOG_LEVEL", "LOUD")
    assert main(reconcile_args(demo_dir, demo_dir / "o")) == 2
    assert "LEDGERMATCH_LOG_LEVEL" in capsys.readouterr().err
