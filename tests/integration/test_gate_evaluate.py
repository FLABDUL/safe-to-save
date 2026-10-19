import csv
from datetime import date
from pathlib import Path

import pytest

from safe_to_save.db import connect
from safe_to_save.gate_config import FloorScenarios, GateAConfig
from safe_to_save.gate_evaluate import evaluate_gate
from safe_to_save.gate_prepare import prepare_private_history
from safe_to_save.monzo_export import MONZO_CSV_V1_HEADERS

pytestmark = pytest.mark.integration

def _config(tmp_path: Path) -> GateAConfig:
    source = tmp_path / "source.csv"
    with source.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=MONZO_CSV_V1_HEADERS)
        writer.writeheader()
        writer.writerow({
            **dict.fromkeys(MONZO_CSV_V1_HEADERS, ""),
            "Transaction ID": "synthetic-provider-id",
            "Date": "10/01/2026",
            "Time": "12:00:00",
            "Type": "Card payment",
            "Name": "Invented Merchant",
            "Category": "groceries",
            "Amount": "-10.00",
            "Currency": "GBP",
            "Description": "Invented private source text",
        })
    anchors = tmp_path / "anchors.jsonl"
    anchors.write_text(
        '{"balance_minor":100000,"captured_at":"2025-08-31T23:00:00Z",'
        '"currency":"GBP"}\n'
        '{"balance_minor":99000,"captured_at":"2026-04-30T23:00:00Z",'
        '"currency":"GBP"}\n',
        encoding="utf-8",
    )
    paydays = tmp_path / "paydays.jsonl"
    paydays.write_text(
        '{"known_from":"2025-12-01","payday":"2026-01-28"}\n', encoding="utf-8",
    )
    commitments = tmp_path / "commitments.jsonl"
    commitments.write_text("", encoding="utf-8")
    config = GateAConfig(
        schema_version="gate-a-v1",
        as_of_date=date(2026, 5, 17),
        period_start=date(2025, 9, 1),
        period_end_exclusive=date(2026, 5, 1),
        dataset_id="synthetic-v2",
        account_id="synthetic-current",
        source_csv=source,
        anchors_path=anchors,
        paydays_path=paydays,
        commitments_path=commitments,
        canonical_directory=tmp_path / "canonical",
        output_directory=tmp_path / "evaluation",
        floors=FloorScenarios(lower=20_000, primary=30_000, higher=40_000),
    )
    assert prepare_private_history(config).status == "completed"
    return config


def test_gate_imports_once_and_persists_three_runs_per_baseline(repository, tmp_path):
    config = _config(tmp_path)

    result = evaluate_gate(config, repository)

    assert result.status == "completed"
    with connect(repository.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM history_imports").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 9


def test_unchanged_gate_evaluation_is_noop_and_byte_stable(repository, tmp_path):
    config = _config(tmp_path)
    assert evaluate_gate(config, repository).status == "completed"
    before = {
        path.relative_to(config.output_directory).as_posix(): path.read_bytes()
        for path in config.output_directory.rglob("*") if path.is_file()
    }

    result = evaluate_gate(config, repository)

    assert result.status == "noop"
    assert {
        path.relative_to(config.output_directory).as_posix(): path.read_bytes()
        for path in config.output_directory.rglob("*") if path.is_file()
    } == before
    with connect(repository.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 9
