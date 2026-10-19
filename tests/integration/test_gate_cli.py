import json
import os
import shutil
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from safe_to_save.cli import main
from safe_to_save.db import connect


@pytest.mark.integration
def test_synthetic_gate_a_cli_is_private_reproducible_and_decision_is_pinned(
    repository, capsys,
):
    project = Path(__file__).parents[2]
    source = project / "tests" / "fixtures" / "gate_a_synthetic"
    private = project / "data" / "private" / "gate-a-synthetic-cli-test"
    output = project / "outputs" / "private" / "gate-a-synthetic-cli-test"
    shutil.rmtree(private, ignore_errors=True)
    shutil.rmtree(output, ignore_errors=True)
    private.mkdir(parents=True)
    for name in ("monzo.csv", "overrides.json", "anchors.jsonl", "paydays.jsonl",
                 "commitments.jsonl"):
        shutil.copyfile(source / name, private / name)
    config = {
        "schema_version": "gate-a-v1",
        "as_of_date": "2026-05-17",
        "period_start": "2025-10-01",
        "period_end_exclusive": "2026-05-01",
        "dataset_id": "synthetic-gate-a-cli-v1",
        "account_id": "synthetic-current-account",
        "source_csv": "data/private/gate-a-synthetic-cli-test/monzo.csv",
        "overrides_path": "data/private/gate-a-synthetic-cli-test/overrides.json",
        "anchors_path": "data/private/gate-a-synthetic-cli-test/anchors.jsonl",
        "paydays_path": "data/private/gate-a-synthetic-cli-test/paydays.jsonl",
        "commitments_path": "data/private/gate-a-synthetic-cli-test/commitments.jsonl",
        "canonical_directory": "outputs/private/gate-a-synthetic-cli-test/canonical",
        "output_directory": "outputs/private/gate-a-synthetic-cli-test",
        "floors": {"lower": 20000, "primary": 30000, "higher": 40000},
    }
    config_path = private / "gate-a.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    relative_config = config_path.relative_to(project)

    try:
        assert main(["gate-a", "prepare", "--config", str(relative_config)]) == 0
        assert main(["gate-a", "evaluate", "--config", str(relative_config)]) == 0
        with connect(repository.dsn) as connection:
            runs_before_assess = connection.execute(
                "SELECT count(*) FROM backtest_runs"
            ).fetchone()[0]
        assert main(["gate-a", "assess", "--config", str(relative_config)]) == 0
        with connect(repository.dsn) as connection:
            assert connection.execute("SELECT count(*) FROM backtest_runs").fetchone() == (
                runs_before_assess,
            )

        report_json = output / "assessment" / "gate-a-report.json"
        report_markdown = output / "assessment" / "gate-a-report.md"
        assert report_json.is_file()
        assert report_markdown.is_file()
        payload = json.loads(report_json.read_text(encoding="utf-8"))
        assert payload["decision"] == "proceed_to_modelling"
        primary = next(row for row in payload["scenarios"] if row["name"] == "primary")
        assert all(row["scored_checkpoints"] >= 12 for row in primary["baselines"])
        before = {
            path.relative_to(output).as_posix(): path.read_bytes()
            for path in output.rglob("*") if path.is_file()
        }

        assert main(["gate-a", "prepare", "--config", str(relative_config)]) == 0
        assert main(["gate-a", "evaluate", "--config", str(relative_config)]) == 0
        with connect(repository.dsn) as connection:
            runs_before_second_assess = connection.execute(
                "SELECT count(*) FROM backtest_runs"
            ).fetchone()[0]
        assert main(["gate-a", "assess", "--config", str(relative_config)]) == 0
        with connect(repository.dsn) as connection:
            assert connection.execute("SELECT count(*) FROM backtest_runs").fetchone() == (
                runs_before_second_assess,
            )
        assert {
            path.relative_to(output).as_posix(): path.read_bytes()
            for path in output.rglob("*") if path.is_file()
        } == before

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        password = conninfo_to_dict(os.environ["DATABASE_URL"]).get("password", "")
        forbidden = (
            "Invented weekly purchase",
            "synthetic-card-01",
            str(private),
            str(output),
            "20000",
            "30000",
            "40000",
            password,
        )
        assert captured.err == ""
        assert all(not value or value not in combined for value in forbidden)
        assert captured.out.splitlines() == [
            "gate-a prepare: status=completed.",
            "gate-a evaluate: status=completed; scenarios=3.",
            (
                "gate-a assess: status=proceed_to_modelling; "
                "files=gate-a-report.json,gate-a-report.md."
            ),
            "gate-a prepare: status=noop.",
            "gate-a evaluate: status=noop; scenarios=3.",
            (
                "gate-a assess: status=proceed_to_modelling; "
                "files=gate-a-report.json,gate-a-report.md."
            ),
        ]
    finally:
        shutil.rmtree(private, ignore_errors=True)
        shutil.rmtree(output, ignore_errors=True)
