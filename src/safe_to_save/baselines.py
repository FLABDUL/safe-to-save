"""Deterministic, point-in-time spending forecasts in integer minor units."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_CEILING, Decimal, localcontext

from safe_to_save.features import LONDON, CompletedExample

BASELINE_VERSION = "v1"


@dataclass(frozen=True)
class BaselineForecast:
    baseline_name: str
    point_minor: int
    upper_minor: int
    sample_size: int
    training_paydays: tuple[date, ...]
    training_days_to_payday: tuple[int, ...]


@dataclass(frozen=True)
class UnavailableForecast:
    baseline_name: str
    reason: str


def nearest_rank(values: Sequence[int], percentile: Decimal) -> int:
    """Nearest-rank quantile; zero selects the minimum."""
    if not values:
        raise ValueError("values must not be empty")
    if not percentile.is_finite() or not Decimal(0) <= percentile <= Decimal(1):
        raise ValueError("percentile must be between zero and one")
    ordered = sorted(values)
    with localcontext() as context:
        context.prec = max(28, len(str(len(ordered))) + len(percentile.as_tuple().digits) + 2)
        rank = (Decimal(len(ordered)) * percentile).to_integral_value(rounding=ROUND_CEILING)
    return ordered[max(0, int(rank) - 1)]


def _training_cycles(
    current: CompletedExample, history: Sequence[CompletedExample], *, comparable: bool,
) -> tuple[CompletedExample, ...]:
    """Choose one row per mature cycle without consulting any target to break ties.

    Comparables prefer the closest position, then the latest checkpoint. Exact
    duplicate rows are harmless; conflicting rows for one checkpoint are errors.
    Payday at London midnight closes the target window, so a label whose payday
    has not yet arrived cannot be training evidence even if its checkpoint has.
    """
    eligible = {}
    for row in history:
        if (row.dataset_id != current.dataset_id
                or row.cycle_payday >= current.cycle_payday
                or row.features.checkpoint_at >= current.features.checkpoint_at
                or datetime.combine(row.cycle_payday, time.min, tzinfo=LONDON)
                > current.features.checkpoint_at):
            continue
        key = (row.cycle_payday, row.features.checkpoint_at)
        if key in eligible and eligible[key] != row:
            raise ValueError("conflicting historical checkpoint")
        eligible[key] = row
    cycles = {}
    for row in eligible.values():
        distance = abs(row.features.days_to_payday - current.features.days_to_payday)
        if comparable and distance > 1:
            continue
        # Prefer lower distance, then later time; never sort on realised spend.
        rank = (-distance if comparable else 0, row.features.checkpoint_at)
        previous = cycles.get(row.cycle_payday)
        if previous is None or rank > previous[0]:
            cycles[row.cycle_payday] = (rank, row)
    count = 5 if comparable else 3
    return tuple(cycles[payday][1] for payday in sorted(cycles)[-count:])


def _forecast(name: str, rows: Sequence[CompletedExample]) -> BaselineForecast:
    targets = sorted(row.target_variable_spend_minor for row in rows)
    middle = len(targets) // 2
    # For an even count, round a half minor unit upwards. Integer arithmetic also
    # preserves exactness for large values and signed, refund-heavy targets.
    point = targets[middle] if len(targets) % 2 else (
        targets[middle - 1] + targets[middle] + 1
    ) // 2
    return BaselineForecast(
        name, point, nearest_rank(targets, Decimal("0.9")), len(rows),
        tuple(row.cycle_payday for row in rows),
        tuple(row.features.days_to_payday for row in rows),
    )


class TrailingMedianBaseline:
    name = "trailing_median"

    def predict(
        self, current: CompletedExample, history: Sequence[CompletedExample],
    ) -> BaselineForecast | UnavailableForecast:
        rows = _training_cycles(current, history, comparable=False)
        if len(rows) < 3:
            return UnavailableForecast(self.name, "fewer_than_three_earlier_cycles")
        return _forecast(self.name, rows)


class ComparablePositionBaseline:
    name = "comparable_position"

    def predict(
        self, current: CompletedExample, history: Sequence[CompletedExample],
    ) -> BaselineForecast | UnavailableForecast:
        rows = _training_cycles(current, history, comparable=True)
        if len(rows) < 3:
            return UnavailableForecast(self.name, "fewer_than_three_comparable_cycles")
        return _forecast(self.name, rows)


class ConservativeRulesBaseline:
    name = "conservative_rules"

    def predict(
        self, current: CompletedExample, history: Sequence[CompletedExample],
    ) -> BaselineForecast | UnavailableForecast:
        rows = _training_cycles(current, history, comparable=True)
        if len(rows) < 3:
            return UnavailableForecast(self.name, "fewer_than_three_comparable_cycles")
        forecast = _forecast(self.name, rows)
        spend = current.features.variable_spend_7d_minor
        days = current.features.days_to_payday
        with localcontext() as context:
            context.prec = max(28, len(str(abs(spend))) + len(str(abs(days))) + 10)
            # Multiply first so an intermediate recurring division cannot push
            # an exact whole penny just above its boundary before ceiling.
            pace = int((Decimal(spend) * Decimal(days) * Decimal("1.25") / Decimal(7))
                       .to_integral_value(rounding=ROUND_CEILING))
        conservative = max(pace, forecast.upper_minor)
        return BaselineForecast(
            self.name, conservative, conservative, forecast.sample_size,
            forecast.training_paydays, forecast.training_days_to_payday,
        )
