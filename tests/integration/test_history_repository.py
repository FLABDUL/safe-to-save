import hashlib
import json
from pathlib import Path

import psycopg
import pytest

from safe_to_save.contracts import RawHistoryRecord
from safe_to_save.db import apply_migrations, connect
from safe_to_save.history import load_history
from safe_to_save.repository import HistoryRepository

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "synthetic_history"


def test_identical_import_is_a_recorded_noop(repository: HistoryRepository) -> None:
    bundle = load_history(FIXTURE)
    first = repository.import_bundle(bundle)
    second = repository.import_bundle(bundle)
    assert first.canonical_transactions_created == len(bundle.transactions)
    assert second.canonical_transactions_created == 0
    assert second.status == "noop"
    with connect(repository.dsn) as connection:
        assert connection.execute("SELECT status FROM history_imports ORDER BY created_at").fetchall() == [
            ("completed",), ("noop",)
        ]


def test_bundle_roundtrip_preserves_all_source_evidence(repository: HistoryRepository) -> None:
    bundle = load_history(FIXTURE)
    repository.import_bundle(bundle)
    assert repository.load_bundle(bundle.manifest.dataset_id) == bundle


def test_conflict_rolls_back_canonical_rows_and_retains_raw_evidence(
    repository: HistoryRepository,
) -> None:
    original = load_history(FIXTURE)
    repository.import_bundle(original)
    changed = original.transactions[0].model_copy(update={"amount_minor": -9999})
    payload = changed.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    conflict_raw = RawHistoryRecord(
        source_name="transactions.jsonl", source_line=999, payload=payload,
        source_hash=hashlib.sha256(encoded).hexdigest(),
    )
    conflicting = original.model_copy(update={
        "raw_records": (*original.raw_records, conflict_raw),
        "transactions": (changed, *original.transactions[1:]),
    })
    with pytest.raises(ValueError, match="canonical transaction conflict"):
        repository.import_bundle(conflicting)
    assert repository.count_raw_versions(original.transactions[0].transaction_id) == 2
    assert repository.load_bundle(original.manifest.dataset_id) == original
    with connect(repository.dsn) as connection:
        assert connection.execute(
            "SELECT records_seen FROM history_imports WHERE status = 'rejected'"
        ).fetchone() == (len(conflicting.raw_records),)


@pytest.mark.parametrize("statement", [
    "UPDATE raw_history_records SET source_hash = 'changed'",
    "DELETE FROM raw_history_records",
])
def test_raw_evidence_rejects_mutation(repository: HistoryRepository, statement: str) -> None:
    repository.import_bundle(load_history(FIXTURE))
    with (
        pytest.raises(psycopg.errors.RaiseException, match="immutable"),
        connect(repository.dsn) as connection,
    ):
        connection.execute(statement)


def test_migration_checksum_changes_are_rejected(
    repository: HistoryRepository, tmp_path: Path,
) -> None:
    for migration in Path("sql").glob("[0-9][0-9][0-9]_*.sql"):
        (tmp_path / migration.name).write_bytes(migration.read_bytes())
    source = Path("sql/001_history.sql")
    (tmp_path / source.name).write_bytes(source.read_bytes() + b"\n-- changed\n")
    with pytest.raises(ValueError, match="checksum"):
        apply_migrations(repository.dsn, tmp_path)


def test_applied_migrations_are_a_noop(repository: HistoryRepository) -> None:
    assert apply_migrations(repository.dsn, Path("sql")) == ()


def test_new_rows_before_conflict_are_rolled_back(repository: HistoryRepository) -> None:
    bundle = load_history(FIXTURE)
    repository.import_bundle(bundle)
    extra = bundle.transactions[0].model_copy(update={"transaction_id": "synthetic-extra"})
    extra_payload = extra.model_dump(mode="json")
    raw = RawHistoryRecord(
        source_name="transactions.jsonl", source_line=1000, payload=extra_payload,
        source_hash=hashlib.sha256(json.dumps(extra_payload).encode()).hexdigest(),
    )
    # An anchor conflict occurs after the new transaction is inserted.
    anchor = bundle.anchors[0].model_copy(update={"balance_minor": 1})
    anchor_payload = anchor.model_dump(mode="json")
    anchor_raw = RawHistoryRecord(
        source_name="anchors.jsonl", source_line=1000, payload=anchor_payload,
        source_hash=hashlib.sha256(json.dumps(anchor_payload).encode()).hexdigest(),
    )
    attempt = bundle.model_copy(update={
        "transactions": (*bundle.transactions, extra),
        "anchors": (anchor, *bundle.anchors[1:]),
        "raw_records": (*bundle.raw_records, raw, anchor_raw),
    })
    with pytest.raises(ValueError, match="canonical balance_anchors conflict"):
        repository.import_bundle(attempt)
    assert repository.load_bundle(bundle.manifest.dataset_id) == bundle
    assert repository.count_raw_versions("synthetic-extra") == 1


def test_roundtrip_keeps_commitments_in_source_order(repository: HistoryRepository) -> None:
    bundle = load_history(FIXTURE)
    commitments = tuple(reversed(bundle.commitments))
    raws = tuple(raw for raw in bundle.raw_records if raw.source_name != "commitments.jsonl")
    original_raws = tuple(raw for raw in bundle.raw_records if raw.source_name == "commitments.jsonl")
    raws += tuple(
        raw.model_copy(update={"source_line": index})
        for index, raw in enumerate(reversed(original_raws), 1)
    )
    bundle = bundle.model_copy(update={"commitments": commitments, "raw_records": raws})
    repository.import_bundle(bundle)
    assert repository.load_bundle(bundle.manifest.dataset_id) == bundle


def test_whitespace_noop_loads_one_snapshot_that_can_be_reimported(
    repository: HistoryRepository, tmp_path: Path,
) -> None:
    original = load_history(FIXTURE)
    repository.import_bundle(original)
    for source in FIXTURE.glob("*.json*"):
        if source.suffix == ".jsonl":
            # Different source bytes and hashes; identical validated canonical records.
            contents = "".join(
                json.dumps(json.loads(line), separators=(", ", ": ")) + "\n"
                for line in source.read_text(encoding="utf-8").splitlines()
            )
            (tmp_path / source.name).write_text(contents, encoding="utf-8")
        else:
            (tmp_path / source.name).write_bytes(source.read_bytes())
    reformatted = load_history(tmp_path)
    assert reformatted.transactions == original.transactions
    assert reformatted.raw_records != original.raw_records
    assert repository.import_bundle(reformatted).status == "noop"

    loaded = repository.load_bundle(original.manifest.dataset_id)
    assert repository.import_bundle(loaded).status == "noop"
    # The canonical dataset retains its original supporting evidence; the
    # whitespace-only attempt remains separately available in the audit tables.
    assert loaded == original
    assert repository.count_raw_versions(original.transactions[0].transaction_id) == 2
    with connect(repository.dsn) as connection:
        # Every attempt's complete evidence remains available in the audit tables.
        assert connection.execute("SELECT count(*) FROM raw_history_records").fetchone() == (
            3 * len(original.raw_records),
        )


def test_subset_noop_cannot_truncate_the_accepted_dataset(repository: HistoryRepository) -> None:
    original = load_history(FIXTURE)
    repository.import_bundle(original)
    subset = original.model_copy(update={
        "transactions": original.transactions[:1],
        "raw_records": tuple(
            raw for raw in original.raw_records
            if raw.source_name != "transactions.jsonl"
            or raw.payload["transaction_id"] == original.transactions[0].transaction_id
        ),
    })
    assert repository.import_bundle(subset).status == "noop"
    loaded = repository.load_bundle(original.manifest.dataset_id)
    assert loaded == original
    assert repository.import_bundle(loaded).status == "noop"


def test_additive_import_with_reused_line_numbers_exports_reimportable_complete_history(
    repository: HistoryRepository,
) -> None:
    original = load_history(FIXTURE)
    repository.import_bundle(original)
    extra = original.transactions[-1].model_copy(update={"transaction_id": "synthetic-additive"})
    payload = extra.model_dump(mode="json")
    evidence = RawHistoryRecord(
        source_name="transactions.jsonl", source_line=1, payload=payload,
        source_hash=hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
    )
    additive = original.model_copy(update={
        "transactions": (extra,), "anchors": (), "paydays": (), "commitments": (),
        "raw_records": (evidence,),
    })
    assert repository.import_bundle(additive).canonical_transactions_created == 1
    loaded = repository.load_bundle(original.manifest.dataset_id)
    assert {row.transaction_id for row in loaded.transactions} == {
        *(row.transaction_id for row in original.transactions), extra.transaction_id,
    }
    assert loaded.anchors == original.anchors
    assert loaded.paydays == original.paydays
    assert loaded.commitments == original.commitments
    assert loaded.manifest == original.manifest
    assert len({(raw.source_name, raw.source_line) for raw in loaded.raw_records}) == len(
        loaded.raw_records
    )
    assert {raw.source_hash for raw in loaded.raw_records} == {
        *(raw.source_hash for raw in original.raw_records), evidence.source_hash,
    }
    assert repository.import_bundle(loaded).status == "noop"
    assert repository.load_bundle(original.manifest.dataset_id) == loaded
    assert repository.count_raw_versions(extra.transaction_id) == 1
