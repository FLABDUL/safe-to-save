from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from safe_to_save.features import CompletedExample, WithheldExample
from safe_to_save.history import load_history
from safe_to_save.paycycles import ReviewSchedule
from safe_to_save.workflow import build_examples, write_reports

SCHEDULE = ReviewSchedule(6, time(18), "Europe/London")


def test_incomplete_target_is_withheld_before_label_construction():
    bundle = load_history(Path("tests/fixtures/synthetic_history"))
    bundle = bundle.model_copy(update={"manifest": bundle.manifest.model_copy(update={
        "coverage_end": datetime(2026, 1, 20, tzinfo=UTC),
    })})
    rows = build_examples(bundle, SCHEDULE)
    current = next(row for row in rows if isinstance(row, WithheldExample)
                   and row.checkpoint_at == datetime(2026, 1, 18, 18, tzinfo=UTC))
    assert current.reason == "incomplete_target_coverage"


def test_unobserved_next_payday_cannot_enter_training_or_scoring():
    bundle = load_history(Path("tests/fixtures/synthetic_history"))
    paydays = tuple(row.model_copy(update={"known_from": date(2026, 2, 1)})
                   if row.payday == date(2026, 1, 24) else row for row in bundle.paydays)
    rows = build_examples(bundle.model_copy(update={"paydays": paydays}), SCHEDULE)
    january = [row for row in rows if (
        row.checkpoint_at if isinstance(row, WithheldExample) else row.features.checkpoint_at
    ).month == 1]
    assert january
    assert all(isinstance(row, WithheldExample) and row.reason == "missing_payday"
               for row in january)


def test_unknown_paydays_after_last_cycle_are_visible_in_coverage():
    rows = build_examples(load_history(Path("tests/fixtures/synthetic_history")), SCHEDULE)
    assert any(isinstance(row, CompletedExample) for row in rows)
    assert any(isinstance(row, WithheldExample) and row.reason == "missing_payday"
               and row.checkpoint_at == datetime(2026, 4, 26, 17, tzinfo=UTC) for row in rows)


def test_render_failure_preserves_both_existing_reports(tmp_path, monkeypatch):
    metadata = {"data_label": "Synthetic demonstration", "input_sha256": "a" * 64}
    write_reports(tmp_path, metadata, ())
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    def fail(*args):
        raise ValueError("render failed")

    monkeypatch.setattr("safe_to_save.workflow.render_markdown", fail)
    with pytest.raises(ValueError, match="render failed"):
        write_reports(tmp_path, metadata, ())
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
