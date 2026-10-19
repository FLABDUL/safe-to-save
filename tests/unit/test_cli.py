import pytest

from safe_to_save.cli import main
from safe_to_save.gate_assess import GateDecision
from safe_to_save.gate_evaluate import GateEvaluationResult
from safe_to_save.gate_prepare import PreparationResult


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


@pytest.mark.parametrize("action", ("prepare", "evaluate", "assess"))
def test_gate_subcommands_require_private_config(action):
    with pytest.raises(SystemExit) as exc:
        main(["gate-a", action])
    assert exc.value.code == 2


def test_prepare_does_not_require_database_environment(monkeypatch, tmp_path, capsys):
    config = tmp_path / "gate.json"
    config.write_text("{}", encoding="utf-8")
    sentinel = object()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("safe_to_save.cli.load_gate_config", lambda *_: sentinel)
    monkeypatch.setattr(
        "safe_to_save.cli.prepare_private_history",
        lambda value: PreparationResult("completed") if value is sentinel else None,
    )

    assert main(["gate-a", "prepare", "--config", str(config)]) == 0
    assert capsys.readouterr().out == "gate-a prepare: status=completed.\n"


def test_evaluate_requires_isolated_database(monkeypatch, tmp_path, capsys):
    config = tmp_path / "gate.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("safe_to_save.cli.load_gate_config", lambda *_: object())
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/not-isolated")

    assert main(["gate-a", "evaluate", "--config", str(config)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "gate-a evaluate: status=failed; error=ValueError; no report was published.\n"
    )


def test_prepare_integrity_failure_prints_fix_data_or_policy(monkeypatch, tmp_path, capsys):
    config = tmp_path / "gate.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("safe_to_save.cli.load_gate_config", lambda *_: object())
    monkeypatch.setattr(
        "safe_to_save.cli.prepare_private_history",
        lambda _: PreparationResult("fix_data_or_policy", "private-row-value"),
    )

    assert main(["gate-a", "prepare", "--config", str(config)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "gate-a prepare: status=fix_data_or_policy; error=PreparationRejected; "
        "no report was published.\n"
    )
    assert "private-row-value" not in captured.err


@pytest.mark.parametrize(
    ("decision", "exit_code"),
    ((GateDecision.COLLECT_MORE_EVIDENCE, 0), (GateDecision.PROCEED_TO_MODELLING, 0)),
)
def test_assess_readiness_decisions_have_stable_success_exit(
    decision, exit_code, monkeypatch, tmp_path, capsys,
):
    config_path = tmp_path / "gate.json"
    config_path.write_text("{}", encoding="utf-8")
    config = type("Config", (), {
        "canonical_directory": tmp_path / "canonical",
        "output_directory": tmp_path / "output",
    })()
    assessment = type("Assessment", (), {"decision": decision})()
    report_targets = []
    monkeypatch.setattr("safe_to_save.cli.load_gate_config", lambda *_: config)
    monkeypatch.setattr("safe_to_save.cli.load_history", lambda *_: object())
    monkeypatch.setattr("safe_to_save.cli.load_gate_evidence", lambda *_: object())
    monkeypatch.setattr("safe_to_save.cli.assess_gate", lambda *_: assessment)
    monkeypatch.setattr(
        "safe_to_save.cli.write_gate_report",
        lambda target, _: report_targets.append(target) or target,
    )

    assert main(["gate-a", "assess", "--config", str(config_path)]) == exit_code
    assert capsys.readouterr().out == (
        f"gate-a assess: status={decision.value}; "
        "files=gate-a-report.json,gate-a-report.md.\n"
    )
    assert report_targets == [config.output_directory / "assessment"]


def test_assess_fix_decision_reports_published_aggregate(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "gate.json"
    config_path.write_text("{}", encoding="utf-8")
    config = type("Config", (), {
        "canonical_directory": tmp_path / "canonical",
        "output_directory": tmp_path / "output",
    })()
    assessment = type("Assessment", (), {"decision": GateDecision.FIX_DATA_OR_POLICY})()
    monkeypatch.setattr("safe_to_save.cli.load_gate_config", lambda *_: config)
    monkeypatch.setattr("safe_to_save.cli.load_history", lambda *_: object())
    monkeypatch.setattr("safe_to_save.cli.load_gate_evidence", lambda *_: object())
    monkeypatch.setattr("safe_to_save.cli.assess_gate", lambda *_: assessment)
    monkeypatch.setattr("safe_to_save.cli.write_gate_report", lambda *_: config.output_directory)

    assert main(["gate-a", "assess", "--config", str(config_path)]) == 1
    assert capsys.readouterr().err == (
        "gate-a assess: status=fix_data_or_policy; error=AssessmentRejected; "
        "aggregate report was published.\n"
    )


def test_assess_writer_failure_does_not_claim_nothing_was_published(
    monkeypatch, tmp_path, capsys,
):
    config_path = tmp_path / "gate.json"
    config_path.write_text("{}", encoding="utf-8")
    config = type("Config", (), {
        "canonical_directory": tmp_path / "canonical",
        "output_directory": tmp_path / "output",
    })()
    assessment = type("Assessment", (), {"decision": GateDecision.PROCEED_TO_MODELLING})()
    monkeypatch.setattr("safe_to_save.cli.load_gate_config", lambda *_: config)
    monkeypatch.setattr("safe_to_save.cli.load_history", lambda *_: object())
    monkeypatch.setattr("safe_to_save.cli.load_gate_evidence", lambda *_: object())
    monkeypatch.setattr("safe_to_save.cli.assess_gate", lambda *_: assessment)
    monkeypatch.setattr(
        "safe_to_save.cli.write_gate_report",
        lambda *_: (_ for _ in ()).throw(ValueError("private partial-write detail")),
    )

    assert main(["gate-a", "assess", "--config", str(config_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "gate-a assess: status=failed; error=ValueError; "
        "report publication was not confirmed.\n"
    )
    assert "private partial-write detail" not in captured.err


def test_gate_failure_output_excludes_row_values_paths_credentials_and_floors(
    monkeypatch, tmp_path, capsys,
):
    config = tmp_path / "private-secret" / "gate.json"
    secrets = (str(config), "merchant-secret", "postgres-secret", "25000")
    monkeypatch.setattr(
        "safe_to_save.cli.load_gate_config",
        lambda *_: (_ for _ in ()).throw(ValueError(" ".join(secrets))),
    )

    assert main(["gate-a", "assess", "--config", str(config)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "gate-a assess: status=failed; error=ValueError; "
        "report publication was not confirmed.\n"
    )
    assert all(secret not in captured.err for secret in secrets)


def test_evaluate_success_prints_only_aggregate_scenario_count(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "gate.json"
    config_path.write_text("{}", encoding="utf-8")
    config = type("Config", (), {
        "output_directory": tmp_path / "output",
        "model_copy": lambda self, *, update: type("EvaluationConfig", (), update)(),
    })()
    evaluation_configs = []
    monkeypatch.setattr("safe_to_save.cli.load_gate_config", lambda *_: config)
    monkeypatch.setattr("safe_to_save.cli.Settings.from_environment", lambda: type(
        "SettingsValue", (), {"database_url": "postgresql://ignored"},
    )())
    monkeypatch.setattr("safe_to_save.cli.HistoryRepository", lambda *_: object())
    monkeypatch.setattr(
        "safe_to_save.cli.evaluate_gate",
        lambda stage_config, _: evaluation_configs.append(stage_config) or GateEvaluationResult(
            "completed", scenarios=(object(), object(), object()),
        ),
    )

    assert main(["gate-a", "evaluate", "--config", str(config_path)]) == 0
    assert capsys.readouterr().out == "gate-a evaluate: status=completed; scenarios=3.\n"
    assert evaluation_configs[0].output_directory == config.output_directory / "evaluation"
