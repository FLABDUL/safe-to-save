import csv
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from safe_to_save.gate_config import FloorScenarios, GateAConfig
from safe_to_save.gate_evaluate import evaluate_gate
from safe_to_save.gate_prepare import prepare_private_history
from safe_to_save.monzo_export import MONZO_CSV_V1_HEADERS
from safe_to_save.paycycles import PayCycle, weekly_checkpoints
from safe_to_save.repository import ImportResult
from safe_to_save.workflow import WorkflowResult

HISTORY_FILES = (
    "manifest.json",
    "transactions.jsonl",
    "anchors.jsonl",
    "paydays.jsonl",
    "commitments.jsonl",
)


class FakeRepository:
    def __init__(self) -> None:
        self.imports = 0

    def import_bundle(self, _bundle) -> ImportResult:
        self.imports += 1
        return ImportResult("completed", 1)


def _canonical_hash(contents: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in HISTORY_FILES:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(contents[name])
    return digest.hexdigest()


def _config(tmp_path: Path) -> GateAConfig:
    source = tmp_path / "source.csv"
    with source.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=MONZO_CSV_V1_HEADERS)
        writer.writeheader()
        writer.writerow(
            {
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
            }
        )
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
        '{"known_from":"2025-12-01","payday":"2026-01-28"}\n',
        encoding="utf-8",
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


def _install_fake_backtest(monkeypatch, calls: list[tuple]) -> None:
    def fake_run(repository, dataset_id, schedule, floor_minor, data_label, output):
        calls.append((repository, dataset_id, schedule, floor_minor, data_label, output.name))
        output.mkdir(parents=True)
        (output / "baseline-report.json").write_text(
            json.dumps({"scenario": output.name}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (output / "baseline-report.md").write_text(
            f"# Synthetic {output.name}\n",
            encoding="utf-8",
        )
        return WorkflowResult("completed", output, 0, (), (), "a" * 64)

    monkeypatch.setattr("safe_to_save.gate_evaluate.run_backtest", fake_run)


def test_gate_uses_monday_0600_london_through_dst(tmp_path, monkeypatch):
    config = _config(tmp_path)
    calls = []
    _install_fake_backtest(monkeypatch, calls)

    evaluate_gate(config, FakeRepository())

    schedule = calls[0][2]
    assert (schedule.weekday, schedule.local_time.isoformat(), schedule.timezone) == (
        0,
        "06:00:00",
        "Europe/London",
    )
    checkpoints = weekly_checkpoints(
        PayCycle(date(2026, 3, 23), date(2026, 4, 7)),
        schedule,
    )
    assert checkpoints == (
        datetime(2026, 3, 23, 6, tzinfo=UTC),
        datetime(2026, 3, 30, 5, tzinfo=UTC),
        datetime(2026, 4, 6, 5, tzinfo=UTC),
    )


def test_gate_runs_lower_primary_higher_in_stable_order(tmp_path, monkeypatch):
    config = _config(tmp_path)
    calls = []
    _install_fake_backtest(monkeypatch, calls)
    repository = FakeRepository()

    result = evaluate_gate(config, repository)

    assert repository.imports == 1
    assert tuple(row.name for row in result.scenarios) == ("lower", "primary", "higher")
    assert [(row[3], row[5]) for row in calls] == [
        (20_000, "lower"),
        (30_000, "primary"),
        (40_000, "higher"),
    ]


def test_scenario_directories_and_manifest_are_deterministic(tmp_path, monkeypatch):
    config = _config(tmp_path)
    calls = []
    _install_fake_backtest(monkeypatch, calls)

    completed = evaluate_gate(config, FakeRepository())
    before = {
        path.relative_to(config.output_directory).as_posix(): path.read_bytes()
        for path in config.output_directory.rglob("*")
        if path.is_file()
    }
    unchanged = evaluate_gate(config, FakeRepository())

    assert completed.status == "completed"
    assert unchanged.status == "noop"
    assert {
        path.relative_to(config.output_directory).as_posix(): path.read_bytes()
        for path in config.output_directory.rglob("*")
        if path.is_file()
    } == before
    manifest = json.loads(before["evaluation-manifest.json"])
    assert (
        before["preparation-manifest.json"]
        == (config.canonical_directory / "preparation-manifest.json").read_bytes()
    )
    assert (
        hashlib.sha256(before["preparation-manifest.json"]).hexdigest()
        == (manifest["preparation_sha256"])
    )
    assert manifest["schedule"] == {
        "review_hour": 6,
        "review_weekday": 0,
        "timezone": "Europe/London",
    }
    assert [row["name"] for row in manifest["scenarios"]] == [
        "lower",
        "primary",
        "higher",
    ]
    assert set(manifest["scenarios"][0]["reports"]) == {
        "baseline-report.json",
        "baseline-report.md",
    }
    assert "20000" not in before["evaluation-manifest.json"].decode()


def test_evidence_hash_mismatch_fails_before_import_or_output(tmp_path, monkeypatch):
    config = _config(tmp_path)
    calls = []
    _install_fake_backtest(monkeypatch, calls)
    repository = FakeRepository()
    with (config.canonical_directory / "transactions.jsonl").open("ab") as stream:
        stream.write(b"\n")

    result = evaluate_gate(config, repository)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "invalid_preparation"
    assert repository.imports == 0
    assert calls == []
    assert not config.output_directory.exists()


def test_changed_config_period_rejects_old_canonical_evidence_before_import(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path).model_copy(update={"period_start": date(2025, 10, 1)})
    calls = []
    _install_fake_backtest(monkeypatch, calls)
    repository = FakeRepository()

    result = evaluate_gate(config, repository)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "invalid_preparation"
    assert repository.imports == 0
    assert calls == []
    assert not config.output_directory.exists()


def test_rehashed_but_unreconciled_anchors_are_rejected_before_import(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)
    anchors_path = config.canonical_directory / "anchors.jsonl"
    anchors = [json.loads(line) for line in anchors_path.read_text().splitlines()]
    anchors[-1]["balance_minor"] += 1
    anchors_path.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in anchors),
        encoding="utf-8",
    )
    contents = {name: (config.canonical_directory / name).read_bytes() for name in HISTORY_FILES}
    manifest_path = config.canonical_directory / "preparation-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["anchors.jsonl"] = {
        "sha256": hashlib.sha256(contents["anchors.jsonl"]).hexdigest(),
        "row_count": len(contents["anchors.jsonl"].splitlines()),
    }
    manifest["canonical_input_sha256"] = _canonical_hash(contents)
    manifest["auxiliary_sources"]["anchors"]["sha256"] = hashlib.sha256(
        contents["anchors.jsonl"]
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    calls = []
    _install_fake_backtest(monkeypatch, calls)
    repository = FakeRepository()

    result = evaluate_gate(config, repository)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "invalid_preparation"
    assert repository.imports == 0
    assert calls == []
    assert not config.output_directory.exists()


def test_stale_evaluation_is_preserved_and_rejected_before_import(tmp_path, monkeypatch):
    config = _config(tmp_path)
    calls = []
    _install_fake_backtest(monkeypatch, calls)
    assert evaluate_gate(config, FakeRepository()).status == "completed"
    report = config.output_directory / "primary" / "baseline-report.md"
    report.write_bytes(b"stale synthetic report\n")
    before = {
        path.relative_to(config.output_directory).as_posix(): path.read_bytes()
        for path in config.output_directory.rglob("*")
        if path.is_file()
    }
    repository = FakeRepository()
    calls.clear()

    result = evaluate_gate(config, repository)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "existing_evaluation_differs"
    assert repository.imports == 0
    assert calls == []
    assert {
        path.relative_to(config.output_directory).as_posix(): path.read_bytes()
        for path in config.output_directory.rglob("*")
        if path.is_file()
    } == before


@pytest.mark.parametrize("change", ["missing", "tampered"])
def test_changed_copied_preparation_manifest_is_rejected_before_import(
    tmp_path,
    monkeypatch,
    change,
):
    config = _config(tmp_path)
    calls = []
    _install_fake_backtest(monkeypatch, calls)
    assert evaluate_gate(config, FakeRepository()).status == "completed"
    copied = config.output_directory / "preparation-manifest.json"
    if change == "missing":
        copied.unlink()
    else:
        copied.write_bytes(copied.read_bytes() + b" ")
    before = {
        path.relative_to(config.output_directory).as_posix(): path.read_bytes()
        for path in config.output_directory.rglob("*")
        if path.is_file()
    }
    repository = FakeRepository()
    calls.clear()

    result = evaluate_gate(config, repository)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "existing_evaluation_differs"
    assert repository.imports == 0
    assert calls == []
    assert {
        path.relative_to(config.output_directory).as_posix(): path.read_bytes()
        for path in config.output_directory.rglob("*")
        if path.is_file()
    } == before


@pytest.mark.parametrize("change", ["missing", "extra", "altered"])
def test_changed_source_provenance_is_rejected_before_import_or_replay(
    tmp_path,
    monkeypatch,
    change,
):
    config = _config(tmp_path)
    calls = []
    _install_fake_backtest(monkeypatch, calls)
    assert evaluate_gate(config, FakeRepository()).status == "completed"

    preparation_path = config.canonical_directory / "preparation-manifest.json"
    preparation = json.loads(preparation_path.read_text(encoding="utf-8"))
    records = preparation["source"]["record_provenance"]
    transaction_id = next(iter(records))
    if change == "missing":
        records.pop(transaction_id)
    elif change == "extra":
        records["monzo-" + "f" * 64] = {
            "source_line": 99,
            "source_hash": "f" * 64,
        }
    else:
        records[transaction_id]["source_line"] += 1
    preparation_bytes = (
        json.dumps(preparation, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    preparation_path.write_bytes(preparation_bytes)

    copied = config.output_directory / "preparation-manifest.json"
    copied.write_bytes(preparation_bytes)
    evaluation_path = config.output_directory / "evaluation-manifest.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["preparation_sha256"] = hashlib.sha256(preparation_bytes).hexdigest()
    evaluation_path.write_text(
        json.dumps(evaluation, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    before = {
        path.relative_to(config.output_directory).as_posix(): path.read_bytes()
        for path in config.output_directory.rglob("*")
        if path.is_file()
    }
    repository = FakeRepository()
    calls.clear()

    result = evaluate_gate(config, repository)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "invalid_preparation"
    assert repository.imports == 0
    assert calls == []
    assert {
        path.relative_to(config.output_directory).as_posix(): path.read_bytes()
        for path in config.output_directory.rglob("*")
        if path.is_file()
    } == before
