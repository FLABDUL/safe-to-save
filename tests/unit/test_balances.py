from datetime import UTC, datetime
from pathlib import Path

import pytest

from safe_to_save.balances import CoverageError, reconstruct_balance_minor
from safe_to_save.history import load_history
from tests.factories import balance_anchor, manifest, transaction


@pytest.mark.parametrize("checkpoint", ["2026-01-10", "2026-01-15", "2026-01-20"])
def test_inconsistent_covered_anchors_withhold_at_every_checkpoint(checkpoint: str) -> None:
    with pytest.raises(CoverageError, match="inconsistent balance anchors"):
        reconstruct_balance_minor(
            datetime.fromisoformat(checkpoint).replace(tzinfo=UTC),
            (balance_anchor("2026-01-10T00:00:00Z", 100_000),
             balance_anchor("2026-01-20T00:00:00Z", 100_000)),
            (transaction("flow", "2026-01-12T00:00:00Z", -2000),), manifest(),
        )


def test_consistent_anchors_reconcile_every_signed_flow_with_closed_right_boundary() -> None:
    anchors = (balance_anchor("2026-01-10T00:00:00Z", 100_000),
               balance_anchor("2026-01-20T00:00:00Z", 103_500))
    rows = (
        transaction("already-in-opening", "2026-01-10T00:00:00Z", -5000),
        transaction("transfer", "2026-01-12T00:00:00Z", -2000, "savings_transfer"),
        transaction("income", "2026-01-13T00:00:00Z", 5000, "income"),
        transaction("adjustment", "2026-01-20T00:00:00Z", 500, "adjustment"),
    )
    assert reconstruct_balance_minor(
        datetime(2026, 1, 15, tzinfo=UTC), anchors, rows, manifest(),
    ) == 103_000


def test_public_synthetic_anchors_agree_with_all_signed_account_flows() -> None:
    bundle = load_history(Path("tests/fixtures/synthetic_history"))
    opening, closing = bundle.anchors
    assert closing.balance_minor == opening.balance_minor + sum(
        row.amount_minor for row in bundle.transactions
        if opening.captured_at < row.occurred_at <= closing.captured_at
    )


def test_reconstructs_backwards_from_later_anchor() -> None:
    checkpoint = datetime(2026, 1, 10, tzinfo=UTC)
    anchor = balance_anchor("2026-01-20T00:00:00Z", 100_000)
    later = [
        transaction("a", "2026-01-12T00:00:00Z", -2_000),
        transaction("b", "2026-01-15T00:00:00Z", 5_000),
    ]

    assert reconstruct_balance_minor(checkpoint, (anchor,), later, manifest()) == 97_000


def test_checkpoint_before_coverage_is_withheld() -> None:
    with pytest.raises(CoverageError, match="before coverage_start"):
        reconstruct_balance_minor(
            datetime(2025, 8, 1, tzinfo=UTC), (), (), manifest()
        )


def test_checkpoint_after_coverage_is_withheld() -> None:
    with pytest.raises(CoverageError, match="after coverage_end"):
        reconstruct_balance_minor(
            datetime(2026, 5, 2, tzinfo=UTC), (), (), manifest()
        )


def test_missing_later_anchor_is_withheld() -> None:
    with pytest.raises(CoverageError, match="no balance anchor"):
        reconstruct_balance_minor(
            datetime(2026, 1, 10, tzinfo=UTC),
            (balance_anchor("2026-01-09T00:00:00Z", 100_000),),
            (),
            manifest(),
        )


def test_anchor_after_coverage_is_withheld() -> None:
    with pytest.raises(CoverageError, match="anchor is after coverage_end"):
        reconstruct_balance_minor(
            datetime(2026, 4, 30, tzinfo=UTC),
            (balance_anchor("2026-05-02T00:00:00Z", 100_000),),
            (),
            manifest(),
        )


def test_conflicting_anchors_at_same_instant_are_withheld() -> None:
    with pytest.raises(CoverageError, match="ambiguous balance anchors"):
        reconstruct_balance_minor(
            datetime(2026, 1, 10, tzinfo=UTC),
            (
                balance_anchor("2026-01-20T00:00:00Z", 100_000),
                balance_anchor("2026-01-20T00:00:00Z", 101_000),
            ),
            (),
            manifest(),
        )


def test_all_signed_flows_are_used_regardless_of_economic_type() -> None:
    checkpoint = datetime(2026, 1, 10, tzinfo=UTC)
    rows = (
        transaction(
            "internal", "2026-01-11T00:00:00Z", -1_000,
            economic_type="internal_transfer",
        ),
        transaction(
            "savings", "2026-01-12T00:00:00Z", -2_000,
            economic_type="savings_transfer",
        ),
        transaction(
            "adjustment", "2026-01-13T00:00:00Z", 500,
            economic_type="adjustment",
        ),
    )

    assert reconstruct_balance_minor(
        checkpoint,
        (balance_anchor("2026-01-20T00:00:00Z", 100_000),),
        rows,
        manifest(),
    ) == 102_500


def test_flow_at_checkpoint_is_not_applied_twice() -> None:
    checkpoint = datetime(2026, 1, 10, tzinfo=UTC)
    rows = (
        transaction("at-checkpoint", "2026-01-10T00:00:00Z", -5_000),
        transaction("later", "2026-01-11T00:00:00Z", -2_000),
    )

    assert reconstruct_balance_minor(
        checkpoint,
        (balance_anchor("2026-01-20T00:00:00Z", 100_000),),
        rows,
        manifest(),
    ) == 102_000
