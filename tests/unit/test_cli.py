import pytest

from safe_to_save.cli import main


def test_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "offline safe-to-save baseline" in capsys.readouterr().out.lower()


@pytest.mark.parametrize(("option", "value", "message"), [
    ("--floor-minor", "-1", "non-negative"),
    ("--review-weekday", "7", "invalid choice"),
    ("--review-hour", "24", "invalid choice"),
    ("--timezone", "Unknown/Zone", "timezone"),
    ("--data-label", "public", "invalid choice"),
])
def test_invalid_backtest_options_fail_before_database_access(
    option, value, message, monkeypatch, tmp_path, capsys,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    args = ["backtest", "run", "--dataset-id", "synthetic-v2", "--review-weekday", "6",
            "--review-hour", "18", "--timezone", "Europe/London", "--floor-minor", "25000",
            "--data-label", "Synthetic demonstration", "--output", str(tmp_path)]
    args[args.index(option) + 1] = value
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
