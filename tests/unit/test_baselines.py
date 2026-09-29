from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from safe_to_save.baselines import (
    ComparablePositionBaseline,
    ConservativeRulesBaseline,
    TrailingMedianBaseline,
    UnavailableForecast,
    nearest_rank,
)
from tests.factories import baseline_case

STRATEGIES = (TrailingMedianBaseline, ComparablePositionBaseline, ConservativeRulesBaseline)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_exact_forecast_and_chronological_provenance(strategy) -> None:
    current, history = baseline_case()
    forecast = strategy().predict(current, history)
    assert forecast.point_minor == (14_000 if strategy is ConservativeRulesBaseline else 12_000)
    assert forecast.upper_minor == 14_000
    assert forecast.sample_size == 3
    assert forecast.training_paydays == (
        date(2026, 1, 28), date(2026, 2, 27), date(2026, 3, 27),
    )
    assert forecast.training_days_to_payday == (8, 9, 10)


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("count", [0, 1, 2])
def test_minimum_is_three_distinct_cycles(strategy, count: int) -> None:
    current, history = baseline_case()
    result = strategy().predict(current, history[1:1 + count] * 3)
    assert isinstance(result, UnavailableForecast)
    assert result.reason == (
        "fewer_than_three_earlier_cycles" if strategy is TrailingMedianBaseline
        else "fewer_than_three_comparable_cycles"
    )


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_future_unfinished_same_cycle_and_other_dataset_cannot_leak(strategy) -> None:
    current, history = baseline_case()
    unfinished = replace(history[-1], cycle_payday=date(2026, 4, 25), features=replace(
        history[-1].features, payday=date(2026, 4, 25),
    ), target_variable_spend_minor=999_999)
    future_checkpoint = replace(history[-1], features=replace(
        history[-1].features, checkpoint_at=current.features.checkpoint_at,
    ), target_variable_spend_minor=999_999)
    other = replace(history[-1], dataset_id="another-dataset", target_variable_spend_minor=999_999)
    same_cycle = replace(current, target_variable_spend_minor=999_999)
    future = replace(current, cycle_payday=date(2026, 5, 28))
    expected = strategy().predict(current, history)
    assert strategy().predict(current, (*history, unfinished, future_checkpoint,
                                        other, same_cycle, future)) == expected
    assert strategy().predict(replace(current, target_variable_spend_minor=999_999), history) == expected


def test_trailing_chooses_latest_checkpoint_per_cycle() -> None:
    current, history = baseline_case()
    later = replace(history[-1], features=replace(
        history[-1].features, checkpoint_at=history[-1].features.checkpoint_at + timedelta(days=7),
        days_to_payday=3,
    ), target_variable_spend_minor=1_000)
    result = TrailingMedianBaseline().predict(current, (*history, later))
    assert result.point_minor == 10_000
    assert result.upper_minor == 12_000
    assert result.training_days_to_payday == (8, 9, 3)


@pytest.mark.parametrize("strategy", [ComparablePositionBaseline, ConservativeRulesBaseline])
def test_comparable_selects_nearest_position_then_latest_and_caps_five(strategy) -> None:
    current, history = baseline_case()
    base = history[1]
    more = tuple(replace(base, cycle_payday=date(2025, month, 28), features=replace(
        base.features, payday=date(2025, month, 28),
        checkpoint_at=base.features.checkpoint_at.replace(year=2025, month=month),
    ), target_variable_spend_minor=amount)
        for month, amount in [(9, 999_999), (10, 1_000), (11, 2_000)])
    # Both positions are one day away; the later checkpoint wins deterministically.
    tied = replace(history[-1], features=replace(
        history[-1].features, days_to_payday=8,
        checkpoint_at=history[-1].features.checkpoint_at + timedelta(days=2),
    ), target_variable_spend_minor=13_000)
    # A perfect position beats a later, less comparable checkpoint.
    nearest = replace(history[-2], features=replace(history[-2].features, days_to_payday=8,
        checkpoint_at=history[-2].features.checkpoint_at + timedelta(days=1)),
        target_variable_spend_minor=999_999)
    inputs = (*history, *more, tied, nearest)
    result = strategy().predict(current, inputs)
    assert result == strategy().predict(current, tuple(reversed(inputs)))
    assert result.sample_size == 5
    assert result.training_paydays[0] == date(2025, 10, 28)
    assert result.upper_minor == 13_000
    assert result.point_minor == (13_000 if strategy is ConservativeRulesBaseline else 10_000)


@pytest.mark.parametrize("strategy", [ComparablePositionBaseline, ConservativeRulesBaseline])
def test_outside_one_day_is_not_a_comparable(strategy) -> None:
    current, history = baseline_case()
    history = tuple(replace(row, features=replace(row.features, days_to_payday=11))
                    for row in history)
    assert isinstance(strategy().predict(current, history), UnavailableForecast)


def test_conservative_pace_rounds_up_exactly() -> None:
    current, history = baseline_case()
    current = replace(current, features=replace(current.features, variable_spend_7d_minor=10_001))
    result = ConservativeRulesBaseline().predict(current, history)
    # 10,001 * 9 * 1.25 / 7 = 450,045 / 28 = 16,073 + 1/28.
    assert result.point_minor == result.upper_minor == 16_074


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_negative_targets_remain_signed_and_do_not_become_missing(strategy) -> None:
    current, history = baseline_case()
    current = replace(current, features=replace(current.features, variable_spend_7d_minor=-700))
    history = tuple(replace(row, target_variable_spend_minor=-1000) for row in history)
    result = strategy().predict(current, history)
    assert result.point_minor == result.upper_minor == -1000


def test_even_median_keeps_large_minor_units_exact() -> None:
    current, history = baseline_case()
    huge = 10 ** 30
    fourth = replace(history[1], cycle_payday=date(2025, 11, 28), features=replace(
        history[1].features, payday=date(2025, 11, 28),
        checkpoint_at=history[1].features.checkpoint_at.replace(year=2025, month=11),
    ))
    inputs = tuple(replace(row, target_variable_spend_minor=huge + index)
                   for index, row in enumerate((*history[1:], fourth)))
    assert ComparablePositionBaseline().predict(current, inputs).point_minor == huge + 2


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_conflicting_checkpoint_cannot_make_result_depend_on_input_order(strategy) -> None:
    current, history = baseline_case()
    conflict = replace(history[-1], target_variable_spend_minor=999_999)
    with pytest.raises(ValueError, match="conflicting historical checkpoint"):
        strategy().predict(current, (*history, conflict))


def test_nearest_rank_boundary_and_empty_input() -> None:
    assert nearest_rank([30, 10, 20], Decimal("0.9")) == 30
    assert nearest_rank([-30, -10, -20], Decimal("0.5")) == -20
    assert nearest_rank([30, 10, 20], Decimal(0)) == 10
    with pytest.raises(ValueError):
        nearest_rank([], Decimal("0.9"))
    with pytest.raises(ValueError):
        nearest_rank([1], Decimal("1.1"))


@pytest.mark.parametrize("spend,expected", [(-99, -495), (1, 5)])
def test_exact_integral_pace_does_not_gain_a_penny_from_decimal_division(spend, expected) -> None:
    current, history = baseline_case()
    current = replace(current, features=replace(
        current.features, variable_spend_7d_minor=spend, days_to_payday=28,
    ))
    history = tuple(replace(row, features=replace(row.features, days_to_payday=28),
                            target_variable_spend_minor=-1000) for row in history)
    assert ConservativeRulesBaseline().predict(current, history).point_minor == expected
