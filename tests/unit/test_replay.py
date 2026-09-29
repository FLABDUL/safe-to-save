from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from safe_to_save.baselines import UnavailableForecast
from safe_to_save.replay import actual_balance_path, replay_one
from tests.factories import sample_actual_path, sample_example, sample_forecast, transaction


def test_recommendation_reserves_commitments_forecast_and_floor():
    observation = replay_one(sample_example(), sample_forecast(),
                             (100_000, 85_000, 63_000, 55_000), 25_000)
    assert observation.recommended_minor == 25_000
    assert observation.minimum_balance_after_saving_minor == 30_000
    assert observation.shortfall_violation is False
    assert observation.reversal_required_minor == 0


def test_realised_breach_is_exposed_without_hindsight_capping():
    result = replay_one(sample_example(), sample_forecast(), (100_000, 30_000, 60_000), 25_000)
    assert result.recommended_minor == 25_000
    assert result.minimum_balance_after_saving_minor == 5_000
    assert result.hindsight_safe_minor == 5_000
    assert result.shortfall_violation is True
    assert result.reversal_required_minor == 20_000


def test_floor_equality_is_safe():
    result = replay_one(sample_example(), sample_forecast(), (100_000, 50_000), 25_000)
    assert result.shortfall_violation is False


@given(*(st.integers(min_value=0, max_value=10**15) for _ in range(5)))
def test_higher_floor_never_increases_recommendation(balance, commitments, upper, low, extra):
    example = sample_example(balance_minor=balance, known_commitments_minor=commitments)
    forecast = sample_forecast(upper_minor=upper)
    a = replay_one(example, forecast, (balance,), low)
    b = replay_one(example, forecast, (balance,), low + extra)
    assert 0 <= b.recommended_minor <= a.recommended_minor


def test_negative_floor_is_rejected():
    with pytest.raises(ValueError, match="floor_minor must be non-negative"):
        replay_one(sample_example(), sample_forecast(), sample_actual_path(), -1)


@pytest.mark.parametrize("floor", [None, True, 1.5])
def test_missing_or_invalid_floor_withholds(floor):
    assert replay_one(sample_example(), sample_forecast(), sample_actual_path(), floor).status \
        == "withheld"


@pytest.mark.parametrize("path, reason", [((), "missing_actual_path"),
                                          ((1, 0), "path_balance_mismatch")])
def test_invalid_path_withholds(path, reason):
    result = replay_one(sample_example(), sample_forecast(), path, 25_000)
    assert result.withheld_reason == reason
    assert result.recommended_minor is None


def test_unavailable_forecast_is_not_zero():
    result = replay_one(sample_example(), UnavailableForecast("conservative_rules", "too_short"),
                        sample_actual_path(), 25_000)
    assert result.withheld_reason == "too_short"
    assert result.forecast_minor is None


def test_missing_forecast_is_withheld():
    result = replay_one(sample_example(), None, sample_actual_path(), 25_000)
    assert result.withheld_reason == "missing_forecast"
    assert result.recommended_minor is None


@pytest.mark.parametrize("target", [None, 1.5, True, -1])
def test_invalid_realised_target_is_withheld(target):
    example = replace(sample_example(), target_variable_spend_minor=target)
    result = replay_one(example, sample_forecast(), sample_actual_path(), 25_000)
    assert result.status == "withheld"
    assert result.withheld_reason == "invalid_actual_variable_spend"
    assert result.actual_variable_spend_minor is None
    assert result.recommended_minor is None


@pytest.mark.parametrize("field, value", [("balance_minor", None),
                                         ("known_commitments_minor", None),
                                         ("known_commitments_minor", -1)])
def test_invalid_features_withhold(field, value):
    example = sample_example()
    example = replace(example, features=replace(example.features, **{field: value}))
    assert replay_one(example, sample_forecast(), sample_actual_path(), 25_000).status \
        == "withheld"


def test_signed_path_orders_all_flows_and_excludes_payday_boundary():
    example = sample_example()
    start = example.features.checkpoint_at
    end = datetime(2026, 4, 27, 23, tzinfo=UTC)
    rows = (transaction("later", "2026-04-23T12:00:00Z", 500, "income"),
            transaction("earlier", "2026-04-22T12:00:00Z", -2000, "savings_transfer"),
            transaction("payday", end.isoformat(), 9000, "income"))
    assert actual_balance_path(example, rows, start, end) == (100_000, 98_000, 98_500)
    assert actual_balance_path(example, rows, start, start) is None
