"""Point-in-time feature rows and separately calculated completed-window labels."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from zoneinfo import ZoneInfo

from safe_to_save.contracts import CanonicalTransaction, Commitment, EconomicType

FEATURE_VERSION = "offline-v2"
LONDON = ZoneInfo("Europe/London")


@dataclass(frozen=True)
class FeatureVector:
    checkpoint_at: datetime
    payday: date
    days_to_payday: int
    balance_minor: int
    known_commitments_minor: int
    variable_spend_7d_minor: int
    variable_spend_28d_minor: int
    daily_spend_stddev_28d_minor: int
    weekday: int
    month: int
    coverage_age_days: int


@dataclass(frozen=True)
class CompletedExample:
    dataset_id: str
    cycle_payday: date
    features: FeatureVector
    target_variable_spend_minor: int


@dataclass(frozen=True)
class WithheldExample:
    dataset_id: str
    checkpoint_at: datetime
    reason: str


def integer_pstdev(values: Sequence[int]) -> int:
    """Population deviation, rounded half up to whole minor units without floats."""
    if not values:
        return 0
    with localcontext() as context:
        context.prec = max(28, 2 * len(str(max(abs(value) for value in values))) + 10)
        mean = Decimal(sum(values)) / Decimal(len(values))
        variance = sum((Decimal(value) - mean) ** 2 for value in values) / Decimal(len(values))
        return int(variance.sqrt().quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _variable_spend(rows: Sequence[CanonicalTransaction]) -> int:
    # Refunds are positive expense flow. Adjustments, income and either kind of
    # transfer never enter the spending target or the trailing spending features.
    return max(0, -sum(
        row.amount_minor for row in rows
        if row.economic_type == EconomicType.EXPENSE and row.committed is False
    ))


def build_example(
    dataset_id: str,
    checkpoint_at: datetime,
    payday: date | None,
    balance_minor: int | None,
    coverage_start: datetime | None,
    transactions: Sequence[CanonicalTransaction],
    commitments: Sequence[Commitment],
) -> CompletedExample | WithheldExample:
    """Build one example using a caller-verified, completed target window.

    The caller must establish continuous coverage through London midnight at
    payday, reconstruct a valid checkpoint balance, and supply a recorded payday
    known at the checkpoint. This interface cannot establish those facts from
    transactions alone (an empty day is valid); it checks the inputs it receives.

    Trailing windows are elapsed UTC days, open on the left and closed at the
    checkpoint. Deviation uses 28 contiguous 24-hour bins ending there, including
    zero-spend bins. Calendar features use London dates. The realised target is
    strictly after the checkpoint and before London midnight starting payday.
    """
    checkpoint = _utc(checkpoint_at, "checkpoint_at")

    def withheld(reason: str) -> WithheldExample:
        return WithheldExample(dataset_id, checkpoint, reason)

    if balance_minor is None:
        return withheld("missing_balance")
    if type(balance_minor) is not int:
        return withheld("invalid_balance")
    if payday is None:
        return withheld("missing_payday")
    local_date = checkpoint.astimezone(LONDON).date()
    if payday <= local_date:
        return withheld("invalid_payday")
    if coverage_start is None:
        return withheld("missing_coverage")
    coverage = _utc(coverage_start, "coverage_start")
    if checkpoint < coverage:
        return withheld("checkpoint_before_coverage")
    if checkpoint - coverage < timedelta(days=28):
        return withheld("insufficient_trailing_history")

    trailing_start = checkpoint - timedelta(days=28)
    trailing = tuple(row for row in transactions if trailing_start < row.occurred_at <= checkpoint)
    seven_day_start = checkpoint - timedelta(days=7)
    daily_spend = tuple(
        _variable_spend(tuple(
            row for row in trailing
            if checkpoint - timedelta(days=index + 1) < row.occurred_at
            <= checkpoint - timedelta(days=index)
        ))
        for index in range(28)
    )
    target_end = datetime.combine(payday, time.min, tzinfo=LONDON).astimezone(UTC)
    features = FeatureVector(
        checkpoint_at=checkpoint,
        payday=payday,
        days_to_payday=(payday - local_date).days,
        balance_minor=balance_minor,
        known_commitments_minor=-sum(
            row.amount_minor for row in commitments
            if row.known_from <= checkpoint < row.due_at < target_end
        ),
        variable_spend_7d_minor=_variable_spend(tuple(
            row for row in trailing if row.occurred_at > seven_day_start
        )),
        variable_spend_28d_minor=_variable_spend(trailing),
        daily_spend_stddev_28d_minor=integer_pstdev(daily_spend),
        weekday=local_date.weekday(),
        month=local_date.month,
        coverage_age_days=(checkpoint - coverage).days,
    )
    target = _variable_spend(tuple(
        row for row in transactions if checkpoint < row.occurred_at < target_end
    ))
    return CompletedExample(dataset_id, payday, features, target)
