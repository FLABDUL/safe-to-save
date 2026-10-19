import json
from decimal import Decimal
from pathlib import Path

import pytest

from safe_to_save.gate_assess import (
    GATE_DECISION_VERSION,
    BaselineAssessment,
    DaysToPaydaySummary,
    GateAssessment,
    GateDecision,
    ScenarioAssessment,
)
from safe_to_save.gate_report import render_gate_json, render_gate_markdown, write_gate_report


def _assessment(decision: GateDecision) -> GateAssessment:
    baseline = BaselineAssessment(
        "synthetic_baseline", 12, 12, Decimal(1), Decimal("0.0833"), Decimal("0.5"),
        (DaysToPaydaySummary(7, 6, 1), DaysToPaydaySummary(14, 6, 0)),
    )
    return GateAssessment(
        GATE_DECISION_VERSION, decision, "synthetic_validated", ("synthetic_reason",),
        6, 6, 3,
        tuple(ScenarioAssessment(name, (baseline,))
              for name in ("lower", "primary", "higher")),
        (), (("evaluation_manifest", "a" * 64),),
        ("This is model-readiness evidence, not approval for live financial use.",),
    )


@pytest.mark.parametrize("decision", tuple(GateDecision))
def test_json_is_sorted_and_reproducible(decision):
    assessment = _assessment(decision)

    first = render_gate_json(assessment)

    assert first == render_gate_json(assessment)
    assert first == json.dumps(json.loads(first), sort_keys=True, indent=2) + "\n"


def test_markdown_distinguishes_readiness_from_live_safety():
    report = render_gate_markdown(_assessment(GateDecision.PROCEED_TO_MODELLING))

    assert "model-readiness evidence" in report
    assert "not approval for live financial use" in report


def test_report_groups_metrics_by_days_to_payday():
    payload = json.loads(render_gate_json(_assessment(GateDecision.COLLECT_MORE_EVIDENCE)))

    assert payload["primary"]["days_to_payday"] == [{
        "baseline": "synthetic_baseline",
        "buckets": [
            {"days": 7, "scored": 6, "shortfalls": 1},
            {"days": 14, "scored": 6, "shortfalls": 0},
        ],
    }]


def test_reports_contain_no_transaction_identity_or_description(tmp_path: Path):
    assessment = _assessment(GateDecision.FIX_DATA_OR_POLICY)
    forbidden = ("transaction_id", "description", "merchant", "secret-token", "C:\\private")

    output = write_gate_report(tmp_path, assessment)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir())

    assert all(value not in combined for value in forbidden)


def test_existing_different_report_is_preserved_and_fails_closed(tmp_path: Path):
    existing = tmp_path / "gate-a-report.json"
    tmp_path.mkdir(exist_ok=True)
    existing.write_text("preserve me\n", encoding="utf-8")

    with pytest.raises(ValueError, match="existing Gate A report differs"):
        write_gate_report(tmp_path, _assessment(GateDecision.PROCEED_TO_MODELLING))

    assert existing.read_text(encoding="utf-8") == "preserve me\n"
    assert not (tmp_path / "gate-a-report.md").exists()
