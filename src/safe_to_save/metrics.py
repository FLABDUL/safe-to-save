"""One-baseline metrics; withheld checkpoints stay in the coverage denominator."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from itertools import pairwise

from safe_to_save.replay import ReplayObservation


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if not denominator:
        return None
    with localcontext() as context:
        context.prec = max(28, len(str(abs(numerator))) + len(str(abs(denominator))) + 10)
        return Decimal(numerator) / Decimal(denominator)


def _median(values: Sequence[int]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return Decimal(ordered[middle])
    return _ratio(ordered[middle - 1] + ordered[middle], 2)


@dataclass(frozen=True)
class BacktestSummary:
    checkpoints_total: int
    checkpoints_scored: int
    checkpoints_withheld: int
    withheld_reasons: dict[str, int]
    coverage_ratio: Decimal | None
    mean_absolute_error_minor: Decimal | None
    median_absolute_error_minor: Decimal | None
    forecast_upper_coverage_ratio: Decimal | None
    shortfall_violation_count: int
    shortfall_violation_rate: Decimal | None
    reversal_count: int
    reversal_total_minor: int
    recommended_total_minor: int
    useful_savings_minor: int
    hindsight_safe_minor: int
    useful_yield_ratio: Decimal | None
    median_absolute_recommendation_change_minor: Decimal | None


def summarise(observations: Sequence[ReplayObservation]) -> BacktestSummary:
    if len({row.baseline_name for row in observations}) > 1:
        raise ValueError("summarise accepts one baseline only")
    ordered = sorted(observations, key=lambda row: row.checkpoint_at)
    scored = [row for row in ordered if row.status == "scored"]
    withheld = [row for row in ordered if row.status == "withheld"]
    if len(scored) + len(withheld) != len(ordered):
        raise ValueError("invalid observation status")
    errors = [abs(row.forecast_minor - row.actual_variable_spend_minor) for row in scored]
    violations = sum(row.shortfall_violation for row in scored)
    useful = sum(min(row.recommended_minor, row.hindsight_safe_minor) for row in scored)
    hindsight = sum(row.hindsight_safe_minor for row in scored)
    # A withheld checkpoint breaks consecutive recommendations; do not bridge it.
    # Six-to-eight elapsed days admits weekly local-time DST transitions.
    changes = [abs(right.recommended_minor - left.recommended_minor)
               for left, right in pairwise(ordered)
               if left.status == right.status == "scored"
               and 6 * 86400 <= (right.checkpoint_at - left.checkpoint_at).total_seconds()
               <= 8 * 86400]
    return BacktestSummary(
        len(ordered), len(scored), len(withheld),
        dict(sorted(Counter(row.withheld_reason for row in withheld).items())),
        _ratio(len(scored), len(ordered)), _ratio(sum(errors), len(errors)), _median(errors),
        _ratio(sum(row.actual_variable_spend_minor <= row.forecast_upper_minor for row in scored),
               len(scored)),
        violations, _ratio(violations, len(scored)),
        sum(row.reversal_required_minor > 0 for row in scored),
        sum(row.reversal_required_minor for row in scored),
        sum(row.recommended_minor for row in scored), useful, hindsight,
        _ratio(useful, hindsight), _median(changes),
    )
