"""Stable, merchant-free JSON and Markdown baseline reports."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal, localcontext

from safe_to_save.metrics import summarise
from safe_to_save.replay import ReplayObservation


def json_value(value):
    if isinstance(value, Decimal):
        with localcontext() as context:
            context.prec = max(28, len(value.as_tuple().digits) + abs(value.adjusted()) + 10)
            return format(value, ".4f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def report_payload(metadata: Mapping, observations: Sequence[ReplayObservation]) -> dict:
    ordered = sorted(observations, key=lambda row: (row.checkpoint_at, row.baseline_name))
    names = sorted({row.baseline_name for row in ordered})
    return json_value({
        "metadata": dict(metadata),
        "baselines": {name: asdict(summarise(tuple(
            row for row in ordered if row.baseline_name == name))) for name in names},
        "observations": [asdict(row) for row in ordered],
    })


def render_json(metadata: Mapping, observations: Sequence[ReplayObservation]) -> str:
    return json.dumps(report_payload(metadata, observations), sort_keys=True, indent=2) + "\n"


def render_markdown(metadata: Mapping, observations: Sequence[ReplayObservation]) -> str:
    label = metadata.get("data_label")
    if label not in {"Synthetic demonstration", "Private evaluation"}:
        raise ValueError("data_label must explicitly identify synthetic or private evaluation")
    payload = report_payload(metadata, observations)
    summaries = payload["baselines"]
    names = list(summaries)
    lines = ["# Safe to Save baseline evaluation", "", label, "",
             f"Coverage: {metadata.get('coverage_start')} to {metadata.get('coverage_end')}.",
             (f"Checkpoints: {len({row.checkpoint_at for row in observations})} distinct; "
              f"{len(observations)} baseline observations."), "",
             "| Metric | " + " | ".join(names) + " |",
             "| --- | " + " | ".join("---" for _ in names) + " |"]
    metrics = [
        ("Scored / total", None), ("Withheld", "checkpoints_withheld"),
        ("Coverage ratio", "coverage_ratio"), ("MAE (minor units)", "mean_absolute_error_minor"),
        ("Median absolute error (minor units)", "median_absolute_error_minor"),
        ("Upper forecast coverage", "forecast_upper_coverage_ratio"),
        ("Floor violations", "shortfall_violation_count"),
        ("Violation rate", "shortfall_violation_rate"), ("Reversal count", "reversal_count"),
        ("Reversal total (minor units)", "reversal_total_minor"),
        ("Recommended total (minor units)", "recommended_total_minor"),
        ("Useful savings (minor units)", "useful_savings_minor"),
        ("Hindsight safe (minor units)", "hindsight_safe_minor"),
        ("Useful yield", "useful_yield_ratio"),
        ("Median weekly change (minor units)", "median_absolute_recommendation_change_minor"),
    ]
    for title, key in metrics:
        values = []
        for name in names:
            summary = summaries[name]
            value = (f"{summary['checkpoints_scored']} / {summary['checkpoints_total']}"
                     if key is None else summary[key])
            values.append("null" if value is None else str(value))
        lines.append(f"| {title} | " + " | ".join(values) + " |")
    lines += ["", "Withheld reasons:", ""]
    reasons = [f"- {name}: {reason} = {count}" for name in names
               for reason, count in summaries[name]["withheld_reasons"].items()]
    lines += reasons or ["None."]
    lines += ["", "Worst three shortfall/reversal cases:", ""]
    worst = sorted((row for row in observations if row.shortfall_violation),
                   key=lambda row: (-row.reversal_required_minor,
                                    row.checkpoint_at, row.baseline_name))[:3]
    lines += [f"- {row.checkpoint_at.date().isoformat()} ({row.baseline_name}): "
              f"reversal {row.reversal_required_minor} minor units; "
              f"minimum balance {row.minimum_balance_after_saving_minor} minor units."
              for row in worst] or ["None."]
    lines += ["", ("Limitations: baseline results are not model promotion evidence. "
              "Checkpoints are independent counterfactuals; totals are overlapping opportunities, "
              "not cumulative savings. The floor reserve uses forecasts and does not guarantee "
              "realised safety. Reversal amounts include pre-existing floor deficits.")]
    return "\n".join(lines) + "\n"
