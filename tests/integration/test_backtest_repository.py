from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from safe_to_save.db import connect
from safe_to_save.history import load_history
from tests.factories import available_observation, withheld_observation

pytestmark = pytest.mark.integration


def test_run_roundtrip_rerun_is_noop_and_observations_immutable(repository):
    repository.import_bundle(load_history(Path("tests/fixtures/synthetic_history")))
    rows = (available_observation(500), withheld_observation())
    first = repository.save_backtest("synthetic-v2", "conservative_rules", 25_000, rows)
    second = repository.save_backtest("synthetic-v2", "conservative_rules", 25_000, rows[::-1])
    assert first.status == "completed"
    assert second.status == "noop"
    assert first.run_id == second.run_id
    assert repository.load_backtest_observations(first.run_id) == rows
    with connect(repository.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM backtest_runs").fetchone() == (1,)
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            connection.execute("UPDATE backtest_observations SET recommended_minor = 0")


def test_changed_same_identity_is_conflict_and_floor_changes_run_identity(repository):
    repository.import_bundle(load_history(Path("tests/fixtures/synthetic_history")))
    row = available_observation(500)
    first = repository.save_backtest("synthetic-v2", row.baseline_name, 25_000, (row,))
    with pytest.raises(ValueError, match="backtest conflict"):
        repository.save_backtest("synthetic-v2", row.baseline_name, 25_000,
                                 (replace(row, forecast_minor=1),))
    other = repository.save_backtest("synthetic-v2", row.baseline_name, 30_000, (row,))
    assert other.run_id != first.run_id
    assert repository.load_backtest_observations(first.run_id) == (row,)


def test_completed_run_rejects_new_observations(repository):
    repository.import_bundle(load_history(Path("tests/fixtures/synthetic_history")))
    row = available_observation(0)
    result = repository.save_backtest("synthetic-v2", row.baseline_name, 25_000, (row,))
    with (connect(repository.dsn) as connection,
          pytest.raises(psycopg.errors.RaiseException, match="completed.*immutable")):
        connection.execute("""INSERT INTO backtest_observations
            (run_id, checkpoint_at, status, withheld_reason)
            VALUES (%s, '2026-04-26T00:00:00Z', 'withheld', 'late_append')""",
            (result.run_id,),
        )
    assert repository.load_backtest_observations(result.run_id) == (row,)


def test_completed_run_cannot_be_reopened_or_metadata_changed(repository):
    repository.import_bundle(load_history(Path("tests/fixtures/synthetic_history")))
    row = available_observation(0)
    result = repository.save_backtest("synthetic-v2", row.baseline_name, 25_000, (row,))
    for assignment in ("status = 'running', completed_at = NULL, summary = NULL",
                       "summary = '{}'::jsonb", "floor_minor = 1"):
        with (connect(repository.dsn) as connection,
              pytest.raises(psycopg.errors.RaiseException, match="immutable")):
            connection.execute(f"UPDATE backtest_runs SET {assignment} WHERE run_id = %s",
                               (result.run_id,))
