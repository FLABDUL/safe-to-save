from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from safe_to_save.contracts import Commitment
from safe_to_save.features import CompletedExample, WithheldExample, build_example, integer_pstdev
from tests.factories import history_case, replace_future_expense, transaction


def test_future_amount_changes_label_but_not_features() -> None:
    case = history_case()
    original = build_example(**case)
    changed = build_example(**replace_future_expense(case, -5000))
    assert isinstance(original, CompletedExample)
    assert changed.features == original.features
    assert changed.target_variable_spend_minor == original.target_variable_spend_minor + 5000
    assert case["transactions"][-1].amount_minor == -1000


def test_target_nets_refunds_and_excludes_non_variable_flows() -> None:
    rows = [
        transaction("expense", "2026-04-20T12:00:00Z", -10_000),
        transaction("refund", "2026-04-21T12:00:00Z", 2000),
        transaction("bill", "2026-04-22T12:00:00Z", -4000, committed=True),
        transaction("pot", "2026-04-23T12:00:00Z", -3000, economic_type="savings_transfer"),
        transaction("internal", "2026-04-23T12:00:00Z", -3000, economic_type="internal_transfer"),
        transaction("adjustment", "2026-04-23T12:00:00Z", -3000, economic_type="adjustment"),
        transaction("income", "2026-04-23T12:00:00Z", 100_000, economic_type="income"),
    ]
    example = build_example(**history_case(rows))
    assert example.target_variable_spend_minor == 8000


def test_refund_only_target_is_clamped_to_zero() -> None:
    example = build_example(**history_case([
        transaction("refund", "2026-04-21T12:00:00Z", 2000),
    ]))
    assert example.target_variable_spend_minor == 0


def test_trailing_features_use_complete_elapsed_day_bins() -> None:
    example = build_example(**history_case())
    assert example.features.variable_spend_7d_minor == 700
    assert example.features.variable_spend_28d_minor == 2800
    assert example.features.daily_spend_stddev_28d_minor == 0
    assert example.features.days_to_payday == 9
    assert example.features.coverage_age_days == 60
    assert example.features.weekday == 6
    assert example.features.month == 4


def test_feature_boundaries_exclude_old_rows_include_checkpoint_and_net_refunds() -> None:
    case = history_case(())
    at = case["checkpoint_at"]
    case["transactions"] = (
        transaction("old", (at - timedelta(days=28)).isoformat(), -9999),
        transaction("seven", (at - timedelta(days=7)).isoformat(), -500),
        transaction("recent", (at - timedelta(days=1)).isoformat(), -1000),
        transaction("now-refund", at.isoformat(), 200),
        transaction("bill", at.isoformat(), -3000, committed=True),
        transaction("transfer", at.isoformat(), -3000, economic_type="internal_transfer"),
    )
    example = build_example(**case)
    assert example.features.variable_spend_7d_minor == 800
    assert example.features.variable_spend_28d_minor == 1300
    assert example.target_variable_spend_minor == 0


def test_deviation_includes_all_zero_spend_days() -> None:
    case = history_case(())
    case["transactions"] = (transaction("spike", "2026-04-18T12:00:00Z", -2800),)
    assert build_example(**case).features.daily_spend_stddev_28d_minor == 520


@pytest.mark.parametrize("invalid_balance", [True, 1.5])
def test_non_integer_balance_is_withheld(invalid_balance: object) -> None:
    case = {**history_case(), "balance_minor": invalid_balance}
    assert build_example(**case) == WithheldExample(
        "synthetic-v2", case["checkpoint_at"], "invalid_balance",
    )


def test_future_commitment_knowledge_does_not_leak_into_reserve() -> None:
    case = history_case()
    at = case["checkpoint_at"]
    case["commitments"] = tuple(
        Commitment(commitment_id=name, known_from=known, due_at=due,
                   amount_minor=-amount, description="Synthetic bill")
        for name, known, due, amount in [
            ("known", at, at + timedelta(days=1), 1000),
            ("future", at + timedelta(seconds=1), at + timedelta(days=1), 2000),
            ("paid", at - timedelta(days=1), at, 3000),
            ("payday", at, datetime(2026, 4, 28, 12, tzinfo=UTC), 4000),
            ("later", at, datetime(2026, 4, 29, 12, tzinfo=UTC), 5000),
        ]
    )
    assert build_example(**case).features.known_commitments_minor == 1000


@pytest.mark.parametrize("due_at,expected", [
    ("2026-04-27T22:59:59.999999Z", 123),
    ("2026-04-27T23:00:00Z", 0),
    ("2026-04-27T23:00:00.000001Z", 0),
    ("2026-04-28T00:00:00+01:00", 0),
    ("2026-04-28T12:00:00Z", 0),
])
def test_commitments_end_exclusively_at_london_payday_midnight(due_at: str, expected: int) -> None:
    case = history_case(())
    case["commitments"] = (Commitment(
        commitment_id="boundary", known_from=case["checkpoint_at"], due_at=due_at,
        amount_minor=-123, description="Synthetic boundary bill",
    ),)
    assert build_example(**case).features.known_commitments_minor == expected


def test_target_ends_at_london_midnight_before_payday() -> None:
    rows = [
        transaction("last", "2026-04-27T22:59:59Z", -123),
        transaction("boundary", "2026-04-27T23:00:00Z", -5000),
        transaction("later", "2026-04-29T12:00:00Z", -10_000),
    ]
    assert build_example(**history_case(rows)).target_variable_spend_minor == 123


@pytest.mark.parametrize("changes,reason", [
    ({"balance_minor": None}, "missing_balance"),
    ({"payday": None}, "missing_payday"),
    ({"payday": date(2026, 4, 19)}, "invalid_payday"),
    ({"coverage_start": None}, "missing_coverage"),
    ({"coverage_start": datetime(2026, 4, 20, tzinfo=UTC)}, "checkpoint_before_coverage"),
    ({"coverage_start": datetime(2026, 4, 1, tzinfo=UTC)}, "insufficient_trailing_history"),
])
def test_missing_or_inadequate_inputs_are_withheld(changes: dict, reason: str) -> None:
    case = {**history_case(), **changes}
    assert build_example(**case) == WithheldExample("synthetic-v2", case["checkpoint_at"], reason)


def test_exactly_28_elapsed_days_is_sufficient() -> None:
    case = history_case()
    case["coverage_start"] = case["checkpoint_at"] - timedelta(days=28)
    assert isinstance(build_example(**case), CompletedExample)
    case["coverage_start"] += timedelta(microseconds=1)
    assert isinstance(build_example(**case), WithheldExample)


def test_calendar_features_follow_london_date_and_timestamps_normalise_to_utc() -> None:
    case = history_case(())
    case["checkpoint_at"] = datetime.fromisoformat("2026-05-01T00:30:00+01:00")
    case["payday"] = date(2026, 5, 2)
    result = build_example(**case)
    assert result.features.checkpoint_at == datetime(2026, 4, 30, 23, 30, tzinfo=UTC)
    assert result.features.checkpoint_at.tzinfo == UTC
    assert (result.features.days_to_payday, result.features.weekday, result.features.month) == (1, 4, 5)


@pytest.mark.parametrize("field", ["checkpoint_at", "coverage_start"])
def test_naive_input_times_are_rejected(field: str) -> None:
    case = history_case()
    case[field] = case[field].replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        build_example(**case)


@pytest.mark.parametrize("values,expected", [
    ((), 0), ((1, 2), 1), ((0, 3), 2), ((0, 10), 5),
    ((10**18, 10**18 + 2), 1),
])
def test_integer_population_deviation_avoids_float_money(values: tuple, expected: int) -> None:
    assert integer_pstdev(values) == expected


@given(st.lists(st.tuples(
    st.integers(-1_000_000, 1_000_000),
    st.sampled_from(["expense", "income", "adjustment", "internal_transfer", "savings_transfer"]),
    st.booleans(),
), max_size=30))
def test_arbitrary_future_flows_cannot_change_features(flows: list) -> None:
    original = build_example(**history_case(()))
    rows = tuple(
        transaction(f"future-{index}", "2026-04-20T12:00:00Z", amount, kind, committed)
        for index, (amount, kind, committed) in enumerate(flows)
    )
    changed = build_example(**history_case(rows))
    assert changed.features == original.features
    eligible_flow = sum(amount for amount, kind, committed in flows
                        if kind == "expense" and not committed)
    assert changed.target_variable_spend_minor == max(0, -eligible_flow)
