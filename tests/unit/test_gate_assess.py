import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from safe_to_save import gate_assess as gate_assess_module
from safe_to_save.contracts import (
    BalanceAnchor,
    CanonicalTransaction,
    EconomicType,
    HistoryBundle,
    HistoryManifest,
    Payday,
)
from safe_to_save.gate_assess import (
    BaselineEvidence,
    GateDecision,
    GateEvidence,
    ScenarioEvidence,
    assess_gate,
    load_gate_evidence,
)
from safe_to_save.gate_config import FloorScenarios, GateAConfig
from safe_to_save.monzo_export import load_overrides, parse_monzo_export
from safe_to_save.replay import ReplayObservation
from safe_to_save.report import render_json, render_markdown

BASELINES = ("comparable_position", "conservative_rules", "trailing_median")


def _config(tmp_path: Path) -> GateAConfig:
    return GateAConfig(
        schema_version="gate-a-v1",
        as_of_date=date(2026, 5, 17),
        period_start=date(2025, 11, 1),
        period_end_exclusive=date(2026, 5, 1),
        dataset_id="synthetic-gate-a",
        account_id="synthetic-current",
        source_csv=tmp_path / "unused.csv",
        anchors_path=tmp_path / "unused-anchors.jsonl",
        paydays_path=tmp_path / "unused-paydays.jsonl",
        commitments_path=tmp_path / "unused-commitments.jsonl",
        canonical_directory=tmp_path / "canonical",
        output_directory=tmp_path / "evaluation",
        floors=FloorScenarios(lower=20_000, primary=30_000, higher=40_000),
    )


def _bundle(config: GateAConfig) -> HistoryBundle:
    start = datetime(2025, 11, 1, tzinfo=UTC)
    end = datetime(2026, 4, 30, 23, tzinfo=UTC)
    return HistoryBundle(
        manifest=HistoryManifest(
            dataset_id=config.dataset_id,
            account_id=config.account_id,
            currency="GBP",
            coverage_start=start,
            coverage_end=end,
        ),
        raw_records=(),
        transactions=(),
        anchors=(
            BalanceAnchor(captured_at=start, balance_minor=100_000, currency="GBP"),
            BalanceAnchor(captured_at=end, balance_minor=100_000, currency="GBP"),
        ),
        paydays=(),
        commitments=(),
    )


def _observation(index: int, baseline: str, *, shortfall: bool = False) -> ReplayObservation:
    checkpoint = datetime(2026, 1, 5, 6, tzinfo=UTC) + timedelta(days=7 * index)
    return ReplayObservation(
        checkpoint_at=checkpoint,
        cycle_payday=(checkpoint + timedelta(days=14)).date(),
        baseline_name=baseline,
        status="scored",
        withheld_reason=None,
        forecast_minor=10_000,
        forecast_upper_minor=12_000,
        actual_variable_spend_minor=(index % 3 + 1) * 1_000,
        recommended_minor=20_000,
        minimum_balance_after_saving_minor=50_000,
        hindsight_safe_minor=25_000,
        shortfall_violation=shortfall,
        reversal_required_minor=1_000 if shortfall else 0,
    )


def _evidence(*, count: int = 12, shortfalls: int = 0, integrity=()) -> GateEvidence:
    scenarios = []
    for name in ("lower", "primary", "higher"):
        baselines = tuple(
            BaselineEvidence(
                baseline,
                tuple(_observation(index, baseline, shortfall=index < shortfalls)
                      for index in range(count)),
            )
            for baseline in BASELINES
        )
        scenarios.append(ScenarioEvidence(name, baselines, "a" * 64, "b" * 64))
    return GateEvidence(
        preparation_sha256="c" * 64,
        evaluation_sha256="d" * 64,
        canonical_input_sha256=None,
        canonical_files=(),
        config_schema_version="gate-a-v1",
        account_id_sha256=None,
        scenarios=tuple(scenarios),
        integrity_errors=tuple(integrity),
    )


def _accept_synthetic_replay(monkeypatch) -> None:
    monkeypatch.setattr(
        gate_assess_module, "_source_provenance_integrity_errors", lambda *_args: (),
    )
    monkeypatch.setattr(gate_assess_module, "_replay_integrity_errors", lambda *_args: ())
    monkeypatch.setattr(
        gate_assess_module, "_rendered_report_integrity_errors", lambda *_args: (),
    )


def _accept_missing_source_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        gate_assess_module, "_source_provenance_integrity_errors", lambda *_args: (),
    )


def _withhold(
    evidence: GateEvidence, indexes: set[int], reason: str,
) -> GateEvidence:
    for scenario_name in ("lower", "primary", "higher"):
        scenario = evidence.scenario(scenario_name)
        baselines = tuple(BaselineEvidence(
            baseline.name,
            tuple(
                ReplayObservation(
                    checkpoint_at=row.checkpoint_at,
                    cycle_payday=row.cycle_payday,
                    baseline_name=row.baseline_name,
                    status="withheld",
                    withheld_reason=reason,
                ) if index in indexes else row
                for index, row in enumerate(baseline.observations)
            ),
        ) for baseline in scenario.baselines)
        evidence = evidence.replace_scenario(ScenarioEvidence(
            scenario_name, baselines, scenario.json_sha256, scenario.markdown_sha256,
        ))
    return evidence


def _replay_bundle(config: GateAConfig) -> HistoryBundle:
    start = datetime(2025, 11, 1, tzinfo=UTC)
    end = datetime(2026, 4, 30, 23, tzinfo=UTC)
    transactions = tuple(CanonicalTransaction(
        transaction_id=f"synthetic-{index}",
        occurred_at=datetime(2025, 11, 5, 12, tzinfo=UTC) + timedelta(days=7 * index),
        amount_minor=-(index % 3 + 1) * 100,
        currency="GBP", status="settled", economic_type=EconomicType.EXPENSE,
    ) for index in range(25))
    paydays = tuple(Payday(payday=payday, known_from=known_from) for payday, known_from in (
        (date(2025, 11, 3), date(2025, 11, 1)),
        (date(2025, 12, 1), date(2025, 11, 3)),
        (date(2026, 1, 1), date(2025, 12, 1)),
        (date(2026, 2, 1), date(2026, 1, 1)),
        (date(2026, 3, 1), date(2026, 2, 1)),
        (date(2026, 4, 1), date(2026, 3, 1)),
        (date(2026, 5, 1), date(2026, 4, 1)),
    ))
    end_balance = 100_000 + sum(row.amount_minor for row in transactions)
    return HistoryBundle(
        manifest=HistoryManifest(
            dataset_id=config.dataset_id, account_id=config.account_id, currency="GBP",
            coverage_start=start, coverage_end=end,
        ),
        raw_records=(), transactions=transactions,
        anchors=(BalanceAnchor(captured_at=start, balance_minor=100_000, currency="GBP"),
                 BalanceAnchor(captured_at=end, balance_minor=end_balance, currency="GBP")),
        paydays=paydays, commitments=(),
    )


def test_integrity_or_policy_failure_returns_fix_data_or_policy(tmp_path):
    assessment = assess_gate(
        _config(tmp_path), _bundle(_config(tmp_path)),
        _evidence(integrity=("evidence_integrity_failed",)),
    )

    assert assessment.decision is GateDecision.FIX_DATA_OR_POLICY
    assert assessment.reason_codes == ("evidence_integrity_failed",)


def test_insufficient_valid_history_returns_collect_more_evidence(tmp_path, monkeypatch):
    _accept_synthetic_replay(monkeypatch)
    config = _config(tmp_path)
    evidence = _withhold(
        _evidence(), set(range(4)), "fewer_than_three_comparable_cycles",
    )
    assessment = assess_gate(config, _bundle(config), evidence)

    assert assessment.decision is GateDecision.COLLECT_MORE_EVIDENCE
    assert "insufficient_scored_primary_checkpoints" in assessment.reason_codes


def test_all_thresholds_met_returns_proceed_to_modelling(tmp_path, monkeypatch):
    _accept_synthetic_replay(monkeypatch)
    config = _config(tmp_path)
    assessment = assess_gate(config, _bundle(config), _evidence())

    assert assessment.decision is GateDecision.PROCEED_TO_MODELLING
    assert assessment.reason_codes == ("all_readiness_thresholds_met",)
    assert assessment.limitations


def test_all_missed_thresholds_are_reported(tmp_path, monkeypatch):
    _accept_synthetic_replay(monkeypatch)
    config = _config(tmp_path)
    evidence = _withhold(_evidence(), set(range(2, 12)), "missing_forecast")
    for scenario_name in ("lower", "primary", "higher"):
        scenario = evidence.scenario(scenario_name)
        baselines = tuple(BaselineEvidence(
            baseline.name,
            tuple(replace(row, cycle_payday=date(2026, 4, 20 + index % 2))
                  for index, row in enumerate(baseline.observations)),
        ) for baseline in scenario.baselines)
        evidence = evidence.replace_scenario(ScenarioEvidence(
            scenario_name, baselines, scenario.json_sha256, scenario.markdown_sha256,
        ))

    assessment = assess_gate(config, _bundle(config), evidence)

    assert assessment.reason_codes == (
        "insufficient_completed_payday_cycles",
        "insufficient_scored_primary_checkpoints",
        "insufficient_post_warm_up_coverage",
        "insufficient_distinct_realised_targets",
    )


def test_non_warm_up_missing_payday_counts_as_eligible_but_unscored(tmp_path, monkeypatch):
    _accept_synthetic_replay(monkeypatch)
    config = _config(tmp_path)
    evidence = _evidence()
    checkpoint = datetime(2026, 4, 6, 6, tzinfo=UTC)
    for scenario_name in ("lower", "primary", "higher"):
        scenario = evidence.scenario(scenario_name)
        baselines = tuple(BaselineEvidence(
            baseline.name,
            baseline.observations + (ReplayObservation(
                checkpoint_at=checkpoint,
                cycle_payday=None,
                baseline_name=baseline.name,
                status="withheld",
                withheld_reason="missing_payday",
            ),),
        ) for baseline in scenario.baselines)
        evidence = evidence.replace_scenario(ScenarioEvidence(
            scenario_name, baselines, scenario.json_sha256, scenario.markdown_sha256,
        ))

    assessment = assess_gate(config, _bundle(config), evidence)

    primary = next(row for row in assessment.scenarios if row.name == "primary")
    assert {(row.eligible_checkpoints, row.scored_checkpoints) for row in primary.baselines} == {
        (13, 12),
    }
    assert {row.coverage_ratio for row in primary.baselines} == {Decimal(12) / Decimal(13)}


def test_too_few_above_floor_completed_checkpoints_is_policy_failure(tmp_path, monkeypatch):
    _accept_synthetic_replay(monkeypatch)
    config = _config(tmp_path)

    assessment = assess_gate(config, _bundle(config), _evidence(count=11))

    assert assessment.decision is GateDecision.FIX_DATA_OR_POLICY
    assert assessment.reason_codes == ("primary_floor_policy_unsuitable",)


def test_primary_floor_drives_decision(tmp_path, monkeypatch):
    _accept_synthetic_replay(monkeypatch)
    config = _config(tmp_path)
    evidence = _evidence()
    lower = evidence.scenario("lower")
    broken_lower = ScenarioEvidence(
        "lower",
        tuple(BaselineEvidence(row.name, tuple(
            _observation(index, row.name, shortfall=True) for index in range(12)
        )) for row in lower.baselines),
        lower.json_sha256,
        lower.markdown_sha256,
    )

    assessment = assess_gate(config, _bundle(config), evidence.replace_scenario(broken_lower))

    assert assessment.decision is GateDecision.PROCEED_TO_MODELLING


def test_material_floor_sensitivity_is_highlighted_without_changing_decision(
    tmp_path, monkeypatch,
):
    _accept_synthetic_replay(monkeypatch)
    config = _config(tmp_path)
    evidence = _evidence()
    higher = evidence.scenario("higher")
    changed = tuple(
        BaselineEvidence(row.name, tuple(
            _observation(index, row.name, shortfall=index < 2) for index in range(12)
        ))
        for row in higher.baselines
    )

    assessment = assess_gate(
        config, _bundle(config),
        evidence.replace_scenario(ScenarioEvidence(
            "higher", changed, higher.json_sha256, higher.markdown_sha256,
        )),
    )

    assert assessment.decision is GateDecision.PROCEED_TO_MODELLING
    assert {flag.scenario for flag in assessment.sensitivity_flags} == {"higher"}


def test_assessment_rejects_evidence_hash_mismatch(tmp_path):
    config = _config(tmp_path)
    output = config.output_directory
    output.mkdir()
    preparation = {
        "schema_version": "gate-a-preparation-v1",
        "config_schema_version": "gate-a-v1",
        "canonical_input_sha256": "1" * 64,
        "files": {name: {"sha256": "2" * 64,
                         "row_count": 1 if name == "manifest.json" else 0} for name in (
            "manifest.json", "transactions.jsonl", "anchors.jsonl", "paydays.jsonl",
            "commitments.jsonl",
        )},
        "source": {"sha256": "3" * 64, "line_hashes": [], "row_count": 0},
        "auxiliary_sources": {
            name: {"sha256": "4" * 64, "row_count": 0}
            for name in ("anchors", "paydays", "commitments")
        },
        "transforms": {"canonical": "gate-a-canonical-v1", "monzo": "monzo-csv-v1"},
        "account_binding": {
            "basis": "operator_attested_config", "account_id_sha256": "3" * 64,
            "source_scope_verifiable": False, "applies_to_transaction_count": 0,
        },
    }
    prep_bytes = (json.dumps(preparation, sort_keys=True, separators=(",", ":")) + "\n").encode()
    (output / "preparation-manifest.json").write_bytes(prep_bytes)
    reports = {}
    for scenario in ("lower", "primary", "higher"):
        directory = output / scenario
        directory.mkdir()
        observations = []
        for baseline in BASELINES:
            observation = asdict(_observation(0, baseline))
            observation["checkpoint_at"] = observation["checkpoint_at"].isoformat()
            observation["cycle_payday"] = observation["cycle_payday"].isoformat()
            observations.append(observation)
        payload = {"metadata": {
            "dataset_id": "synthetic-gate-a", "data_label": "Private evaluation",
            "coverage_start": "2025-11-01T00:00:00+00:00",
            "coverage_end": "2026-04-30T23:00:00+00:00",
            "review_weekday": 0, "review_hour": 6, "timezone": "Europe/London",
            "floor_minor": {"lower": 20_000, "primary": 30_000, "higher": 40_000}[scenario],
            "feature_version": "offline-v2", "baseline_version": "v1",
            "input_sha256": "4" * 64,
        }, "baselines": {}, "observations": observations}
        json_bytes = (json.dumps(payload, sort_keys=True) + "\n").encode()
        md_bytes = b"# Aggregate private evaluation\n"
        (directory / "baseline-report.json").write_bytes(json_bytes)
        (directory / "baseline-report.md").write_bytes(md_bytes)
        reports[scenario] = {
            "baseline-report.json": hashlib.sha256(json_bytes).hexdigest(),
            "baseline-report.md": hashlib.sha256(md_bytes).hexdigest(),
        }
    manifest = {
        "schema_version": "gate-a-evaluation-v1",
        "preparation_sha256": hashlib.sha256(prep_bytes).hexdigest(),
        "policy_sha256": "5" * 64,
        "schedule": {"review_hour": 6, "review_weekday": 0, "timezone": "Europe/London"},
        "feature_version": "offline-v2", "baseline_version": "v1",
        "scenarios": [{"name": name, "reports": reports[name]}
                      for name in ("lower", "primary", "higher")],
    }
    (output / "evaluation-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8",
    )
    assert tuple(row.name for row in load_gate_evidence(output).scenarios) == (
        "lower", "primary", "higher",
    )
    (output / "primary" / "baseline-report.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid Gate A evidence"):
        load_gate_evidence(output)


def _write_replay_output(output: Path, config: GateAConfig, bundle: HistoryBundle) -> None:
    output.mkdir()
    contents = gate_assess_module._canonical_contents(bundle)
    preparation = {
        "schema_version": "gate-a-preparation-v1",
        "config_schema_version": "gate-a-v1",
        "canonical_input_sha256": gate_assess_module._canonical_input_hash(contents),
        "files": {
            name: {
                "sha256": hashlib.sha256(source).hexdigest(),
                "row_count": 1 if name == "manifest.json" else len(source.splitlines()),
            }
            for name, source in contents.items()
        },
        "source": {
            "sha256": "1" * 64, "line_hashes": [],
            "row_count": len(bundle.transactions),
        },
        "auxiliary_sources": {
            name: {"sha256": "2" * 64, "row_count": len(rows)}
            for name, rows in (
                ("anchors", bundle.anchors), ("paydays", bundle.paydays),
                ("commitments", bundle.commitments),
            )
        },
        "transforms": {"canonical": "gate-a-canonical-v1", "monzo": "monzo-csv-v1"},
        "account_binding": {
            "basis": "operator_attested_config",
            "account_id_sha256": hashlib.sha256(config.account_id.encode()).hexdigest(),
            "source_scope_verifiable": False,
            "applies_to_transaction_count": len(bundle.transactions),
        },
    }
    preparation_bytes = (
        json.dumps(preparation, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    (output / "preparation-manifest.json").write_bytes(preparation_bytes)
    replay = gate_assess_module._recompute_observations(config, bundle)
    scenario_rows = []
    for scenario_name, observations in replay.items():
        directory = output / scenario_name
        directory.mkdir()
        metadata = dict(gate_assess_module._expected_report_identity(
            config, bundle, scenario_name,
        ))
        json_bytes = render_json(metadata, observations).encode()
        markdown_bytes = render_markdown(metadata, observations).encode()
        (directory / "baseline-report.json").write_bytes(json_bytes)
        (directory / "baseline-report.md").write_bytes(markdown_bytes)
        scenario_rows.append({
            "name": scenario_name,
            "reports": {
                "baseline-report.json": hashlib.sha256(json_bytes).hexdigest(),
                "baseline-report.md": hashlib.sha256(markdown_bytes).hexdigest(),
            },
        })
    evaluation = {
        "schema_version": "gate-a-evaluation-v1",
        "preparation_sha256": hashlib.sha256(preparation_bytes).hexdigest(),
        "policy_sha256": gate_assess_module._policy_hash(config),
        "schedule": {"review_hour": 6, "review_weekday": 0, "timezone": "Europe/London"},
        "feature_version": "offline-v2", "baseline_version": "v1",
        "scenarios": scenario_rows,
    }
    (output / "evaluation-manifest.json").write_text(
        json.dumps(evaluation, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("alteration", (
    "truncate", "forecast", "recommendation", "violation", "reversal", "status",
))
def test_rehashed_altered_replay_is_rejected_by_pure_recomputation(
    tmp_path, alteration, monkeypatch,
):
    _accept_missing_source_provenance(monkeypatch)
    config = _config(tmp_path)
    bundle = _replay_bundle(config)
    _write_replay_output(config.output_directory, config, bundle)
    original = assess_gate(config, bundle, load_gate_evidence(config.output_directory))
    assert original.decision is not GateDecision.FIX_DATA_OR_POLICY

    manifest_path = config.output_directory / "evaluation-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for scenario_name in ("lower", "primary", "higher"):
        report_path = config.output_directory / scenario_name / "baseline-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if alteration == "truncate":
            report["observations"].pop()
        else:
            altered = next(row for row in report["observations"] if row["status"] == "scored")
            if alteration == "forecast":
                altered["forecast_minor"] += 1
            elif alteration == "recommendation":
                altered["recommended_minor"] += 1
            elif alteration == "violation":
                altered["shortfall_violation"] = not altered["shortfall_violation"]
            elif alteration == "reversal":
                altered["reversal_required_minor"] += 1
            else:
                altered.update({
                    "status": "withheld", "withheld_reason": "missing_forecast",
                    "forecast_minor": None, "forecast_upper_minor": None,
                    "actual_variable_spend_minor": None, "recommended_minor": None,
                    "minimum_balance_after_saving_minor": None, "hindsight_safe_minor": None,
                    "shortfall_violation": None, "reversal_required_minor": None,
                })
        report_path.write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )
        scenario = next(row for row in manifest["scenarios"] if row["name"] == scenario_name)
        scenario["reports"]["baseline-report.json"] = hashlib.sha256(
            report_path.read_bytes(),
        ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    assessment = assess_gate(config, bundle, load_gate_evidence(config.output_directory))

    assert assessment.decision is GateDecision.FIX_DATA_OR_POLICY
    assert assessment.reason_codes == ("replay_evidence_mismatch",)


@pytest.mark.parametrize("alteration", ("summary", "markdown"))
def test_rehashed_false_rendered_report_is_rejected(tmp_path, alteration, monkeypatch):
    _accept_missing_source_provenance(monkeypatch)
    config = _config(tmp_path)
    bundle = _replay_bundle(config)
    _write_replay_output(config.output_directory, config, bundle)
    manifest_path = config.output_directory / "evaluation-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    primary = next(row for row in manifest["scenarios"] if row["name"] == "primary")
    if alteration == "summary":
        report_path = config.output_directory / "primary" / "baseline-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["baselines"]["trailing_median"]["checkpoints_total"] += 1
        report_path.write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )
        report_name = "baseline-report.json"
    else:
        report_path = config.output_directory / "primary" / "baseline-report.md"
        report_path.write_text(
            report_path.read_text(encoding="utf-8") + "False readiness claim.\n",
            encoding="utf-8",
        )
        report_name = "baseline-report.md"
    primary["reports"][report_name] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    assessment = assess_gate(config, bundle, load_gate_evidence(config.output_directory))

    assert assessment.decision is GateDecision.FIX_DATA_OR_POLICY
    assert assessment.reason_codes == ("rendered_report_mismatch",)


def _source_provenance_case(tmp_path: Path, *, overrides: bool = False):
    header, row = Path("tests/fixtures/monzo_export/minimal-synthetic.csv").read_text(
        encoding="utf-8",
    ).splitlines()[:2]
    row = row.replace("15/07/2026", "15/01/2026")
    overrides_path = None
    if overrides:
        row = row.replace(",Card payment,", ",Faster payment,")
        overrides_path = tmp_path / "overrides.json"
        overrides_path.write_text(json.dumps([{
            "source_line": 2, "economic_type": "expense", "committed": False,
        }]), encoding="utf-8")
    source = tmp_path / "source.csv"
    source.write_text(f"{header}\n{row}\n", encoding="utf-8")
    config = _config(tmp_path).model_copy(update={
        "source_csv": source, "overrides_path": overrides_path,
    })
    parsed = parse_monzo_export(source, load_overrides(overrides_path))
    bundle = _bundle(config).model_copy(update={"transactions": parsed.transactions})
    provenance = {
        item.transaction_id: {
            "source_line": item.source_line, "source_hash": item.source_hash,
        }
        for item in parsed.record_provenance
    }
    preparation = {
        "source": {
            "sha256": parsed.source_sha256,
            "record_provenance": provenance,
        },
        "auxiliary_sources": ({
            "overrides": {
                "sha256": hashlib.sha256(overrides_path.read_bytes()).hexdigest(),
                "row_count": 1,
            },
        } if overrides_path else {}),
        "account_binding": {
            "basis": "operator_attested_config",
            "account_id_sha256": hashlib.sha256(config.account_id.encode()).hexdigest(),
            "applies_to_transaction_count": len(bundle.transactions),
        },
    }
    return config, bundle, preparation


@pytest.mark.parametrize("alteration", (
    "missing_record", "extra_record", "altered_record", "changed_source", "changed_overrides",
))
def test_changed_source_provenance_fails_closed(tmp_path, alteration):
    config, bundle, source_preparation = _source_provenance_case(
        tmp_path, overrides=alteration == "changed_overrides",
    )
    _write_replay_output(config.output_directory, config, bundle)
    preparation_path = config.output_directory / "preparation-manifest.json"
    preparation = json.loads(preparation_path.read_text(encoding="utf-8"))
    preparation["source"].update(source_preparation["source"])
    preparation["auxiliary_sources"].update(source_preparation["auxiliary_sources"])
    records = preparation["source"]["record_provenance"]
    key = next(iter(records))
    if alteration == "missing_record":
        records.pop(key)
    elif alteration == "extra_record":
        records["monzo-" + "f" * 64] = {"source_line": 99, "source_hash": "e" * 64}
    elif alteration == "altered_record":
        records[key]["source_hash"] = "d" * 64
    elif alteration == "changed_source":
        config.source_csv.write_text(
            config.source_csv.read_text(encoding="utf-8").replace("-12.34", "-12.35"),
            encoding="utf-8",
        )
        changed = parse_monzo_export(config.source_csv, load_overrides(config.overrides_path))
        preparation["source"]["sha256"] = changed.source_sha256
        preparation["source"]["record_provenance"] = {
            item.transaction_id: {
                "source_line": item.source_line, "source_hash": item.source_hash,
            }
            for item in changed.record_provenance
        }
    else:
        config.overrides_path.write_text(json.dumps([{
            "source_line": 2, "economic_type": "internal_transfer", "committed": False,
        }]), encoding="utf-8")
        preparation["auxiliary_sources"]["overrides"] = {
            "sha256": hashlib.sha256(config.overrides_path.read_bytes()).hexdigest(),
            "row_count": 1,
        }
    preparation_bytes = (
        json.dumps(preparation, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    preparation_path.write_bytes(preparation_bytes)
    evaluation_path = config.output_directory / "evaluation-manifest.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["preparation_sha256"] = hashlib.sha256(preparation_bytes).hexdigest()
    evaluation_path.write_text(
        json.dumps(evaluation, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    evidence = load_gate_evidence(config.output_directory)

    assessment = assess_gate(config, bundle, evidence)

    assert assessment.decision is GateDecision.FIX_DATA_OR_POLICY
    assert assessment.reason_codes == ("source_provenance_mismatch",)
