from dataclasses import replace
from pathlib import Path

import pytest

from safe_to_save.baselines import TrailingMedianBaseline, UnavailableForecast
from safe_to_save.db import connect
from safe_to_save.history import load_history
from safe_to_save.repository import HistoryRepository
from tests.factories import baseline_case

pytestmark = pytest.mark.integration


@pytest.fixture
def baseline_repository(repository: HistoryRepository) -> HistoryRepository:
    repository.import_bundle(load_history(Path("tests/fixtures/synthetic_history")))
    current, _ = baseline_case()
    repository.save_example(current)
    return repository


def test_available_forecast_roundtrip_and_idempotence(baseline_repository) -> None:
    current, history = baseline_case()
    forecast = TrailingMedianBaseline().predict(current, history)
    baseline_repository.save_forecast(current, forecast)
    baseline_repository.save_forecast(current, forecast)
    assert baseline_repository.load_forecasts("synthetic-v2") == (
        (current.features.checkpoint_at, forecast),
    )
    with connect(baseline_repository.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM baseline_predictions").fetchone() == (1,)


@pytest.mark.parametrize("mutation", ["point", "provenance", "status"])
def test_conflicting_forecast_preserves_original(baseline_repository, mutation) -> None:
    current, history = baseline_case()
    forecast = TrailingMedianBaseline().predict(current, history)
    baseline_repository.save_forecast(current, forecast)
    if mutation == "point":
        changed = replace(forecast, point_minor=1)
    elif mutation == "provenance":
        changed = replace(forecast, training_days_to_payday=(9, 9, 9))
    else:
        changed = UnavailableForecast(forecast.baseline_name, "fewer_than_three_earlier_cycles")
    with pytest.raises(ValueError, match="baseline forecast conflict"):
        baseline_repository.save_forecast(current, changed)
    assert baseline_repository.load_forecasts("synthetic-v2")[0][1] == forecast


def test_withheld_forecast_retains_reason_and_null_money(baseline_repository) -> None:
    current, _ = baseline_case()
    forecast = TrailingMedianBaseline().predict(current, ())
    baseline_repository.save_forecast(current, forecast)
    baseline_repository.save_forecast(current, forecast)
    assert baseline_repository.load_forecasts("synthetic-v2")[0][1] == forecast
    with connect(baseline_repository.dsn) as connection:
        assert connection.execute("""SELECT status, withheld_reason, point_minor,
            upper_minor, sample_size FROM baseline_predictions""").fetchone() == (
                "withheld", "fewer_than_three_earlier_cycles", None, None, None,
            )


def test_load_filters_dataset_feature_and_baseline_versions(baseline_repository) -> None:
    current, history = baseline_case()
    forecast = TrailingMedianBaseline().predict(current, history)
    baseline_repository.save_forecast(current, forecast)
    baseline_repository.save_forecast(current, replace(forecast, point_minor=11_000),
                                      baseline_version="v2")
    baseline_repository.save_example(current, feature_version="offline-v3")
    baseline_repository.save_forecast(current, replace(forecast, point_minor=10_000),
                                      feature_version="offline-v3")
    assert baseline_repository.load_forecasts("synthetic-v2")[0][1] == forecast
    assert baseline_repository.load_forecasts("synthetic-v2", baseline_version="v2")[0][1] \
        == replace(forecast, point_minor=11_000)
    assert baseline_repository.load_forecasts("synthetic-v2", feature_version="offline-v3")[0][1] \
        == replace(forecast, point_minor=10_000)
    assert baseline_repository.load_forecasts("unknown") == ()
