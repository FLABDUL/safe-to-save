import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from safe_to_save.contracts import Commitment, HistoryManifest, RawHistoryRecord
from safe_to_save.history import HistoryConflictError, load_history

FIXTURE = Path("tests/fixtures/synthetic_history")
HISTORY_FILES = (
    "manifest.json",
    "transactions.jsonl",
    "anchors.jsonl",
    "paydays.jsonl",
    "commitments.jsonl",
)


def _copy_fixture(destination: Path) -> None:
    for name in HISTORY_FILES:
        (destination / name).write_bytes((FIXTURE / name).read_bytes())


@pytest.mark.parametrize("filename,field", [
    ("transactions.jsonl", "amount_minor"),
    ("anchors.jsonl", "balance_minor"),
    ("commitments.jsonl", "amount_minor"),
])
@pytest.mark.parametrize("invalid", [False, True, -4200.0, -4200.5, -9007199254740993.0, "-4200"])
def test_loader_rejects_coercible_money(tmp_path: Path, filename: str, field: str, invalid) -> None:
    _copy_fixture(tmp_path)
    row = json.loads((tmp_path / filename).read_text().splitlines()[0])
    row[field] = invalid
    (tmp_path / filename).write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="valid integer"):
        load_history(tmp_path)


@pytest.mark.parametrize("amount", [-9007199254740993, 9007199254740993])
def test_loader_preserves_large_signed_integer_money_exactly(tmp_path: Path, amount: int) -> None:
    _copy_fixture(tmp_path)
    row = json.loads((tmp_path / "transactions.jsonl").read_text().splitlines()[0])
    row["amount_minor"] = amount
    (tmp_path / "transactions.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert load_history(tmp_path).transactions[0].amount_minor == amount


def test_load_history_preserves_signed_minor_units_and_aware_times() -> None:
    history = load_history(FIXTURE)
    purchase = next(
        row for row in history.transactions if row.transaction_id == "synthetic-tx-001"
    )
    assert purchase.amount_minor == -4200
    assert purchase.occurred_at.utcoffset() is not None
    assert history.manifest.currency == "GBP"


def test_conflicting_provider_id_is_rejected_but_both_raw_lines_are_returned(
    tmp_path: Path,
) -> None:
    _copy_fixture(tmp_path)
    conflicting = tmp_path / "transactions.jsonl"
    conflicting.write_text(
        conflicting.read_text(encoding="utf-8")
        + '{"transaction_id":"synthetic-tx-001","occurred_at":"2026-01-03T12:00:00Z",'
        '"amount_minor":-9999,"currency":"GBP","status":"settled",'
        '"economic_type":"expense","committed":false}\n',
        encoding="utf-8",
    )

    with pytest.raises(HistoryConflictError) as exc:
        load_history(tmp_path)

    assert exc.value.transaction_id == "synthetic-tx-001"
    assert len(exc.value.raw_records) == 2

    evidence = exc.value.evidence
    assert evidence.manifest == load_history(FIXTURE).manifest
    for name in HISTORY_FILES:
        source = (tmp_path / name).read_bytes()
        chunks = [source] if name == "manifest.json" else source.splitlines(keepends=True)
        records = [row for row in evidence.raw_records if row.source_name == name]
        assert len(records) == len(chunks)
        for index, (raw, chunk) in enumerate(zip(records, chunks), 1):
            assert raw.source_line == index
            assert raw.payload == json.loads(chunk)
            assert raw.source_hash == hashlib.sha256(chunk).hexdigest()


def test_byte_identical_duplicate_is_one_canonical_transaction_and_two_raw_records(
    tmp_path: Path,
) -> None:
    _copy_fixture(tmp_path)
    transactions = tmp_path / "transactions.jsonl"
    first_line = transactions.read_bytes().splitlines(keepends=True)[0]
    with transactions.open("ab") as destination:
        destination.write(first_line)

    history = load_history(tmp_path)

    canonical = [row for row in history.transactions if row.transaction_id == "synthetic-tx-001"]
    raw = [
        row
        for row in history.raw_records
        if row.source_name == "transactions.jsonl"
        and row.payload.get("transaction_id") == "synthetic-tx-001"
    ]
    assert len(canonical) == 1
    assert len(raw) == 2


def test_raw_record_hashes_exact_line_bytes() -> None:
    first_line = (FIXTURE / "transactions.jsonl").read_bytes().splitlines(keepends=True)[0]

    history = load_history(FIXTURE)
    first_raw = next(
        row
        for row in history.raw_records
        if row.source_name == "transactions.jsonl" and row.source_line == 1
    )

    assert first_raw.source_hash == hashlib.sha256(first_line).hexdigest()


def test_raw_record_payload_is_deeply_immutable_and_remains_serialisable() -> None:
    record = RawHistoryRecord(
        source_name="transactions.jsonl",
        source_line=1,
        payload={"amount_minor": -4200, "metadata": {"labels": ["synthetic"]}},
        source_hash="synthetic-hash",
    )

    with pytest.raises(TypeError):
        record.payload["amount_minor"] = 0
    with pytest.raises(TypeError):
        record.payload["metadata"]["labels"] = []  # type: ignore[index]
    with pytest.raises(AttributeError):
        record.payload["metadata"]["labels"].append("changed")  # type: ignore[union-attr]

    assert record.model_dump(mode="json")["payload"] == {
        "amount_minor": -4200,
        "metadata": {"labels": ["synthetic"]},
    }


def test_loaded_records_are_sorted_deterministically() -> None:
    history = load_history(FIXTURE)

    assert list(history.transactions) == sorted(
        history.transactions, key=lambda row: row.occurred_at
    )
    assert list(history.anchors) == sorted(history.anchors, key=lambda row: row.captured_at)
    assert list(history.paydays) == sorted(history.paydays, key=lambda row: row.payday)


@pytest.mark.parametrize(
    ("filename", "replacement", "expected"),
    [
        (
            "transactions.jsonl",
            {
                "transaction_id": "synthetic-naive",
                "occurred_at": "2026-01-02T12:00:00",
                "amount_minor": -100,
                "currency": "GBP",
                "status": "settled",
                "economic_type": "expense",
                "committed": False,
            },
            "timezone",
        ),
        (
            "transactions.jsonl",
            {
                "transaction_id": "synthetic-eur",
                "occurred_at": "2026-01-02T12:00:00Z",
                "amount_minor": -100,
                "currency": "EUR",
                "status": "settled",
                "economic_type": "expense",
                "committed": False,
            },
            "GBP",
        ),
        (
            "commitments.jsonl",
            {
                "commitment_id": "synthetic-positive",
                "known_from": "2026-01-01T00:00:00Z",
                "due_at": "2026-01-02T00:00:00Z",
                "amount_minor": 100,
                "description": "Synthetic Invalid Commitment",
            },
            "less than or equal to 0",
        ),
    ],
)
def test_invalid_jsonl_models_are_rejected(
    tmp_path: Path, filename: str, replacement: dict[str, object], expected: str
) -> None:
    _copy_fixture(tmp_path)
    (tmp_path / filename).write_text(json.dumps(replacement) + "\n", encoding="utf-8")

    with pytest.raises(ValidationError, match=expected):
        load_history(tmp_path)


def test_manifest_rejects_reversed_coverage() -> None:
    with pytest.raises(ValidationError, match="coverage_end must be after coverage_start"):
        HistoryManifest(
            dataset_id="synthetic-invalid",
            account_id="synthetic-current",
            currency="GBP",
            coverage_start="2026-02-01T00:00:00Z",
            coverage_end="2026-01-01T00:00:00Z",
        )


def test_commitment_rejects_due_time_before_known_time() -> None:
    with pytest.raises(ValidationError, match="due_at must be after known_from"):
        Commitment(
            commitment_id="synthetic-invalid",
            known_from="2026-02-01T00:00:00Z",
            due_at="2026-01-01T00:00:00Z",
            amount_minor=-100,
            description="Synthetic Invalid Commitment",
        )


@pytest.mark.parametrize(
    "content",
    [
        "not-json\n",
        (
            '{"captured_at":"2025-09-01T00:00:00Z",'
            '"balance_minor":72000,"currency":"GBP"}\n\n'
        ),
    ],
)
def test_malformed_or_blank_jsonl_reports_filename_and_line(
    tmp_path: Path, content: str
) -> None:
    _copy_fixture(tmp_path)
    (tmp_path / "anchors.jsonl").write_text(content, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_history(tmp_path)

    message = str(exc.value)
    assert "anchors.jsonl" in message
    assert ("line 1" in message) if content.startswith("not-json") else ("line 2" in message)


def test_missing_required_file_names_the_file(tmp_path: Path) -> None:
    _copy_fixture(tmp_path)
    (tmp_path / "paydays.jsonl").unlink()

    with pytest.raises(FileNotFoundError, match="paydays.jsonl"):
        load_history(tmp_path)


def test_malformed_manifest_reports_filename_and_line(tmp_path: Path) -> None:
    _copy_fixture(tmp_path)
    (tmp_path / "manifest.json").write_text("{\nnot-json\n}", encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_history(tmp_path)

    assert "manifest.json" in str(exc.value)
    assert "line 2" in str(exc.value)


def test_unrecognised_extra_file_is_ignored(tmp_path: Path) -> None:
    _copy_fixture(tmp_path)
    (tmp_path / "personal-data.jsonl").write_text("not-json\n", encoding="utf-8")

    history = load_history(tmp_path)

    assert all(row.source_name in HISTORY_FILES[1:] for row in history.raw_records)
