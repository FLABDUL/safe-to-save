from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from safe_to_save.contracts import Payday
from safe_to_save.paycycles import (
    NonexistentLocalTimeError,
    PayCycle,
    ReviewSchedule,
    build_pay_cycles,
    next_known_payday,
    weekly_checkpoints,
)


def test_builds_half_open_cycles_from_consecutive_recorded_paydays() -> None:
    paydays = (
        Payday(payday=date(2026, 1, 30), known_from=date(2025, 12, 1)),
        Payday(payday=date(2026, 2, 27), known_from=date(2026, 1, 1)),
        Payday(payday=date(2026, 3, 27), known_from=date(2026, 2, 1)),
    )

    assert build_pay_cycles(paydays) == (
        PayCycle(start=date(2026, 1, 30), payday=date(2026, 2, 27)),
        PayCycle(start=date(2026, 2, 27), payday=date(2026, 3, 27)),
    )


def test_sunday_review_stays_at_1800_london_across_dst() -> None:
    cycle = PayCycle(start=date(2026, 3, 20), payday=date(2026, 4, 20))
    schedule = ReviewSchedule(weekday=6, local_time=time(18, 0), timezone="Europe/London")
    checkpoints = weekly_checkpoints(cycle, schedule)
    london = ZoneInfo("Europe/London")

    assert all(value.tzinfo is UTC for value in checkpoints)
    assert all(value.astimezone(london).hour == 18 for value in checkpoints)
    assert len(checkpoints) == len(set(checkpoints))
    assert checkpoints == tuple(sorted(checkpoints))


def test_checkpoint_on_payday_is_excluded() -> None:
    cycle = PayCycle(start=date(2026, 4, 5), payday=date(2026, 4, 19))
    schedule = ReviewSchedule(weekday=6, local_time=time(18), timezone="Europe/London")

    local_dates = tuple(
        checkpoint.astimezone(ZoneInfo("Europe/London")).date()
        for checkpoint in weekly_checkpoints(cycle, schedule)
    )

    assert local_dates == (date(2026, 4, 5), date(2026, 4, 12))


def test_nonexistent_spring_clock_time_is_rejected() -> None:
    cycle = PayCycle(start=date(2026, 3, 29), payday=date(2026, 3, 30))
    schedule = ReviewSchedule(
        weekday=6, local_time=time(1, 30), timezone="Europe/London"
    )

    with pytest.raises(
        NonexistentLocalTimeError,
        match="2026-03-29T01:30:00.*Europe/London",
    ):
        weekly_checkpoints(cycle, schedule)


def test_ambiguous_autumn_clock_time_uses_earlier_fold() -> None:
    cycle = PayCycle(start=date(2026, 10, 25), payday=date(2026, 10, 26))
    schedule = ReviewSchedule(
        weekday=6, local_time=time(1, 30), timezone="Europe/London"
    )

    assert weekly_checkpoints(cycle, schedule) == (
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
    )


def test_payday_unknown_at_checkpoint_is_not_used() -> None:
    paydays = (
        Payday(payday=date(2026, 2, 27), known_from=date(2026, 1, 1)),
        Payday(payday=date(2026, 3, 27), known_from=date(2026, 3, 20)),
    )

    assert next_known_payday(date(2026, 3, 15), paydays) is None


def test_next_known_payday_selects_first_later_recorded_date() -> None:
    paydays = (
        Payday(payday=date(2026, 4, 30), known_from=date(2026, 1, 1)),
        Payday(payday=date(2026, 3, 27), known_from=date(2026, 1, 1)),
    )

    assert next_known_payday(date(2026, 3, 15), paydays) == date(2026, 3, 27)


def test_schedule_rejects_invalid_weekday() -> None:
    with pytest.raises(ValueError, match="weekday"):
        ReviewSchedule(weekday=7, local_time=time(18), timezone="Europe/London")
