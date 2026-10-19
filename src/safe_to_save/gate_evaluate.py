"""Atomically coordinate the private three-floor Gate A replay."""

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from safe_to_save.baselines import BASELINE_VERSION
from safe_to_save.features import FEATURE_VERSION
from safe_to_save.gate_config import GateAConfig
from safe_to_save.gate_prepare import (
    validate_anchor_reconciliation,
    validate_source_provenance,
)
from safe_to_save.history import load_history
from safe_to_save.paycycles import ReviewSchedule
from safe_to_save.repository import HistoryRepository
from safe_to_save.workflow import run_backtest

_HISTORY_FILES = (
    "manifest.json",
    "transactions.jsonl",
    "anchors.jsonl",
    "paydays.jsonl",
    "commitments.jsonl",
)
_SCENARIOS = ("lower", "primary", "higher")
_REPORTS = ("baseline-report.json", "baseline-report.md")
_SCHEDULE = ReviewSchedule(weekday=0, local_time=time(6), timezone="Europe/London")


@dataclass(frozen=True)
class GateScenarioResult:
    name: Literal["lower", "primary", "higher"]
    status: str
    report_sha256: tuple[tuple[str, str], ...]
    input_sha256: str
    output_directory: Path = field(repr=False)


@dataclass(frozen=True)
class GateEvaluationResult:
    status: Literal["completed", "noop", "fix_data_or_policy"]
    scenarios: tuple[GateScenarioResult, ...] = ()
    preparation_sha256: str | None = None
    reason_code: str | None = None
    output_directory: Path | None = field(default=None, repr=False)


def _sha256(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def _canonical_input_sha256(contents: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in _HISTORY_FILES:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(contents[name])
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_preparation_metadata(
    manifest: dict, config: GateAConfig, transaction_count: int,
) -> bool:
    source = manifest.get("source")
    auxiliary = manifest.get("auxiliary_sources")
    transforms = manifest.get("transforms")
    binding = manifest.get("account_binding")
    required_auxiliary = {"anchors", "paydays", "commitments"}
    allowed_auxiliary = required_auxiliary | {"overrides"}
    if not isinstance(source, dict) or not isinstance(auxiliary, dict):
        return False
    if not required_auxiliary <= set(auxiliary) <= allowed_auxiliary:
        return False
    if not all(
        isinstance(row, dict) and _is_sha256(row.get("sha256"))
        and type(row.get("row_count")) is int and row["row_count"] >= 0
        for row in auxiliary.values()
    ):
        return False
    line_hashes = source.get("line_hashes")
    return (
        manifest.get("config_schema_version") == config.schema_version
        and isinstance(line_hashes, list)
        and all(_is_sha256(value) for value in line_hashes)
        and _is_sha256(source.get("sha256"))
        and source.get("row_count") == transaction_count
        and transforms == {
            "canonical": "gate-a-canonical-v1", "monzo": "monzo-csv-v1",
        }
        and isinstance(binding, dict)
        and binding.get("basis") == "operator_attested_config"
        and binding.get("account_id_sha256") == _sha256(config.account_id.encode())
        and type(binding.get("source_scope_verifiable")) is bool
        and binding.get("applies_to_transaction_count") == transaction_count
    )


def _load_verified_preparation(config: GateAConfig):
    """Return immutable history only when every Task 3 evidence hash still matches."""
    directory = config.canonical_directory
    try:
        preparation_bytes = (directory / "preparation-manifest.json").read_bytes()
        manifest = json.loads(preparation_bytes)
        if not isinstance(manifest, dict) or manifest.get("schema_version") != (
            "gate-a-preparation-v1"
        ):
            raise ValueError
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != set(_HISTORY_FILES):
            raise ValueError
        contents = {name: (directory / name).read_bytes() for name in _HISTORY_FILES}
        for name, source in contents.items():
            evidence = files.get(name)
            expected_rows = 1 if name == "manifest.json" else len(source.splitlines())
            if not isinstance(evidence, dict) or evidence != {
                "sha256": _sha256(source), "row_count": expected_rows,
            }:
                raise ValueError
        if manifest.get("canonical_input_sha256") != _canonical_input_sha256(contents):
            raise ValueError
        bundle = load_history(directory)
        london = ZoneInfo("Europe/London")
        expected_start = datetime.combine(
            config.period_start, time.min, london,
        ).astimezone(UTC)
        expected_end = datetime.combine(
            config.period_end_exclusive, time.min, london,
        ).astimezone(UTC)
        if (bundle.manifest.dataset_id != config.dataset_id
                or bundle.manifest.account_id != config.account_id
                or bundle.manifest.coverage_start != expected_start
                or bundle.manifest.coverage_end != expected_end
                or not _valid_preparation_metadata(manifest, config, len(bundle.transactions))):
            raise ValueError
        validate_anchor_reconciliation(bundle)
        validate_source_provenance(config, bundle, manifest)
        # Close the read/parse window before any database write.
        if any((directory / name).read_bytes() != source for name, source in contents.items()):
            raise ValueError
        if (directory / "preparation-manifest.json").read_bytes() != preparation_bytes:
            raise ValueError
        return bundle, _sha256(preparation_bytes), preparation_bytes
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _policy_sha256(config: GateAConfig) -> str:
    source = json.dumps(
        {name: getattr(config.floors, name) for name in _SCENARIOS},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return _sha256(source)


def _identity(config: GateAConfig, preparation_sha256: str) -> dict[str, object]:
    return {
        "schema_version": "gate-a-evaluation-v1",
        "preparation_sha256": preparation_sha256,
        "policy_sha256": _policy_sha256(config),
        "schedule": {
            "review_weekday": _SCHEDULE.weekday,
            "review_hour": _SCHEDULE.local_time.hour,
            "timezone": _SCHEDULE.timezone,
        },
        "feature_version": FEATURE_VERSION,
        "baseline_version": BASELINE_VERSION,
    }


def _read_existing(target: Path, identity: dict[str, object]) -> dict | None:
    """Validate a prior generation without including its path in any error."""
    if not target.exists():
        return None
    try:
        manifest_path = target / "evaluation-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise TypeError
        if any(manifest.get(key) != value for key, value in identity.items()):
            raise ValueError
        scenarios = manifest.get("scenarios")
        if not isinstance(scenarios, list) or [row.get("name") for row in scenarios] != list(
            _SCENARIOS
        ):
            raise ValueError
        copied_preparation = target / "preparation-manifest.json"
        if (not copied_preparation.is_file()
                or _sha256(copied_preparation.read_bytes()) != identity["preparation_sha256"]):
            raise ValueError
        expected_files = {"evaluation-manifest.json", "preparation-manifest.json"}
        for row in scenarios:
            reports = row.get("reports")
            if not isinstance(reports, dict) or set(reports) != set(_REPORTS):
                raise ValueError
            name = row["name"]
            for report_name, digest in reports.items():
                source = target / name / report_name
                if not source.is_file() or _sha256(source.read_bytes()) != digest:
                    raise ValueError
                expected_files.add(f"{name}/{report_name}")
        actual_files = {
            path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()
        }
        if actual_files != expected_files:
            raise ValueError
        return manifest
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return {}


def _manifest(
    identity: dict[str, object], results: tuple[GateScenarioResult, ...],
) -> dict[str, object]:
    return {
        **identity,
        "scenarios": [
            {"name": row.name, "reports": dict(row.report_sha256)} for row in results
        ],
    }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def evaluate_gate(
    config: GateAConfig,
    repository: HistoryRepository,
) -> GateEvaluationResult:
    """Verify one prepared bundle, import it once and publish three replays."""
    verified = _load_verified_preparation(config)
    if verified is None:
        return GateEvaluationResult("fix_data_or_policy", reason_code="invalid_preparation")
    bundle, preparation_sha256, preparation_bytes = verified
    identity = _identity(config, preparation_sha256)
    existing = _read_existing(config.output_directory, identity)
    if existing == {}:
        return GateEvaluationResult(
            "fix_data_or_policy", preparation_sha256=preparation_sha256,
            reason_code="existing_evaluation_differs",
        )

    staging: Path | None = None
    stage = "initialisation"
    try:
        target = config.output_directory
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
        stage = "history import"
        repository.import_bundle(bundle)
        scenario_results = []
        for name in _SCENARIOS:
            stage = f"{name} scenario"
            workflow = run_backtest(
                repository, config.dataset_id, _SCHEDULE, getattr(config.floors, name),
                "Private evaluation", staging / name,
            )
            report_hashes = tuple(
                (report_name, _sha256((workflow.output_directory / report_name).read_bytes()))
                for report_name in _REPORTS
            )
            scenario_results.append(GateScenarioResult(
                name, workflow.status, report_hashes, workflow.input_sha256, target / name,
            ))
        results = tuple(scenario_results)
        stage = "manifest publication"
        manifest_bytes = _json_bytes(_manifest(identity, results))
        (staging / "preparation-manifest.json").write_bytes(preparation_bytes)
        (staging / "evaluation-manifest.json").write_bytes(manifest_bytes)

        if existing is None:
            staging.rename(target)
            staging = None
            status = "completed"
        else:
            generated = {
                path.relative_to(staging).as_posix(): path.read_bytes()
                for path in staging.rglob("*") if path.is_file()
            }
            prior = {
                path.relative_to(target).as_posix(): path.read_bytes()
                for path in target.rglob("*") if path.is_file()
            }
            if generated != prior:
                return GateEvaluationResult(
                    "fix_data_or_policy", preparation_sha256=preparation_sha256,
                    reason_code="existing_evaluation_differs",
                )
            status = "noop"
        return GateEvaluationResult(status, results, preparation_sha256, output_directory=target)
    except Exception as exc:  # noqa: BLE001 - this privacy boundary must redact failures
        raise RuntimeError(
            f"Gate A evaluation failed safely during {stage} ({type(exc).__name__})"
        ) from None
    finally:
        if staging is not None and staging.exists():
            try:
                shutil.rmtree(staging)
            except OSError:
                pass
