from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from safe_to_save.metrics import summarise
from tests.factories import available_observation, scored_observation, withheld_observation


def test_unavailable_forecasts_counted_but_excluded_from_error():
    result = summarise((available_observation(error_minor=500), withheld_observation()))
    assert (result.checkpoints_total, result.checkpoints_scored, result.checkpoints_withheld) \
        == (2, 1, 1)
    assert result.mean_absolute_error_minor == 500
    assert result.coverage_ratio == Decimal("0.5")
    assert result.withheld_reasons == {"insufficient_history": 1}


def test_useful_yield_is_capped_by_hindsight_safe_amount():
    result = summarise((scored_observation(recommended=8_000, hindsight_safe=5_000),))
    assert result.useful_savings_minor == result.hindsight_safe_minor == 5_000
    assert result.useful_yield_ratio == Decimal(1)
    assert result.shortfall_violation_count == 1
    assert result.reversal_count == 1
    assert result.reversal_total_minor == 3000


def test_empty_denominators_are_null():
    result = summarise(())
    assert result.mean_absolute_error_minor is None
    assert result.shortfall_violation_rate is None
    assert result.useful_yield_ratio is None
    assert result.coverage_ratio is None
    assert result.forecast_upper_coverage_ratio is None


def test_mixed_baselines_rejected():
    with pytest.raises(ValueError, match="one baseline"):
        summarise((available_observation(0), replace(available_observation(0), baseline_name="x")))


def test_median_is_decimal_and_changes_are_chronological_not_across_gaps():
    first = available_observation(1)
    second = replace(available_observation(2), checkpoint_at=first.checkpoint_at + timedelta(days=7),
                     recommended_minor=first.recommended_minor + 3)
    gap = replace(withheld_observation(), checkpoint_at=first.checkpoint_at + timedelta(days=14))
    last = replace(first, checkpoint_at=first.checkpoint_at + timedelta(days=21),
                   recommended_minor=99999)
    result = summarise((last, second, first, gap))
    assert result.median_absolute_error_minor == Decimal(1)
    assert result.median_absolute_recommendation_change_minor == Decimal(3)
    assert summarise((first, second)).median_absolute_error_minor == Decimal("1.5")
