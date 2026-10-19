"""Deterministic Gate A evidence verification and model-readiness decision."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Literal
from zoneinfo import ZoneInfo

from safe_to_save.balances import CoverageError, reconstruct_balance_minor
from safe_to_save.baselines import (
    BASELINE_VERSION,
    ComparablePositionBaseline,
    ConservativeRulesBaseline,
    TrailingMedianBaseline,
)
from safe_to_save.contracts import HistoryBundle
from safe_to_save.features import FEATURE_VERSION, CompletedExample, WithheldExample
from safe_to_save.gate_config import GateAConfig
from safe_to_save.gate_prepare import SourceProvenanceError, validate_source_provenance
from safe_to_save.paycycles import ReviewSchedule
from safe_to_save.replay import (
    ReplayObservation,
    actual_balance_path,
    replay_one,
    withheld_observation,
)
from safe_to_save.report import render_json, render_markdown
from safe_to_save.workflow import build_examples

GATE_DECISION_VERSION = "gate-a-v1"
_SCENARIOS = ("lower", "primary", "higher")
_BASELINES = ("comparable_position", "conservative_rules", "trailing_median")
_HISTORY_FILES = (
    "manifest.json", "transactions.jsonl", "anchors.jsonl", "paydays.jsonl",
    "commitments.jsonl",
)
_REPORTS = ("baseline-report.json", "baseline-report.md")
_WARM_UP_REASONS = {
    "insufficient_trailing_history",
    "fewer_than_three_earlier_cycles",
    "fewer_than_three_comparable_cycles",
}
_SCHEDULE = ReviewSchedule(weekday=0, local_time=time(6), timezone="Europe/London")


class GateDecision(StrEnum):
    FIX_DATA_OR_POLICY = "fix_data_or_policy"
    COLLECT_MORE_EVIDENCE = "collect_more_evidence"
    PROCEED_TO_MODELLING = "proceed_to_modelling"


@dataclass(frozen=True)
class CanonicalFileEvidence:
    name: str
    sha256: str
    row_count: int


@dataclass(frozen=True)
class BaselineEvidence:
    name: str
    observations: tuple[ReplayObservation, ...]


@dataclass(frozen=True)
class ScenarioEvidence:
    name: Literal["lower", "primary", "higher"]
    baselines: tuple[BaselineEvidence, ...]
    json_sha256: str
    markdown_sha256: str
    input_sha256: str | None = None
    report_identity: tuple[tuple[str, object], ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class GateEvidence:
    preparation_sha256: str
    evaluation_sha256: str
    canonical_input_sha256: str | None
    canonical_files: tuple[CanonicalFileEvidence, ...]
    config_schema_version: str
    account_id_sha256: str | None
    scenarios: tuple[ScenarioEvidence, ...]
    integrity_errors: tuple[str, ...] = ()
    policy_sha256: str | None = None
    preparation_evidence: Mapping[str, object] | None = field(
        default=None, repr=False, compare=False,
    )

    def scenario(self, name: str) -> ScenarioEvidence:
        return next(row for row in self.scenarios if row.name == name)

    def replace_scenario(self, scenario: ScenarioEvidence) -> "GateEvidence":
        return replace(self, scenarios=tuple(
            scenario if row.name == scenario.name else row for row in self.scenarios
        ))


@dataclass(frozen=True)
class DaysToPaydaySummary:
    days: int
    scored: int
    shortfalls: int


@dataclass(frozen=True)
class BaselineAssessment:
    name: str
    eligible_checkpoints: int
    scored_checkpoints: int
    coverage_ratio: Decimal
    shortfall_rate: Decimal
    useful_yield_rate: Decimal
    days_to_payday: tuple[DaysToPaydaySummary, ...]


@dataclass(frozen=True)
class ScenarioAssessment:
    name: str
    baselines: tuple[BaselineAssessment, ...]


@dataclass(frozen=True)
class SensitivityFlag:
    scenario: str
    baseline: str
    metric: str


@dataclass(frozen=True)
class GateAssessment:
    decision_version: str
    decision: GateDecision
    validation_state: str
    reason_codes: tuple[str, ...]
    complete_months: int
    completed_payday_cycles: int
    distinct_realised_targets: int
    scenarios: tuple[ScenarioAssessment, ...]
    sensitivity_flags: tuple[SensitivityFlag, ...]
    evidence_hashes: tuple[tuple[str, str], ...]
    limitations: tuple[str, ...]


_LIMITATIONS = (
    "This is model-readiness evidence, not approval for live financial use.",
    "Baseline performance is retrospective and cannot guarantee realised financial safety.",
    "Sensitivity scenarios are diagnostic evidence and do not change the primary decision.",
)


def _sha256(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _parse_observation(payload: dict) -> ReplayObservation:
    allowed = set(ReplayObservation.__dataclass_fields__)
    if set(payload) != allowed:
        raise ValueError
    values = dict(payload)
    values["checkpoint_at"] = datetime.fromisoformat(values["checkpoint_at"])
    if values["checkpoint_at"].tzinfo is None:
        raise ValueError
    if values["cycle_payday"] is not None:
        values["cycle_payday"] = date.fromisoformat(values["cycle_payday"])
    observation = ReplayObservation(**values)
    scored_values = (
        observation.forecast_minor, observation.forecast_upper_minor,
        observation.actual_variable_spend_minor, observation.recommended_minor,
        observation.minimum_balance_after_saving_minor, observation.hindsight_safe_minor,
        observation.reversal_required_minor,
    )
    if observation.status == "scored":
        if (observation.cycle_payday is None or observation.withheld_reason is not None
                or any(type(value) is not int for value in scored_values)
                or type(observation.shortfall_violation) is not bool):
            raise ValueError
    elif observation.status == "withheld":
        if (not isinstance(observation.withheld_reason, str)
                or not observation.withheld_reason or any(value is not None for value in scored_values)
                or observation.shortfall_violation is not None):
            raise ValueError
    else:
        raise ValueError
    return observation


def load_gate_evidence(output: Path) -> GateEvidence:
    """Load observations only after all copied manifest and report hashes verify."""
    try:
        preparation_bytes = (output / "preparation-manifest.json").read_bytes()
        evaluation_bytes = (output / "evaluation-manifest.json").read_bytes()
        preparation = json.loads(preparation_bytes)
        evaluation = json.loads(evaluation_bytes)
        if not isinstance(preparation, dict) or not isinstance(evaluation, dict):
            raise TypeError
        if preparation.get("schema_version") != "gate-a-preparation-v1":
            raise ValueError
        if evaluation.get("schema_version") != "gate-a-evaluation-v1":
            raise ValueError
        if evaluation.get("preparation_sha256") != _sha256(preparation_bytes):
            raise ValueError

        files = preparation.get("files")
        if not isinstance(files, dict) or set(files) != set(_HISTORY_FILES):
            raise ValueError
        canonical_files = []
        for name in _HISTORY_FILES:
            row = files[name]
            if (not isinstance(row, dict) or set(row) != {"sha256", "row_count"}
                    or not _is_sha256(row["sha256"])
                    or type(row["row_count"]) is not int or row["row_count"] < 0):
                raise TypeError
            canonical_files.append(CanonicalFileEvidence(name, row["sha256"], row["row_count"]))
        if not _is_sha256(preparation.get("canonical_input_sha256")):
            raise ValueError
        binding = preparation.get("account_binding")
        if not isinstance(binding, dict) or not _is_sha256(binding.get("account_id_sha256")):
            raise ValueError
        source = preparation.get("source")
        auxiliary = preparation.get("auxiliary_sources")
        transforms = preparation.get("transforms")
        if (not isinstance(source, dict) or not _is_sha256(source.get("sha256"))
                or not isinstance(source.get("line_hashes"), list)
                or not all(_is_sha256(value) for value in source["line_hashes"])
                or type(source.get("row_count")) is not int or source["row_count"] < 0):
            raise ValueError
        if (not isinstance(auxiliary, dict)
                or not {"anchors", "paydays", "commitments"} <= set(auxiliary)
                <= {"anchors", "paydays", "commitments", "overrides"}
                or not all(isinstance(row, dict) and _is_sha256(row.get("sha256"))
                           and type(row.get("row_count")) is int and row["row_count"] >= 0
                           for row in auxiliary.values())):
            raise ValueError
        if transforms != {"canonical": "gate-a-canonical-v1", "monzo": "monzo-csv-v1"}:
            raise ValueError
        if (binding.get("basis") != "operator_attested_config"
                or type(binding.get("source_scope_verifiable")) is not bool
                or type(binding.get("applies_to_transaction_count")) is not int
                or binding["applies_to_transaction_count"] != source["row_count"]
                or files["transactions.jsonl"]["row_count"] != source["row_count"]
                or files["manifest.json"]["row_count"] != 1):
            raise ValueError
        if not _is_sha256(evaluation.get("policy_sha256")):
            raise ValueError
        if (evaluation.get("schedule") != {
                "review_hour": 6, "review_weekday": 0, "timezone": "Europe/London",
        } or evaluation.get("feature_version") != FEATURE_VERSION
                or evaluation.get("baseline_version") != BASELINE_VERSION):
            raise ValueError

        scenario_rows = evaluation.get("scenarios")
        if (not isinstance(scenario_rows, list)
                or [row.get("name") for row in scenario_rows] != list(_SCENARIOS)):
            raise ValueError
        captured: list[tuple[str, bytes, bytes]] = []
        for row in scenario_rows:
            reports = row.get("reports")
            if not isinstance(reports, dict) or set(reports) != set(_REPORTS):
                raise ValueError
            json_bytes = (output / row["name"] / _REPORTS[0]).read_bytes()
            markdown_bytes = (output / row["name"] / _REPORTS[1]).read_bytes()
            if (_sha256(json_bytes) != reports[_REPORTS[0]]
                    or _sha256(markdown_bytes) != reports[_REPORTS[1]]):
                raise ValueError
            captured.append((row["name"], json_bytes, markdown_bytes))

        scenarios = []
        for name, json_bytes, markdown_bytes in captured:
            report = json.loads(json_bytes)
            if not isinstance(report, dict) or not isinstance(report.get("observations"), list):
                raise TypeError
            metadata = report.get("metadata")
            metadata_keys = {
                "dataset_id", "data_label", "coverage_start", "coverage_end",
                "review_weekday", "review_hour", "timezone", "floor_minor",
                "feature_version", "baseline_version", "input_sha256",
            }
            if (not isinstance(metadata, dict) or set(metadata) != metadata_keys
                    or not _is_sha256(metadata.get("input_sha256"))):
                raise ValueError
            observations = tuple(_parse_observation(row) for row in report["observations"])
            grouped: dict[str, list[ReplayObservation]] = defaultdict(list)
            for observation in observations:
                grouped[observation.baseline_name].append(observation)
            if tuple(sorted(grouped)) != _BASELINES:
                raise ValueError
            scenarios.append(ScenarioEvidence(
                name,
                tuple(BaselineEvidence(baseline, tuple(grouped[baseline]))
                      for baseline in sorted(grouped)),
                _sha256(json_bytes), _sha256(markdown_bytes),
                metadata["input_sha256"], tuple(sorted(metadata.items())),
            ))
        frozen_preparation = _freeze_json(preparation)
        if not isinstance(frozen_preparation, Mapping):  # pragma: no cover - checked above
            raise TypeError
        return GateEvidence(
            _sha256(preparation_bytes), _sha256(evaluation_bytes),
            preparation["canonical_input_sha256"], tuple(canonical_files),
            preparation.get("config_schema_version", ""), binding["account_id_sha256"],
            tuple(scenarios), policy_sha256=evaluation["policy_sha256"],
            preparation_evidence=frozen_preparation,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError, KeyError,
            AttributeError):
        raise ValueError("invalid Gate A evidence") from None


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _canonical_contents(bundle: HistoryBundle) -> dict[str, bytes]:
    def rows(values) -> bytes:
        return b"".join(_json_bytes(row.model_dump(mode="json")) for row in values)
    return {
        "manifest.json": _json_bytes(bundle.manifest.model_dump(mode="json")),
        "transactions.jsonl": rows(bundle.transactions),
        "anchors.jsonl": rows(bundle.anchors),
        "paydays.jsonl": rows(bundle.paydays),
        "commitments.jsonl": rows(bundle.commitments),
    }


def _canonical_input_hash(contents: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in _HISTORY_FILES:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(contents[name])
    return digest.hexdigest()


def _policy_hash(config: GateAConfig) -> str:
    return _sha256(json.dumps(
        {name: getattr(config.floors, name) for name in _SCENARIOS},
        sort_keys=True, separators=(",", ":"),
    ).encode())


def _expected_report_identity(
    config: GateAConfig, bundle: HistoryBundle, scenario: str,
) -> tuple[tuple[str, object], ...]:
    metadata = {
        "dataset_id": config.dataset_id,
        "data_label": "Private evaluation",
        "coverage_start": bundle.manifest.coverage_start.isoformat(),
        "coverage_end": bundle.manifest.coverage_end.isoformat(),
        "review_weekday": 0,
        "review_hour": 6,
        "timezone": "Europe/London",
        "floor_minor": getattr(config.floors, scenario),
        "feature_version": FEATURE_VERSION,
        "baseline_version": BASELINE_VERSION,
    }
    source = {
        "history": bundle.model_dump(mode="json", exclude={"raw_records"}),
        "configuration": metadata,
    }
    metadata["input_sha256"] = _sha256(json.dumps(
        source, sort_keys=True, separators=(",", ":"),
    ).encode())
    return tuple(sorted(metadata.items()))


def _integrity_errors(
    config: GateAConfig, bundle: HistoryBundle, evidence: GateEvidence,
) -> tuple[str, ...]:
    errors = ["evidence_integrity_failed"] if evidence.integrity_errors else []
    london = ZoneInfo("Europe/London")
    expected_start = datetime.combine(config.period_start, time.min, london).astimezone(UTC)
    expected_end = datetime.combine(config.period_end_exclusive, time.min, london).astimezone(UTC)
    if (bundle.manifest.dataset_id != config.dataset_id
            or bundle.manifest.account_id != config.account_id
            or bundle.manifest.coverage_start != expected_start
            or bundle.manifest.coverage_end != expected_end):
        errors.append("history_identity_or_period_mismatch")
    if evidence.policy_sha256 is not None and evidence.policy_sha256 != _policy_hash(config):
        errors.append("policy_hash_mismatch")
    if evidence.config_schema_version != config.schema_version:
        errors.append("configuration_schema_mismatch")
    if (evidence.account_id_sha256 is not None
            and evidence.account_id_sha256 != _sha256(config.account_id.encode())):
        errors.append("account_binding_mismatch")
    if evidence.canonical_input_sha256 is not None:
        contents = _canonical_contents(bundle)
        expected = tuple(
            CanonicalFileEvidence(name, _sha256(contents[name]),
                                  1 if name == "manifest.json" else len(contents[name].splitlines()))
            for name in _HISTORY_FILES
        )
        if (evidence.canonical_files != expected
                or evidence.canonical_input_sha256 != _canonical_input_hash(contents)):
            errors.append("canonical_evidence_mismatch")
    try:
        if tuple(row.name for row in evidence.scenarios) != _SCENARIOS:
            errors.append("scenario_evidence_invalid")
        baseline_sets = [tuple(row.name for row in scenario.baselines)
                         for scenario in evidence.scenarios]
        if (not baseline_sets or baseline_sets[0] != _BASELINES
                or any(names != baseline_sets[0] for names in baseline_sets[1:])):
            errors.append("scenario_evidence_invalid")
        for scenario in evidence.scenarios:
            if (scenario.report_identity
                    and scenario.report_identity != _expected_report_identity(
                        config, bundle, scenario.name,
                    )):
                errors.append("scenario_report_identity_mismatch")
        reference = {
            baseline.name: tuple(
                (row.checkpoint_at, row.cycle_payday, row.status, row.withheld_reason,
                 row.actual_variable_spend_minor)
                for row in baseline.observations
            )
            for baseline in evidence.scenario("primary").baselines
        }
        for scenario in evidence.scenarios:
            if scenario.name != "primary" and any(
                tuple((row.checkpoint_at, row.cycle_payday, row.status, row.withheld_reason,
                       row.actual_variable_spend_minor) for row in baseline.observations)
                != reference[baseline.name]
                for baseline in scenario.baselines
            ):
                errors.append("scenario_observations_inconsistent")
    except (AttributeError, KeyError, StopIteration, TypeError):
        errors.append("scenario_evidence_invalid")
    return tuple(dict.fromkeys(errors))


def _ratio(numerator: int, denominator: int) -> Decimal:
    return Decimal(numerator) / Decimal(denominator) if denominator else Decimal(0)


def _baseline_assessment(
    evidence: BaselineEvidence, bundle: HistoryBundle, primary_floor: int,
) -> BaselineAssessment:
    eligible = []
    for row in evidence.observations:
        if row.withheld_reason in _WARM_UP_REASONS:
            continue
        balance = reconstruct_balance_minor(
            row.checkpoint_at, bundle.anchors, bundle.transactions, bundle.manifest,
        )
        if balance >= primary_floor:
            eligible.append(row)
    scored = [row for row in eligible if row.status == "scored"]
    shortfalls = sum(row.shortfall_violation is True for row in scored)
    useful = sum(min(row.recommended_minor or 0, row.hindsight_safe_minor or 0) for row in scored)
    hindsight = sum(row.hindsight_safe_minor or 0 for row in scored)
    grouped: dict[int, list[ReplayObservation]] = defaultdict(list)
    for row in scored:
        grouped[(row.cycle_payday - row.checkpoint_at.date()).days].append(row)
    days = tuple(DaysToPaydaySummary(
        value, len(rows), sum(row.shortfall_violation is True for row in rows),
    ) for value, rows in sorted(grouped.items()))
    return BaselineAssessment(
        evidence.name, len(eligible), len(scored), _ratio(len(scored), len(eligible)),
        _ratio(shortfalls, len(scored)), _ratio(useful, hindsight), days,
    )


def _recompute_observations(
    config: GateAConfig, bundle: HistoryBundle,
) -> dict[str, tuple[ReplayObservation, ...]]:
    """Rebuild all replay rows from verified history without database writes."""
    examples = build_examples(bundle, _SCHEDULE)
    results = {}
    for scenario in _SCENARIOS:
        baselines = (
            TrailingMedianBaseline(), ComparablePositionBaseline(),
            ConservativeRulesBaseline(),
        )
        history: list[CompletedExample] = []
        observations = []
        for example in examples:
            for baseline in baselines:
                if isinstance(example, WithheldExample):
                    observations.append(withheld_observation(
                        example, baseline.name, example.reason,
                    ))
                    continue
                forecast = baseline.predict(example, history)
                path = actual_balance_path(
                    example, bundle.transactions, bundle.manifest.coverage_start,
                    bundle.manifest.coverage_end,
                )
                observations.append(replay_one(
                    example, forecast, path, getattr(config.floors, scenario),
                ))
            if isinstance(example, CompletedExample):
                history.append(example)
        results[scenario] = tuple(observations)
    return results


def _replay_integrity_errors(
    config: GateAConfig, bundle: HistoryBundle, evidence: GateEvidence,
) -> tuple[str, ...]:
    expected = _recompute_observations(config, bundle)
    for scenario in evidence.scenarios:
        saved = tuple(
            row for baseline in scenario.baselines for row in baseline.observations
        )
        expected_rows = expected[scenario.name]
        saved_by_key = {(row.checkpoint_at, row.baseline_name): row for row in saved}
        expected_by_key = {
            (row.checkpoint_at, row.baseline_name): row for row in expected_rows
        }
        if (len(saved_by_key) != len(saved) or len(expected_by_key) != len(expected_rows)
                or saved_by_key != expected_by_key):
            return ("replay_evidence_mismatch",)
    return ()


def _rendered_report_integrity_errors(evidence: GateEvidence) -> tuple[str, ...]:
    for scenario in evidence.scenarios:
        if not scenario.report_identity:
            return ("rendered_report_mismatch",)
        metadata = dict(scenario.report_identity)
        observations = tuple(
            row for baseline in scenario.baselines for row in baseline.observations
        )
        expected_json = render_json(metadata, observations).encode()
        expected_markdown = render_markdown(metadata, observations).encode()
        if (_sha256(expected_json) != scenario.json_sha256
                or _sha256(expected_markdown) != scenario.markdown_sha256):
            return ("rendered_report_mismatch",)
    return ()


def _source_provenance_integrity_errors(
    config: GateAConfig, bundle: HistoryBundle, evidence: GateEvidence,
) -> tuple[str, ...]:
    if evidence.preparation_evidence is None:
        return ("source_provenance_mismatch",)
    try:
        validate_source_provenance(config, bundle, evidence.preparation_evidence)
    except SourceProvenanceError:
        return ("source_provenance_mismatch",)
    return ()


def _policy_evaluable_checkpoint_count(
    config: GateAConfig, bundle: HistoryBundle, evidence: GateEvidence,
) -> int:
    primary = evidence.scenario("primary")
    checkpoints = {
        row.checkpoint_at for baseline in primary.baselines
        for row in baseline.observations if row.cycle_payday is not None
    }
    return sum(
        reconstruct_balance_minor(
            checkpoint, bundle.anchors, bundle.transactions, bundle.manifest,
        ) >= config.floors.primary
        for checkpoint in checkpoints
    )


def _failed_assessment(errors: tuple[str, ...], evidence: GateEvidence) -> GateAssessment:
    return GateAssessment(
        GATE_DECISION_VERSION, GateDecision.FIX_DATA_OR_POLICY, "invalid", errors,
        0, 0, 0, (), (), (("preparation_manifest", evidence.preparation_sha256),
                            ("evaluation_manifest", evidence.evaluation_sha256)), _LIMITATIONS,
    )


def assess_gate(
    config: GateAConfig, bundle: HistoryBundle, evidence: GateEvidence,
) -> GateAssessment:
    """Apply pinned thresholds; only the primary scenario controls the decision."""
    integrity = _integrity_errors(config, bundle, evidence)
    if integrity:
        return _failed_assessment(integrity, evidence)
    provenance_integrity = _source_provenance_integrity_errors(config, bundle, evidence)
    if provenance_integrity:
        return _failed_assessment(provenance_integrity, evidence)
    try:
        replay_integrity = _replay_integrity_errors(config, bundle, evidence)
    except (CoverageError, ValueError, TypeError):
        return _failed_assessment(("replay_evidence_invalid",), evidence)
    if replay_integrity:
        return _failed_assessment(replay_integrity, evidence)
    rendered_integrity = _rendered_report_integrity_errors(evidence)
    if rendered_integrity:
        return _failed_assessment(rendered_integrity, evidence)
    try:
        policy_evaluable = _policy_evaluable_checkpoint_count(config, bundle, evidence)
    except (CoverageError, ValueError, TypeError):
        return _failed_assessment(("history_balance_evidence_invalid",), evidence)
    if policy_evaluable < 12:
        return _failed_assessment(("primary_floor_policy_unsuitable",), evidence)
    try:
        scenarios = tuple(ScenarioAssessment(
            scenario.name,
            tuple(_baseline_assessment(row, bundle, config.floors.primary)
                  for row in scenario.baselines),
        ) for scenario in evidence.scenarios)
    except (CoverageError, ValueError, TypeError):
        return _failed_assessment(("history_balance_evidence_invalid",), evidence)

    primary = next(row for row in scenarios if row.name == "primary")
    primary_evidence = evidence.scenario("primary")
    complete_months = (
        (config.period_end_exclusive.year - config.period_start.year) * 12
        + config.period_end_exclusive.month - config.period_start.month
    )
    completed_cycles = len({
        row.cycle_payday for baseline in primary_evidence.baselines
        for row in baseline.observations if row.cycle_payday is not None
    })
    targets = {
        row.actual_variable_spend_minor for baseline in primary_evidence.baselines
        for row in baseline.observations
        if row.status == "scored" and row.actual_variable_spend_minor is not None
    }
    reasons = []
    if complete_months < 6:
        reasons.append("insufficient_complete_months")
    if completed_cycles < 6:
        reasons.append("insufficient_completed_payday_cycles")
    if any(row.scored_checkpoints < 12 for row in primary.baselines):
        reasons.append("insufficient_scored_primary_checkpoints")
    if any(row.coverage_ratio < Decimal("0.70") for row in primary.baselines):
        reasons.append("insufficient_post_warm_up_coverage")
    if len(targets) < 3:
        reasons.append("insufficient_distinct_realised_targets")

    primary_by_name = {row.name: row for row in primary.baselines}
    flags = []
    for scenario in scenarios:
        if scenario.name == "primary":
            continue
        for baseline in scenario.baselines:
            reference = primary_by_name[baseline.name]
            if abs(baseline.shortfall_rate - reference.shortfall_rate) >= Decimal("0.05"):
                flags.append(SensitivityFlag(scenario.name, baseline.name, "shortfall_rate"))
            if abs(baseline.useful_yield_rate - reference.useful_yield_rate) >= Decimal("0.10"):
                flags.append(SensitivityFlag(scenario.name, baseline.name, "useful_yield_rate"))
    hashes = [("preparation_manifest", evidence.preparation_sha256),
              ("evaluation_manifest", evidence.evaluation_sha256)]
    for scenario in evidence.scenarios:
        hashes.extend(((f"{scenario.name}_json", scenario.json_sha256),
                       (f"{scenario.name}_markdown", scenario.markdown_sha256)))
    decision = (GateDecision.COLLECT_MORE_EVIDENCE if reasons
                else GateDecision.PROCEED_TO_MODELLING)
    return GateAssessment(
        GATE_DECISION_VERSION, decision, "validated",
        tuple(reasons) or ("all_readiness_thresholds_met",), complete_months,
        completed_cycles, len(targets), scenarios, tuple(flags), tuple(hashes), _LIMITATIONS,
    )
