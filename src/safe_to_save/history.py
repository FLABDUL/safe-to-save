import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from safe_to_save.contracts import (
    BalanceAnchor,
    CanonicalTransaction,
    Commitment,
    HistoryBundle,
    HistoryEvidence,
    HistoryManifest,
    Payday,
    RawHistoryRecord,
)

_REQUIRED_FILES = (
    "manifest.json",
    "transactions.jsonl",
    "anchors.jsonl",
    "paydays.jsonl",
    "commitments.jsonl",
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class HistoryParseError(ValueError):
    """An offline history file could not be parsed deterministically."""


class HistoryConflictError(ValueError):
    """One provider transaction ID maps to multiple canonical records."""

    def __init__(
        self, transaction_id: str, raw_records: tuple[RawHistoryRecord, ...],
        evidence: HistoryEvidence,
    ) -> None:
        self.transaction_id = transaction_id
        self.raw_records = raw_records
        self.evidence = evidence
        super().__init__(f"conflicting canonical records for transaction {transaction_id!r}")


def _read_manifest(path: Path) -> tuple[HistoryManifest, RawHistoryRecord]:
    source = path.read_bytes()
    try:
        payload = json.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoryParseError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HistoryParseError(f"{path.name}: expected a JSON object")
    return HistoryManifest.model_validate(payload), RawHistoryRecord(
        source_name=path.name, source_line=1, payload=payload,
        source_hash=hashlib.sha256(source).hexdigest(),
    )


def _read_jsonl(
    path: Path, model: Callable[[dict[str, object]], ModelT]
) -> tuple[tuple[ModelT, RawHistoryRecord], ...]:
    parsed: list[tuple[ModelT, RawHistoryRecord]] = []
    for line_number, line_bytes in enumerate(path.read_bytes().splitlines(keepends=True), 1):
        try:
            text = line_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HistoryParseError(
                f"{path.name} line {line_number}: invalid UTF-8"
            ) from exc
        if not text.strip():
            raise HistoryParseError(f"{path.name} line {line_number}: blank lines are not allowed")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HistoryParseError(
                f"{path.name} line {line_number}: malformed JSON: {exc.msg}"
            ) from exc
        if not isinstance(payload, dict):
            raise HistoryParseError(
                f"{path.name} line {line_number}: expected a JSON object"
            )
        raw = RawHistoryRecord(
            source_name=path.name,
            source_line=line_number,
            payload=payload,
            source_hash=hashlib.sha256(line_bytes).hexdigest(),
        )
        parsed.append((model(payload), raw))
    return tuple(parsed)


def _model_parser(model_type: type[ModelT]) -> Callable[[dict[str, object]], ModelT]:
    return model_type.model_validate


def _canonical_transactions(
    parsed: tuple[tuple[CanonicalTransaction, RawHistoryRecord], ...],
    evidence: HistoryEvidence,
) -> tuple[CanonicalTransaction, ...]:
    by_id: dict[str, tuple[str, CanonicalTransaction, list[RawHistoryRecord]]] = {}
    for transaction, raw in parsed:
        fingerprint = json.dumps(
            transaction.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        current = by_id.get(transaction.transaction_id)
        if current is None:
            by_id[transaction.transaction_id] = (fingerprint, transaction, [raw])
            continue
        current_fingerprint, _, raw_records = current
        raw_records.append(raw)
        if fingerprint != current_fingerprint:
            raise HistoryConflictError(transaction.transaction_id, tuple(raw_records), evidence)
    return tuple(sorted((item[1] for item in by_id.values()), key=lambda row: row.occurred_at))


def load_history(directory: Path) -> HistoryBundle:
    """Load the five-file offline history contract from ``directory``."""

    paths = {name: directory / name for name in _REQUIRED_FILES}
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"required history file is missing: {name}")

    manifest, manifest_raw = _read_manifest(paths["manifest.json"])
    transaction_rows = _read_jsonl(
        paths["transactions.jsonl"], _model_parser(CanonicalTransaction)
    )
    anchor_rows = _read_jsonl(paths["anchors.jsonl"], _model_parser(BalanceAnchor))
    payday_rows = _read_jsonl(paths["paydays.jsonl"], _model_parser(Payday))
    commitment_rows = _read_jsonl(paths["commitments.jsonl"], _model_parser(Commitment))

    raw_records = tuple(
        raw
        for rows in (transaction_rows, anchor_rows, payday_rows, commitment_rows)
        for _, raw in rows
    )
    return HistoryBundle(
        manifest=manifest,
        raw_records=raw_records,
        transactions=_canonical_transactions(transaction_rows, HistoryEvidence(
            manifest=manifest, raw_records=(manifest_raw, *raw_records),
        )),
        anchors=tuple(sorted((row for row, _ in anchor_rows), key=lambda row: row.captured_at)),
        paydays=tuple(sorted((row for row, _ in payday_rows), key=lambda row: row.payday)),
        commitments=tuple(row for row, _ in commitment_rows),
    )
