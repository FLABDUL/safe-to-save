"""Private, atomic preparation of the five-file canonical history contract."""

import hashlib
import json
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time
from itertools import pairwise
from pathlib import Path
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from safe_to_save.balances import CoverageError
from safe_to_save.contracts import (
    BalanceAnchor,
    Commitment,
    HistoryBundle,
    HistoryManifest,
    Payday,
)
from safe_to_save.gate_config import GateAConfig
from safe_to_save.history import load_history
from safe_to_save.monzo_export import load_overrides, parse_monzo_export

_CANONICAL_VERSION = "gate-a-canonical-v1"
_HISTORY_FILES = (
    "manifest.json",
    "transactions.jsonl",
    "anchors.jsonl",
    "paydays.jsonl",
    "commitments.jsonl",
)
ModelT = TypeVar("ModelT", bound=BaseModel)


class _ReconciliationError(CoverageError):
    """Covered anchors disagree after applying all signed flows."""


@dataclass(frozen=True)
class _CapturedSource:
    source: bytes

    def read_bytes(self) -> bytes:
        return self.source

    def read_text(self, encoding: str) -> str:
        return self.source.decode(encoding)


class SourceProvenanceError(ValueError):
    """Current private source evidence does not match canonical history."""


def _parse_monzo_snapshot(source: bytes, overrides_source: bytes | None):
    captured_overrides = (
        None if overrides_source is None else _CapturedSource(overrides_source)
    )
    overrides = load_overrides(captured_overrides)  # type: ignore[arg-type]
    parsed = parse_monzo_export(_CapturedSource(source), overrides)  # type: ignore[arg-type]
    return parsed, overrides


@dataclass(frozen=True)
class PreparationResult:
    status: Literal["completed", "noop", "fix_data_or_policy"]
    reason_code: str | None = None


def validate_anchor_reconciliation(
    bundle: HistoryBundle, *, require_boundaries: bool = True,
) -> None:
    """Require boundary anchors and reconcile all signed flows in ``(a, b]``."""

    start = bundle.manifest.coverage_start
    end = bundle.manifest.coverage_end
    anchors = tuple(sorted(
        (row for row in bundle.anchors if start <= row.captured_at <= end),
        key=lambda row: row.captured_at,
    ))
    if require_boundaries:
        london = ZoneInfo("Europe/London")
        if any(boundary.astimezone(london).time() != time.min for boundary in (start, end)):
            raise CoverageError("coverage boundaries must be exact London midnight")
        if not any(row.captured_at == start for row in anchors) or not any(
            row.captured_at == end for row in anchors
        ):
            raise CoverageError("coverage boundary anchors are required at both ends")
        if len(anchors) != len(bundle.anchors):
            raise CoverageError("balance anchor is outside covered interval")
        if any(not start < row.occurred_at < end for row in bundle.transactions):
            raise CoverageError("transaction is outside covered interval")
    for earlier, later in pairwise(anchors):
        net_flow = sum(
            row.amount_minor for row in bundle.transactions
            if earlier.captured_at < row.occurred_at <= later.captured_at
        )
        if earlier.balance_minor + net_flow != later.balance_minor:
            if earlier.captured_at == later.captured_at:
                raise _ReconciliationError("ambiguous balance anchors at the same instant")
            raise _ReconciliationError("inconsistent balance anchors across covered interval")


def _read_jsonl(source: bytes, model: type[ModelT]) -> tuple[ModelT, ...]:
    rows: list[ModelT] = []
    for line in source.splitlines():
        if not line.strip():
            raise ValueError("invalid canonical input")
        payload = json.loads(line.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("invalid canonical input")
        rows.append(model.model_validate(payload))
    return tuple(rows)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _jsonl_bytes(rows: tuple[BaseModel, ...]) -> bytes:
    return b"".join(_json_bytes(row.model_dump(mode="json")) for row in rows)


def _london_midnight(value) -> datetime:
    return datetime.combine(value, time.min, ZoneInfo("Europe/London")).astimezone(UTC)


def _file_evidence(contents: dict[str, bytes]) -> dict[str, dict[str, int | str]]:
    evidence = {}
    for name in _HISTORY_FILES:
        source = contents[name]
        evidence[name] = {
            "sha256": hashlib.sha256(source).hexdigest(),
            "row_count": 1 if name == "manifest.json" else len(source.splitlines()),
        }
    return evidence


def _canonical_input_hash(contents: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in _HISTORY_FILES:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(contents[name])
    return digest.hexdigest()


def _prepare_contents(config: GateAConfig) -> tuple[dict[str, bytes], dict[str, object]]:
    try:
        source_bytes = config.source_csv.read_bytes()
        auxiliary_bytes = {
            "anchors": config.anchors_path.read_bytes(),
            "paydays": config.paydays_path.read_bytes(),
            "commitments": config.commitments_path.read_bytes(),
        }
        if config.overrides_path is not None:
            auxiliary_bytes["overrides"] = config.overrides_path.read_bytes()
    except OSError:
        raise ValueError("canonical_input_invalid") from None

    parsed, overrides = _parse_monzo_snapshot(
        source_bytes, auxiliary_bytes.get("overrides"),
    )
    try:
        anchors = tuple(sorted(
            _read_jsonl(auxiliary_bytes["anchors"], BalanceAnchor),
            key=lambda row: row.captured_at,
        ))
        paydays = tuple(sorted(
            _read_jsonl(auxiliary_bytes["paydays"], Payday), key=lambda row: row.payday,
        ))
        commitments = tuple(sorted(
            _read_jsonl(auxiliary_bytes["commitments"], Commitment),
            key=lambda row: (row.due_at, row.commitment_id),
        ))
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError, TypeError):
        raise ValueError("canonical_input_invalid") from None

    transactions = tuple(sorted(
        parsed.transactions, key=lambda row: (row.occurred_at, row.transaction_id),
    ))
    record_provenance = {
        row.transaction_id: {
            "source_line": row.source_line,
            "source_hash": row.source_hash,
        }
        for row in parsed.record_provenance
    }
    if (len(record_provenance) != len(parsed.record_provenance)
            or record_provenance.keys() != {row.transaction_id for row in transactions}):
        raise ValueError("source_provenance_invalid")
    manifest = HistoryManifest(
        dataset_id=config.dataset_id,
        account_id=config.account_id,
        currency="GBP",
        coverage_start=_london_midnight(config.period_start),
        coverage_end=_london_midnight(config.period_end_exclusive),
    )
    contents = {
        "manifest.json": _json_bytes(manifest.model_dump(mode="json")),
        "transactions.jsonl": _jsonl_bytes(transactions),
        "anchors.jsonl": _jsonl_bytes(anchors),
        "paydays.jsonl": _jsonl_bytes(paydays),
        "commitments.jsonl": _jsonl_bytes(commitments),
    }
    evidence: dict[str, object] = {
        "schema_version": "gate-a-preparation-v1",
        "config_schema_version": config.schema_version,
        "source": {
            "sha256": parsed.source_sha256,
            "line_hashes": list(parsed.line_hashes),
            "row_count": len(transactions),
            "record_provenance": record_provenance,
        },
        "auxiliary_sources": {
            name: {
                "sha256": hashlib.sha256(source).hexdigest(),
                "row_count": {
                    "anchors": len(anchors),
                    "paydays": len(paydays),
                    "commitments": len(commitments),
                    "overrides": len(overrides),
                }[name],
            }
            for name, source in sorted(auxiliary_bytes.items())
        },
        "transforms": {"canonical": _CANONICAL_VERSION, "monzo": parsed.profile_name},
        "canonical_input_sha256": _canonical_input_hash(contents),
        "files": _file_evidence(contents),
        "account_binding": {
            "basis": "operator_attested_config",
            "account_id_sha256": hashlib.sha256(config.account_id.encode()).hexdigest(),
            "source_scope_verifiable": parsed.account_scope_verifiable,
            "applies_to_transaction_count": len(transactions),
        },
    }
    return contents, evidence


def validate_source_provenance(
    config: GateAConfig,
    bundle: HistoryBundle,
    preparation_evidence: Mapping[str, object],
) -> None:
    """Verify current private source snapshots against canonical evidence.

    Every validation failure is deliberately collapsed to one redacted error.
    """

    try:
        source_bytes = config.source_csv.read_bytes()
        overrides_bytes = (
            None if config.overrides_path is None else config.overrides_path.read_bytes()
        )
        parsed, overrides = _parse_monzo_snapshot(source_bytes, overrides_bytes)
        source_evidence = preparation_evidence["source"]
        auxiliary_evidence = preparation_evidence["auxiliary_sources"]
        account_binding = preparation_evidence["account_binding"]
        if not all(isinstance(value, Mapping) for value in (
            source_evidence, auxiliary_evidence, account_binding,
        )):
            raise TypeError

        if source_evidence.get("sha256") != parsed.source_sha256:
            raise ValueError
        override_evidence = auxiliary_evidence.get("overrides")
        if overrides_bytes is None:
            if override_evidence is not None:
                raise ValueError
        else:
            if not isinstance(override_evidence, Mapping):
                raise TypeError
            if (override_evidence.get("sha256")
                    != hashlib.sha256(overrides_bytes).hexdigest()
                    or override_evidence.get("row_count") != len(overrides)):
                raise ValueError

        bundle_by_id = {row.transaction_id: row for row in bundle.transactions}
        parsed_by_id = {row.transaction_id: row for row in parsed.transactions}
        if (len(bundle_by_id) != len(bundle.transactions)
                or len(parsed_by_id) != len(parsed.transactions)
                or bundle_by_id.keys() != parsed_by_id.keys()
                or any(
                    bundle_by_id[key].model_dump(mode="json")
                    != parsed_by_id[key].model_dump(mode="json")
                    for key in bundle_by_id
                )):
            raise ValueError
        if bundle.manifest.account_id != config.account_id:
            raise ValueError
        if (account_binding.get("basis") != "operator_attested_config"
                or account_binding.get("account_id_sha256")
                != hashlib.sha256(config.account_id.encode()).hexdigest()
                or account_binding.get("applies_to_transaction_count") != len(bundle_by_id)):
            raise ValueError

        expected = source_evidence.get("record_provenance")
        if not isinstance(expected, Mapping):
            raise TypeError
        actual = {
            row.transaction_id: {
                "source_line": row.source_line,
                "source_hash": row.source_hash,
            }
            for row in parsed.record_provenance
        }
        if (len(actual) != len(parsed.record_provenance)
                or actual.keys() != parsed_by_id.keys()
                or expected.keys() != actual.keys()
                or any(
                    not isinstance(expected[key], Mapping)
                    or set(expected[key]) != {"source_line", "source_hash"}
                    or expected[key].get("source_line") != actual[key]["source_line"]
                    or expected[key].get("source_hash") != actual[key]["source_hash"]
                    for key in actual
                )):
            raise ValueError
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        raise SourceProvenanceError("source provenance validation failed") from None


def prepare_private_history(config: GateAConfig) -> PreparationResult:
    """Build, validate and atomically publish private canonical history."""

    try:
        contents, evidence = _prepare_contents(config)
    except ValueError as exc:
        reason = str(exc)
        return PreparationResult(
            "fix_data_or_policy",
            reason if reason == "canonical_input_invalid" else "source_validation_failed",
        )

    target = config.canonical_directory
    staging: Path | None = None
    result: PreparationResult
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
        for name, source in contents.items():
            (staging / name).write_bytes(source)
        preparation_manifest = _json_bytes(evidence)
        (staging / "preparation-manifest.json").write_bytes(preparation_manifest)
        try:
            bundle = load_history(staging)
            validate_anchor_reconciliation(bundle)
        except _ReconciliationError:
            result = PreparationResult("fix_data_or_policy", "reconciliation_failed")
        except (OSError, CoverageError, ValueError, TypeError, ValidationError):
            result = PreparationResult("fix_data_or_policy", "coverage_validation_failed")
        else:
            if target.exists():
                identical = all(
                    (target / name).is_file() and (target / name).read_bytes() == source
                    for name, source in (*contents.items(),
                                         ("preparation-manifest.json", preparation_manifest))
                )
                result = PreparationResult(
                    "noop" if identical else "fix_data_or_policy",
                    None if identical else "existing_output_differs",
                )
            else:
                staging.rename(target)
                staging = None
                result = PreparationResult("completed")
    except OSError:
        result = PreparationResult("fix_data_or_policy", "output_unavailable")

    if staging is not None and staging.exists():
        try:
            shutil.rmtree(staging)
        except OSError:
            return PreparationResult("fix_data_or_policy", "staging_cleanup_failed")
        if staging.exists():
            return PreparationResult("fix_data_or_policy", "staging_cleanup_failed")
    return result
