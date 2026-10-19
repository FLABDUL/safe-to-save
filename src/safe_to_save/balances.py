"""Point-in-time balance reconstruction from complete history and later anchors."""

from collections.abc import Sequence
from datetime import UTC, datetime

from safe_to_save.contracts import (
    BalanceAnchor,
    CanonicalTransaction,
    HistoryBundle,
    HistoryManifest,
)


class CoverageError(ValueError):
    """Raised when evidence cannot support a balance at the requested instant."""


def _aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def reconstruct_balance_minor(
    checkpoint: datetime,
    anchors: Sequence[BalanceAnchor],
    transactions: Sequence[CanonicalTransaction],
    manifest: HistoryManifest,
) -> int:
    """Reverse all signed account flows from the earliest usable later anchor."""

    checkpoint_utc = _aware_utc(checkpoint, "checkpoint")
    if checkpoint_utc < manifest.coverage_start:
        raise CoverageError("checkpoint is before coverage_start")
    if checkpoint_utc > manifest.coverage_end:
        raise CoverageError("checkpoint is after coverage_end")

    # Import here to keep gate preparation's public validator as the single rule
    # without creating a module import cycle.
    from safe_to_save.gate_prepare import validate_anchor_reconciliation

    validate_anchor_reconciliation(HistoryBundle(
        manifest=manifest,
        raw_records=(),
        transactions=tuple(transactions),
        anchors=tuple(anchors),
        paydays=(),
        commitments=(),
    ), require_boundaries=False)

    later_anchors = tuple(
        anchor for anchor in anchors if anchor.captured_at >= checkpoint_utc
    )
    if not later_anchors:
        raise CoverageError("no balance anchor on or after checkpoint")
    anchor_time = min(anchor.captured_at for anchor in later_anchors)
    candidates = tuple(
        anchor for anchor in later_anchors if anchor.captured_at == anchor_time
    )
    if len({anchor.balance_minor for anchor in candidates}) != 1:
        raise CoverageError("ambiguous balance anchors at the same instant")
    anchor = candidates[0]
    if anchor.captured_at > manifest.coverage_end:
        raise CoverageError("anchor is after coverage_end")

    flow_after_checkpoint = sum(
        row.amount_minor
        for row in transactions
        if checkpoint_utc < row.occurred_at <= anchor.captured_at
    )
    return anchor.balance_minor - flow_after_checkpoint
