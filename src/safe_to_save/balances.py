"""Point-in-time balance reconstruction from complete history and later anchors."""

from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import pairwise

from safe_to_save.contracts import BalanceAnchor, CanonicalTransaction, HistoryManifest


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

    covered_anchors = sorted(
        (anchor for anchor in anchors
         if manifest.coverage_start <= anchor.captured_at <= manifest.coverage_end),
        key=lambda anchor: anchor.captured_at,
    )
    for earlier, later in pairwise(covered_anchors):
        net_flow = sum(
            row.amount_minor for row in transactions
            if earlier.captured_at < row.occurred_at <= later.captured_at
        )
        if earlier.balance_minor + net_flow != later.balance_minor:
            if earlier.captured_at == later.captured_at:
                raise CoverageError("ambiguous balance anchors at the same instant")
            raise CoverageError("inconsistent balance anchors across covered interval")

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
