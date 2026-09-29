"""Ex-ante savings policy and independent, realised balance-path evaluation."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

from safe_to_save.baselines import BaselineForecast, UnavailableForecast
from safe_to_save.contracts import CanonicalTransaction
from safe_to_save.features import LONDON, CompletedExample, WithheldExample


@dataclass(frozen=True)
class ReplayObservation:
    checkpoint_at: datetime
    cycle_payday: date | None
    baseline_name: str
    status: str
    withheld_reason: str | None
    forecast_minor: int | None = None
    forecast_upper_minor: int | None = None
    actual_variable_spend_minor: int | None = None
    recommended_minor: int | None = None
    minimum_balance_after_saving_minor: int | None = None
    hindsight_safe_minor: int | None = None
    shortfall_violation: bool | None = None
    reversal_required_minor: int | None = None


def withheld_observation(
    example: CompletedExample | WithheldExample, baseline_name: str, reason: str,
) -> ReplayObservation:
    if isinstance(example, WithheldExample):
        return ReplayObservation(example.checkpoint_at, None, baseline_name, "withheld", reason)
    return ReplayObservation(example.features.checkpoint_at, example.cycle_payday,
                             baseline_name, "withheld", reason)


def actual_balance_path(
    example: CompletedExample, transactions: Sequence[CanonicalTransaction],
    coverage_start: datetime | None, coverage_end: datetime | None,
) -> tuple[int, ...] | None:
    """Apply all signed flows after checkpoint and before payday London midnight.

    Coverage bounds must attest continuous history. Payday income is excluded:
    the floor must survive until the new cycle begins. Input order breaks ties
    between simultaneous transactions, matching canonical source ordering.
    """
    start = example.features.checkpoint_at
    end = datetime.combine(example.cycle_payday, time.min, tzinfo=LONDON)
    if (coverage_start is None or coverage_end is None
            or coverage_start > start or coverage_end < end
            or type(example.features.balance_minor) is not int):
        return None
    path = [example.features.balance_minor]
    for row in sorted(transactions, key=lambda row: row.occurred_at):
        if start < row.occurred_at < end:
            path.append(path[-1] + row.amount_minor)
    return tuple(path)


def replay_one(
    example: CompletedExample | WithheldExample,
    forecast: BaselineForecast | UnavailableForecast | None,
    actual_path: Sequence[int] | None, floor_minor: int | None,
) -> ReplayObservation:
    """Reserve commitments, upper forecast and floor; never use hindsight to save.

    Negative floors are configuration errors. Missing/malformed evidence instead
    produces an explicit withheld result. Realised breaches remain visible even
    when the account would already have breached its floor without saving.
    """
    def withheld(reason: str) -> ReplayObservation:
        return withheld_observation(example, forecast.baseline_name if forecast else "unknown",
                                    reason)

    if type(floor_minor) is not int:
        return withheld("invalid_floor")
    if floor_minor < 0:
        raise ValueError("floor_minor must be non-negative")
    if isinstance(example, WithheldExample):
        return withheld(example.reason)
    if forecast is None:
        return withheld("missing_forecast")
    if isinstance(forecast, UnavailableForecast):
        return withheld(forecast.reason)
    if (type(example.target_variable_spend_minor) is not int
            or example.target_variable_spend_minor < 0):
        return withheld("invalid_actual_variable_spend")
    balance = example.features.balance_minor
    commitments = example.features.known_commitments_minor
    if type(balance) is not int:
        return withheld("invalid_balance")
    if type(commitments) is not int or commitments < 0:
        return withheld("invalid_commitments")
    if (type(forecast.point_minor) is not int or type(forecast.upper_minor) is not int
            or forecast.upper_minor < forecast.point_minor or forecast.upper_minor < 0):
        return withheld("invalid_forecast")
    if not actual_path:
        return withheld("missing_actual_path")
    if any(type(value) is not int for value in actual_path):
        return withheld("invalid_actual_path")
    if actual_path[0] != balance:
        return withheld("path_balance_mismatch")
    recommended = max(0, balance - commitments - forecast.upper_minor - floor_minor)
    minimum_after = min(actual_path) - recommended
    return ReplayObservation(
        example.features.checkpoint_at, example.cycle_payday, forecast.baseline_name,
        "scored", None, forecast.point_minor, forecast.upper_minor,
        example.target_variable_spend_minor, recommended, minimum_after,
        max(0, min(actual_path) - floor_minor), minimum_after < floor_minor,
        max(0, floor_minor - minimum_after),
    )
