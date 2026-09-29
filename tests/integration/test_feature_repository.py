from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from safe_to_save.db import connect
from safe_to_save.features import WithheldExample, build_example
from safe_to_save.history import load_history
from safe_to_save.repository import HistoryRepository
from tests.factories import history_case

pytestmark = pytest.mark.integration


@pytest.fixture
def feature_repository(repository: HistoryRepository) -> HistoryRepository:
    repository.import_bundle(load_history(Path("tests/fixtures/synthetic_history")))
    return repository


def test_complete_example_roundtrip_is_idempotent(feature_repository: HistoryRepository) -> None:
    example = build_example(**history_case())
    feature_repository.save_example(example)
    feature_repository.save_example(example)
    assert feature_repository.load_completed_examples("synthetic-v2") == (example,)
    with connect(feature_repository.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM feature_examples").fetchone() == (1,)


@pytest.mark.parametrize("mutation", ["feature", "target", "status"])
def test_conflicting_example_never_overwrites_history(
    feature_repository: HistoryRepository, mutation: str,
) -> None:
    example = build_example(**history_case())
    feature_repository.save_example(example)
    if mutation == "feature":
        changed = replace(example, features=replace(example.features, balance_minor=1))
    elif mutation == "target":
        changed = replace(example, target_variable_spend_minor=1)
    else:
        changed = WithheldExample(example.dataset_id, example.features.checkpoint_at, "missing_balance")
    with pytest.raises(ValueError, match="feature example conflict"):
        feature_repository.save_example(changed)
    assert feature_repository.load_completed_examples("synthetic-v2") == (example,)


@pytest.mark.parametrize("balance,reason", [
    (None, "missing_balance"), (True, "invalid_balance"), (1.5, "invalid_balance"),
])
def test_withheld_example_is_persisted_and_excluded_from_completed_rows(
    feature_repository: HistoryRepository, balance: object, reason: str,
) -> None:
    case = {**history_case(), "balance_minor": balance}
    example = build_example(**case)
    feature_repository.save_example(example)
    feature_repository.save_example(example)
    assert feature_repository.load_completed_examples("synthetic-v2") == ()
    with connect(feature_repository.dsn) as connection:
        assert connection.execute("""SELECT status, withheld_reason, features,
            target_variable_spend_minor FROM feature_examples""").fetchone() == (
                "withheld", reason, None, None,
            )
    with pytest.raises(ValueError, match="feature example conflict"):
        feature_repository.save_example(replace(example, reason="missing_payday"))


def test_inconsistent_cycle_payday_cannot_be_silently_lost(
    feature_repository: HistoryRepository,
) -> None:
    example = build_example(**history_case())
    with pytest.raises(ValueError, match="cycle payday"):
        feature_repository.save_example(replace(
            example, cycle_payday=example.cycle_payday + timedelta(days=1),
        ))
    assert feature_repository.load_completed_examples("synthetic-v2") == ()


def test_load_filters_dataset_and_version_and_orders_checkpoints(
    feature_repository: HistoryRepository,
) -> None:
    example = build_example(**history_case())
    earlier = replace(example, features=replace(
        example.features, checkpoint_at=example.features.checkpoint_at - timedelta(days=7),
    ))
    feature_repository.save_example(example)
    feature_repository.save_example(earlier)
    feature_repository.save_example(example, feature_version="offline-v3")
    assert feature_repository.load_completed_examples("synthetic-v2") == (earlier, example)
    assert feature_repository.load_completed_examples("synthetic-v2", "offline-v3") == (example,)
    assert feature_repository.load_completed_examples("unknown") == ()
