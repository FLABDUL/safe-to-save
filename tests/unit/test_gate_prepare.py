import csv
import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from safe_to_save.gate_config import FloorScenarios, GateAConfig
from safe_to_save.gate_prepare import (
    SourceProvenanceError,
    prepare_private_history,
    validate_source_provenance,
)
from safe_to_save.history import load_history
from safe_to_save.monzo_export import MONZO_CSV_V1_HEADERS


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _config(tmp_path: Path, *, reconcile: bool = True) -> GateAConfig:
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
    _write_jsonl(anchors, [
        {"captured_at": "2025-08-31T23:00:00Z", "balance_minor": 100_000,
         "currency": "GBP"},
        {"captured_at": "2026-04-30T23:00:00Z",
         "balance_minor": 99_000 if reconcile else 100_000, "currency": "GBP"},
    ])
    paydays = tmp_path / "paydays.jsonl"
    _write_jsonl(paydays, [{"payday": "2026-01-28", "known_from": "2025-12-01"}])
    commitments = tmp_path / "commitments.jsonl"
    _write_jsonl(commitments, [])
    return GateAConfig(
        schema_version="gate-a-v1",
        as_of_date=date(2026, 5, 17),
        period_start=date(2025, 9, 1),
        period_end_exclusive=date(2026, 5, 1),
        dataset_id="synthetic-gate-a",
        account_id="operator-attested-current",
        source_csv=source,
        anchors_path=anchors,
        paydays_path=paydays,
        commitments_path=commitments,
        canonical_directory=tmp_path / "canonical",
        output_directory=tmp_path / "reports",
        floors=FloorScenarios(lower=20_000, primary=30_000, higher=40_000),
    )


def test_prepare_writes_existing_five_file_contract(tmp_path: Path) -> None:
    config = _config(tmp_path)

    result = prepare_private_history(config)

    assert result.status == "completed"
    assert {path.name for path in config.canonical_directory.iterdir()} == {
        "manifest.json", "transactions.jsonl", "anchors.jsonl", "paydays.jsonl",
        "commitments.jsonl", "preparation-manifest.json",
    }
    history = load_history(config.canonical_directory)
    assert history.manifest.account_id == "operator-attested-current"
    assert len(history.transactions) == 1


def test_prepare_manifest_hashes_source_rows_and_transform_versions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    overrides = tmp_path / "overrides.json"
    overrides.write_bytes(b"[]\r\n")
    config = config.model_copy(update={"overrides_path": overrides})
    source_bytes = config.source_csv.read_bytes()

    assert prepare_private_history(config).status == "completed"

    evidence = json.loads(
        (config.canonical_directory / "preparation-manifest.json").read_text(encoding="utf-8")
    )
    assert evidence["source"]["sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert evidence["source"]["line_hashes"] == [
        hashlib.sha256(line).hexdigest() for line in source_bytes.splitlines(keepends=True)
    ]
    assert evidence["source"]["row_count"] == 1
    assert evidence["auxiliary_sources"] == {
        "anchors": {
            "sha256": hashlib.sha256(config.anchors_path.read_bytes()).hexdigest(),
            "row_count": 2,
        },
        "commitments": {
            "sha256": hashlib.sha256(config.commitments_path.read_bytes()).hexdigest(),
            "row_count": 0,
        },
        "overrides": {
            "sha256": hashlib.sha256(overrides.read_bytes()).hexdigest(),
            "row_count": 0,
        },
        "paydays": {
            "sha256": hashlib.sha256(config.paydays_path.read_bytes()).hexdigest(),
            "row_count": 1,
        },
    }
    assert evidence["transforms"] == {
        "canonical": "gate-a-canonical-v1", "monzo": "monzo-csv-v1",
    }
    assert evidence["config_schema_version"] == "gate-a-v1"
    assert len(evidence["canonical_input_sha256"]) == 64
    assert evidence["account_binding"]["basis"] == "operator_attested_config"
    assert evidence["account_binding"]["applies_to_transaction_count"] == 1
    serialised = json.dumps(evidence)
    assert "synthetic-provider-id" not in serialised
    assert "Invented private source text" not in serialised


def test_prepare_manifest_maps_sorted_transactions_to_exact_multiline_records(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with config.source_csv.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=MONZO_CSV_V1_HEADERS)
        writer.writeheader()
        base = {
            **dict.fromkeys(MONZO_CSV_V1_HEADERS, ""),
            "Time": "12:00:00",
            "Type": "Card payment",
            "Currency": "GBP",
        }
        writer.writerow({
            **base,
            "Transaction ID": "synthetic-later",
            "Date": "20/01/2026",
            "Amount": "-10.00",
            "Description": "Invented line one\nInvented line two",
        })
        writer.writerow({
            **base,
            "Transaction ID": "synthetic-earlier",
            "Date": "10/01/2026",
            "Amount": "-2.00",
        })
    _write_jsonl(config.anchors_path, [
        {"captured_at": "2025-08-31T23:00:00Z", "balance_minor": 100_000,
         "currency": "GBP"},
        {"captured_at": "2026-04-30T23:00:00Z", "balance_minor": 98_800,
         "currency": "GBP"},
    ])
    physical_lines = config.source_csv.read_bytes().splitlines(keepends=True)
    earlier_id = "monzo-" + hashlib.sha256(b"synthetic-earlier").hexdigest()
    later_id = "monzo-" + hashlib.sha256(b"synthetic-later").hexdigest()

    assert prepare_private_history(config).status == "completed"

    history = load_history(config.canonical_directory)
    evidence = json.loads(
        (config.canonical_directory / "preparation-manifest.json").read_text(encoding="utf-8")
    )
    assert [row.transaction_id for row in history.transactions] == [earlier_id, later_id]
    assert evidence["source"]["record_provenance"] == {
        earlier_id: {
            "source_line": 4,
            "source_hash": hashlib.sha256(physical_lines[3]).hexdigest(),
        },
        later_id: {
            "source_line": 2,
            "source_hash": hashlib.sha256(b"".join(physical_lines[1:3])).hexdigest(),
        },
    }
    assert "synthetic-earlier" not in json.dumps(evidence)
    assert "Invented line one" not in json.dumps(evidence)


@pytest.mark.parametrize("mutation", ["missing", "extra", "line", "hash"])
def test_source_provenance_rejects_missing_extra_or_altered_record_mapping(
    tmp_path: Path, mutation: str,
) -> None:
    config = _config(tmp_path)
    assert prepare_private_history(config).status == "completed"
    bundle = load_history(config.canonical_directory)
    evidence = json.loads(
        (config.canonical_directory / "preparation-manifest.json").read_text(encoding="utf-8")
    )
    records = evidence["source"]["record_provenance"]
    transaction_id = bundle.transactions[0].transaction_id
    if mutation == "missing":
        records.pop(transaction_id)
    elif mutation == "extra":
        records["monzo-" + "f" * 64] = {
            "source_line": 99,
            "source_hash": "f" * 64,
        }
    elif mutation == "line":
        records[transaction_id]["source_line"] += 1
    else:
        records[transaction_id]["source_hash"] = "f" * 64

    with pytest.raises(SourceProvenanceError, match="^source provenance validation failed$"):
        validate_source_provenance(config, bundle, evidence)


@pytest.mark.parametrize("changed_input", ["source", "overrides"])
def test_source_provenance_rejects_changed_source_or_override_snapshot(
    tmp_path: Path, changed_input: str,
) -> None:
    config = _config(tmp_path)
    overrides = tmp_path / "overrides.json"
    overrides.write_bytes(b"[]\n")
    config = config.model_copy(update={"overrides_path": overrides})
    assert prepare_private_history(config).status == "completed"
    bundle = load_history(config.canonical_directory)
    evidence = json.loads(
        (config.canonical_directory / "preparation-manifest.json").read_text(encoding="utf-8")
    )
    if changed_input == "source":
        config.source_csv.write_bytes(
            config.source_csv.read_bytes().replace(b"Invented Merchant", b"Changed Merchant")
        )
    else:
        overrides.write_bytes(b"[]\r\n")

    with pytest.raises(SourceProvenanceError, match="^source provenance validation failed$"):
        validate_source_provenance(config, bundle, evidence)


def test_source_provenance_parses_each_current_input_snapshot_once(
    tmp_path: Path, monkeypatch,
) -> None:
    config = _config(tmp_path)
    overrides = tmp_path / "overrides.json"
    overrides.write_bytes(b"[]\n")
    config = config.model_copy(update={"overrides_path": overrides})
    assert prepare_private_history(config).status == "completed"
    bundle = load_history(config.canonical_directory)
    evidence = json.loads(
        (config.canonical_directory / "preparation-manifest.json").read_text(encoding="utf-8")
    )
    originals = {
        config.source_csv: config.source_csv.read_bytes(),
        overrides: overrides.read_bytes(),
    }
    changed = {
        config.source_csv: originals[config.source_csv].replace(
            b"Invented Merchant", b"Changed Merchant"
        ),
        overrides: b"[]\r\n",
    }
    original_read_bytes = Path.read_bytes
    reads = {path: 0 for path in originals}

    def varying_read_bytes(path: Path) -> bytes:
        if path in originals:
            reads[path] += 1
            return originals[path] if reads[path] == 1 else changed[path]
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", varying_read_bytes)

    validate_source_provenance(config, bundle, evidence)

    assert set(reads.values()) == {1}


def test_identical_prepare_is_a_noop(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert prepare_private_history(config).status == "completed"
    before = {path.name: path.read_bytes() for path in config.canonical_directory.iterdir()}

    result = prepare_private_history(config)

    assert result.status == "noop"
    assert {path.name: path.read_bytes() for path in config.canonical_directory.iterdir()} == before


def test_changed_prepare_input_preserves_existing_output(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert prepare_private_history(config).status == "completed"
    before = {path.name: path.read_bytes() for path in config.canonical_directory.iterdir()}
    _write_jsonl(config.paydays_path, [
        {"payday": "2026-01-29", "known_from": "2025-12-01"},
    ])

    result = prepare_private_history(config)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "existing_output_differs"
    assert {path.name: path.read_bytes() for path in config.canonical_directory.iterdir()} == before


def test_representation_only_input_change_is_not_noop_and_preserves_output(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    assert prepare_private_history(config).status == "completed"
    before = {path.name: path.read_bytes() for path in config.canonical_directory.iterdir()}
    config.paydays_path.write_bytes(
        b'{ "known_from": "2025-12-01", "payday": "2026-01-28" }\r\n'
    )

    result = prepare_private_history(config)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "existing_output_differs"
    assert {path.name: path.read_bytes() for path in config.canonical_directory.iterdir()} == before


def test_auxiliary_evidence_and_parsing_share_one_immutable_byte_snapshot(
    tmp_path: Path, monkeypatch,
) -> None:
    config = _config(tmp_path)
    override_snapshot = json.dumps([{
        "source_line": 2,
        "economic_type": "expense",
        "committed": False,
        "category": "snapshot-category",
    }]).encode()
    override_changed = override_snapshot.replace(b"snapshot-category", b"changed-category")
    overrides = tmp_path / "overrides.json"
    overrides.write_bytes(override_snapshot)
    config = config.model_copy(update={"overrides_path": overrides})
    payday_snapshot = config.paydays_path.read_bytes()
    payday_changed = payday_snapshot.replace(b"2026-01-28", b"2026-01-29")
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text
    reads = {"overrides": 0, "paydays": 0}
    override_was_read_as_text = False

    def varying_read_bytes(path: Path) -> bytes:
        if path == overrides:
            reads["overrides"] += 1
            return override_changed if override_was_read_as_text else override_snapshot
        if path == config.paydays_path:
            reads["paydays"] += 1
            return payday_snapshot if reads["paydays"] == 1 else payday_changed
        return original_read_bytes(path)

    def tracked_read_text(path: Path, *args, **kwargs) -> str:
        nonlocal override_was_read_as_text
        if path == overrides:
            override_was_read_as_text = True
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", varying_read_bytes)
    monkeypatch.setattr(Path, "read_text", tracked_read_text)

    assert prepare_private_history(config).status == "completed"

    history = load_history(config.canonical_directory)
    evidence = json.loads(
        (config.canonical_directory / "preparation-manifest.json").read_text(encoding="utf-8")
    )
    assert reads == {"overrides": 1, "paydays": 1}
    assert history.transactions[0].category == "snapshot-category"
    assert history.paydays[0].payday == date(2026, 1, 28)
    assert evidence["auxiliary_sources"]["overrides"]["sha256"] == hashlib.sha256(
        override_snapshot
    ).hexdigest()
    assert evidence["auxiliary_sources"]["paydays"]["sha256"] == hashlib.sha256(
        payday_snapshot
    ).hexdigest()


def test_reconciliation_failure_returns_fix_decision_without_partial_directory(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, reconcile=False)

    result = prepare_private_history(config)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "reconciliation_failed"
    assert not config.canonical_directory.exists()


def test_out_of_coverage_source_returns_stable_coverage_decision(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.source_csv.write_text(
        config.source_csv.read_text(encoding="utf-8").replace("10/01/2026", "10/06/2026"),
        encoding="utf-8",
    )

    result = prepare_private_history(config)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "coverage_validation_failed"
    assert not config.canonical_directory.exists()


@pytest.mark.parametrize("boundary_date", ["01/09/2025", "01/05/2026"])
def test_transaction_at_either_period_boundary_is_rejected(
    tmp_path: Path, boundary_date: str,
) -> None:
    config = _config(tmp_path)
    config.source_csv.write_text(
        config.source_csv.read_text(encoding="utf-8")
        .replace("10/01/2026", boundary_date)
        .replace("12:00:00", "00:00:00"),
        encoding="utf-8",
    )

    result = prepare_private_history(config)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "coverage_validation_failed"
    assert not config.canonical_directory.exists()


def test_unavailable_output_returns_safe_decision_without_private_path(tmp_path: Path) -> None:
    config = _config(tmp_path)
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("synthetic", encoding="utf-8")
    config = config.model_copy(update={"canonical_directory": blocked_parent / "canonical"})

    result = prepare_private_history(config)

    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "output_unavailable"
    assert str(tmp_path) not in repr(result)


def test_cleanup_failure_replaces_noop_with_safe_terminal_decision(
    tmp_path: Path, monkeypatch,
) -> None:
    config = _config(tmp_path)
    assert prepare_private_history(config).status == "completed"

    with monkeypatch.context() as patch:
        patch.setattr(
            "safe_to_save.gate_prepare.shutil.rmtree",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("private path")),
        )
        result = prepare_private_history(config)

    leftovers = list(tmp_path.glob(".canonical-*"))
    assert result.status == "fix_data_or_policy"
    assert result.reason_code == "staging_cleanup_failed"
    assert str(tmp_path) not in repr(result)
    assert leftovers
    for directory in leftovers:
        shutil.rmtree(directory)
