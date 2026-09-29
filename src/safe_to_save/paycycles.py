"""Recorded payday periods and daylight-saving-safe weekly review times."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from safe_to_save.contracts import Payday


class NonexistentLocalTimeError(ValueError):
    """Raised when a configured wall-clock checkpoint is skipped by DST."""


@dataclass(frozen=True)
class PayCycle:
    """A half-open period beginning on one payday and ending at the next."""

    start: date
    payday: date

    def __post_init__(self) -> None:
        if self.payday <= self.start:
            raise ValueError("payday must be after cycle start")


@dataclass(frozen=True)
class ReviewSchedule:
    weekday: int
    local_time: time
    timezone: str

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise ValueError("weekday must be between 0 and 6")
        if self.local_time.tzinfo is not None:
            raise ValueError("local_time must not include timezone information")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError(f"unknown timezone: {self.timezone}") from None


def build_pay_cycles(paydays: Sequence[Payday]) -> tuple[PayCycle, ...]:
    """Build cycles only between consecutive, explicitly recorded paydays."""

    ordered = sorted(row.payday for row in paydays)
    if len(set(ordered)) != len(ordered):
        raise ValueError("payday dates must be unique")
    return tuple(
        PayCycle(start=previous, payday=current)
        for previous, current in pairwise(ordered)
    )


def next_known_payday(checkpoint: date, paydays: Sequence[Payday]) -> date | None:
    """Return the earliest future payday that was recorded by the checkpoint."""

    candidates = (
        row.payday
        for row in paydays
        if row.known_from <= checkpoint and row.payday > checkpoint
    )
    return min(candidates, default=None)


def weekly_checkpoints(
    cycle: PayCycle, schedule: ReviewSchedule,
) -> tuple[datetime, ...]:
    """Create local weekly checkpoints in UTC.

    Non-existent wall times fail closed. Ambiguous autumn times deterministically
    use ``fold=0``, the earlier of the two UTC instants.
    """

    zone = ZoneInfo(schedule.timezone)
    days_to_first = (schedule.weekday - cycle.start.weekday()) % 7
    review_date = cycle.start + timedelta(days=days_to_first)
    checkpoints: list[datetime] = []
    while review_date < cycle.payday:
        wall_time = datetime.combine(review_date, schedule.local_time)
        local = wall_time.replace(tzinfo=zone, fold=0)
        round_trip = local.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if round_trip != wall_time:
            raise NonexistentLocalTimeError(
                f"local checkpoint {wall_time.isoformat()} does not exist "
                f"in {schedule.timezone}"
            )
        checkpoints.append(local.astimezone(UTC))
        review_date += timedelta(days=7)
    return tuple(checkpoints)
