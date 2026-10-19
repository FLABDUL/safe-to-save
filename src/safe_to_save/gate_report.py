"""Deterministic, aggregate-only Gate A decision reports."""

import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from safe_to_save.gate_assess import GateAssessment


def _value(value):
    if isinstance(value, Decimal):
        return format(value, ".4f")
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dict):
        return {key: _value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_value(item) for item in value]
    return value


def _payload(assessment: GateAssessment) -> dict:
    value = _value(asdict(assessment))
    scenarios = value.pop("scenarios")
    value["scenarios"] = scenarios
    primary = next(row for row in scenarios if row["name"] == "primary")
    value["primary"] = {"days_to_payday": [
        {"baseline": baseline["name"], "buckets": baseline["days_to_payday"]}
        for baseline in primary["baselines"]
    ]}
    return value


def render_gate_json(assessment: GateAssessment) -> str:
    return json.dumps(_payload(assessment), sort_keys=True, indent=2) + "\n"


def render_gate_markdown(assessment: GateAssessment) -> str:
    lines = [
        "# Safe to Save Gate A assessment", "",
        f"Decision: `{assessment.decision.value}`.",
        f"Validation state: `{assessment.validation_state}`.", "",
        "This report is model-readiness evidence; it is not approval for live financial use.",
        "Baseline results do not establish that a live savings recommendation is safe.", "",
        "## Decision reasons", "",
        *(f"- `{reason}`" for reason in assessment.reason_codes), "",
        "## Readiness coverage", "",
        f"- Complete months: {assessment.complete_months}",
        f"- Completed payday cycles: {assessment.completed_payday_cycles}",
        f"- Distinct realised targets: {assessment.distinct_realised_targets}", "",
        "## Per-floor and per-baseline summaries", "",
    ]
    for scenario in assessment.scenarios:
        lines.extend((f"### {scenario.name}", ""))
        for baseline in scenario.baselines:
            lines.append(
                f"- {baseline.name}: {baseline.scored_checkpoints}/{baseline.eligible_checkpoints} "
                f"scored; coverage {baseline.coverage_ratio:.4f}; shortfall rate "
                f"{baseline.shortfall_rate:.4f}; useful-yield rate {baseline.useful_yield_rate:.4f}."
            )
        lines.append("")
    lines.extend(("## Primary days-to-payday breakdown", ""))
    for baseline in _payload(assessment)["primary"]["days_to_payday"]:
        lines.append(f"### {baseline['baseline']}")
        lines.extend(
            f"- {row['days']} days: {row['scored']} scored; {row['shortfalls']} shortfalls."
            for row in baseline["buckets"]
        )
    lines.extend(("", "## Sensitivity flags", ""))
    lines.extend(
        f"- {flag.scenario}/{flag.baseline}: material {flag.metric} difference."
        for flag in assessment.sensitivity_flags
    )
    if not assessment.sensitivity_flags:
        lines.append("None.")
    lines.extend(("", "## Evidence hashes", ""))
    lines.extend(f"- {name}: `{digest}`" for name, digest in assessment.evidence_hashes)
    lines.extend(("", "## Limitations", ""))
    lines.extend(f"- {item}" for item in assessment.limitations)
    return "\n".join(lines) + "\n"


def write_gate_report(output: Path, assessment: GateAssessment) -> Path:
    """Render both reports before replacing either; preserve differing outputs."""
    rendered = {
        "gate-a-report.json": render_gate_json(assessment),
        "gate-a-report.md": render_gate_markdown(assessment),
    }
    existing = {name: output / name for name in rendered}
    if any(path.exists() for path in existing.values()):
        try:
            identical = all(
                path.is_file() and path.read_text(encoding="utf-8") == rendered[name]
                for name, path in existing.items()
            )
        except OSError:
            identical = False
        if identical:
            return output
        raise ValueError("existing Gate A report differs") from None
    temporary = []
    try:
        output.mkdir(parents=True, exist_ok=True)
        for name, content in rendered.items():
            path = output / f"{name}.tmp"
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            temporary.append(path)
        for path in temporary:
            path.replace(path.with_suffix(""))
    except OSError:
        raise ValueError("Gate A report write failed") from None
    finally:
        for path in temporary:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return output
