from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from safe_to_save.baselines import BaselineForecast
from safe_to_save.contracts import (
    BalanceAnchor,
    CanonicalTransaction,
    EconomicType,
    HistoryManifest,
)
from safe_to_save.features import CompletedExample, FeatureVector
from safe_to_save.replay import ReplayObservation, replay_one


def transaction(
    transaction_id: str = "synthetic-tx",
    occurred_at: str = "2026-01-10T12:00:00+00:00",
    amount_minor: int = -1_000,
    economic_type: str = "expense",
    committed: bool = False,
) -> CanonicalTransaction:
    return CanonicalTransaction(
        transaction_id=transaction_id,
        occurred_at=occurred_at,
        amount_minor=amount_minor,
        currency="GBP",
        status="settled",
        economic_type=EconomicType(economic_type),
        committed=committed,
    )


def balance_anchor(
    captured_at: str = "2026-01-20T00:00:00+00:00",
    balance_minor: int = 100_000,
) -> BalanceAnchor:
    return BalanceAnchor(captured_at=captured_at, balance_minor=balance_minor, currency="GBP")


def manifest() -> HistoryManifest:
    return HistoryManifest(
        dataset_id="synthetic-v2",
        account_id="synthetic-current",
        currency="GBP",
        coverage_start="2025-09-01T00:00:00+00:00",
        coverage_end="2026-05-01T00:00:00+00:00",
    )


def history_case(
    future_transactions: Sequence[CanonicalTransaction] | None = None,
) -> dict:
    """Invented daily history and a completed nine-day target window."""
    checkpoint = datetime(2026, 4, 19, 17, tzinfo=UTC)
    trailing = tuple(
        transaction(
            f"synthetic-trailing-{index}",
            (checkpoint - timedelta(days=index, hours=12)).isoformat(),
            -100,
        )
        for index in range(28)
    )
    if future_transactions is None:
        future_transactions = (transaction("synthetic-future", "2026-04-20T12:00:00Z", -1000),)
    return {
        "dataset_id": "synthetic-v2",
        "checkpoint_at": checkpoint,
        "payday": date(2026, 4, 28),
        "balance_minor": 100_000,
        "coverage_start": checkpoint - timedelta(days=60),
        "transactions": (*trailing, *future_transactions),
        "commitments": (),
    }


def replace_future_expense(case: dict, delta_minor: int) -> dict:
    rows = list(case["transactions"])
    for index, row in enumerate(rows):
        if (row.occurred_at > case["checkpoint_at"]
                and row.economic_type == EconomicType.EXPENSE and not row.committed):
            rows[index] = row.model_copy(update={"amount_minor": row.amount_minor + delta_minor})
            return {**case, "transactions": tuple(rows)}
    raise ValueError("No future variable expense in synthetic case")


def baseline_case() -> tuple[CompletedExample, tuple[CompletedExample, ...]]:
    """Invented cycles: three comparable targets with median 12,000 minor units."""
    def example(payday: date, days: int, target: int) -> CompletedExample:
        checkpoint = datetime.combine(payday, datetime.min.time(), tzinfo=UTC)
        checkpoint -= timedelta(days=days)
        return CompletedExample("synthetic-v2", payday, FeatureVector(
            checkpoint_at=checkpoint, payday=payday, days_to_payday=days,
            balance_minor=100_000, known_commitments_minor=20_000,
            variable_spend_7d_minor=7_000, variable_spend_28d_minor=28_000,
            daily_spend_stddev_28d_minor=100, weekday=checkpoint.weekday(),
            month=checkpoint.month, coverage_age_days=120,
        ), target)

    return example(date(2026, 4, 28), 9, 13_000), (
        example(date(2025, 12, 28), 20, 99_000),
        example(date(2026, 1, 28), 8, 10_000),
        example(date(2026, 2, 27), 9, 12_000),
        example(date(2026, 3, 27), 10, 14_000),
    )


def sample_example(balance_minor=100_000, known_commitments_minor=20_000):
    current, _ = baseline_case()
    return replace(current, features=replace(current.features, balance_minor=balance_minor,
                                             known_commitments_minor=known_commitments_minor))


def sample_forecast(upper_minor=30_000):
    return BaselineForecast("conservative_rules", upper_minor, upper_minor, 3,
                            (date(2026, 1, 28), date(2026, 2, 27), date(2026, 3, 27)), (8, 9, 10))


def sample_actual_path():
    return (100_000, 85_000, 63_000, 55_000)


def available_observation(error_minor):
    result = replay_one(sample_example(), sample_forecast(), sample_actual_path(), 25_000)
    return replace(result, actual_variable_spend_minor=result.forecast_minor - error_minor)


def withheld_observation():
    example = sample_example()
    return ReplayObservation(example.features.checkpoint_at + timedelta(days=7),
                             example.cycle_payday, "conservative_rules", "withheld",
                             "insufficient_history")


def scored_observation(recommended, hindsight_safe):
    return replace(available_observation(0), recommended_minor=recommended,
                   hindsight_safe_minor=hindsight_safe,
                   minimum_balance_after_saving_minor=25_000 + hindsight_safe - recommended,
                   shortfall_violation=recommended > hindsight_safe,
                   reversal_required_minor=max(0, recommended - hindsight_safe))
