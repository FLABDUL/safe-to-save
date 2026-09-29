"""Compose the offline domain services and deterministic report artefacts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from safe_to_save.balances import CoverageError, reconstruct_balance_minor
from safe_to_save.baselines import (
    BASELINE_VERSION,
    ComparablePositionBaseline,
    ConservativeRulesBaseline,
    TrailingMedianBaseline,
)
from safe_to_save.contracts import HistoryBundle
from safe_to_save.features import (
    FEATURE_VERSION,
    LONDON,
    CompletedExample,
    WithheldExample,
    build_example,
)
from safe_to_save.paycycles import (
    PayCycle,
    ReviewSchedule,
    build_pay_cycles,
    next_known_payday,
    weekly_checkpoints,
)
from safe_to_save.replay import (
    ReplayObservation,
    actual_balance_path,
    replay_one,
    withheld_observation,
)
from safe_to_save.report import render_json, render_markdown
from safe_to_save.repository import HistoryRepository

SYNTHETIC_NOTICE = (
    "> **Synthetic demonstration:** These values come from invented fixtures. They prove\n"
    "> reproducibility and evaluation behaviour, not accuracy on Hakim's finances or\n"
    "> generalisation to Monzo customers.\n\n"
)


def build_examples(
    bundle: HistoryBundle, schedule: ReviewSchedule,
) -> tuple[CompletedExample | WithheldExample, ...]:
    """Retain every in-coverage checkpoint, explicitly withholding unknown windows."""
    if schedule.timezone != "Europe/London":
        raise ValueError("Only Europe/London is supported for payday boundaries")
    manifest = bundle.manifest
    coverage = PayCycle(manifest.coverage_start.astimezone(LONDON).date(),
                        manifest.coverage_end.astimezone(LONDON).date() + timedelta(days=1))
    cycles = build_pay_cycles(bundle.paydays)
    examples = []
    for checkpoint in weekly_checkpoints(coverage, schedule):
        if not manifest.coverage_start <= checkpoint <= manifest.coverage_end:
            continue
        local_date = checkpoint.astimezone(LONDON).date()
        cycle = next((row for row in cycles if row.start <= local_date < row.payday), None)
        known_payday = next_known_payday(local_date, bundle.paydays)
        reason = None
        if cycle is None or known_payday != cycle.payday:
            reason = "missing_payday"
        elif datetime.combine(cycle.payday, time.min, tzinfo=LONDON) > manifest.coverage_end:
            reason = "incomplete_target_coverage"
        if reason:
            examples.append(WithheldExample(manifest.dataset_id, checkpoint, reason))
            continue
        try:
            balance = reconstruct_balance_minor(
                checkpoint, bundle.anchors, bundle.transactions, manifest,
            )
        except CoverageError:
            examples.append(WithheldExample(manifest.dataset_id, checkpoint,
                                            "missing_usable_balance_anchor"))
            continue
        examples.append(build_example(
            manifest.dataset_id, checkpoint, known_payday, balance,
            manifest.coverage_start, bundle.transactions, bundle.commitments,
        ))
    return tuple(examples)


@dataclass(frozen=True)
class WorkflowResult:
    status: str
    output_directory: Path
    checkpoint_count: int


def run_backtest(
    repository: HistoryRepository, dataset_id: str, schedule: ReviewSchedule,
    floor_minor: int, data_label: str, output: Path,
) -> WorkflowResult:
    bundle = repository.load_bundle(dataset_id)
    examples = build_examples(bundle, schedule)
    baselines = (TrailingMedianBaseline(), ComparablePositionBaseline(), ConservativeRulesBaseline())
    history = []
    observations = []
    for example in examples:
        repository.save_example(example)
        for baseline in baselines:
            if isinstance(example, WithheldExample):
                observations.append(withheld_observation(example, baseline.name, example.reason))
                continue
            forecast = baseline.predict(example, history)
            repository.save_forecast(example, forecast)
            path = actual_balance_path(example, bundle.transactions,
                                       bundle.manifest.coverage_start, bundle.manifest.coverage_end)
            observations.append(replay_one(example, forecast, path, floor_minor))
        if isinstance(example, CompletedExample):
            history.append(example)
    runs = tuple(repository.save_backtest(
        dataset_id, baseline.name, floor_minor,
        tuple(row for row in observations if row.baseline_name == baseline.name),
    ) for baseline in baselines)
    metadata = {
        "dataset_id": dataset_id, "data_label": data_label,
        "coverage_start": bundle.manifest.coverage_start.isoformat(),
        "coverage_end": bundle.manifest.coverage_end.isoformat(),
        "review_weekday": schedule.weekday, "review_hour": schedule.local_time.hour,
        "timezone": schedule.timezone, "floor_minor": floor_minor,
        "feature_version": FEATURE_VERSION, "baseline_version": BASELINE_VERSION,
    }
    # Canonical source values and configuration, independent of import/run UUIDs
    # and wall-clock times, pin the report to reproducible inputs.
    source = {"history": bundle.model_dump(mode="json", exclude={"raw_records"}),
              "configuration": metadata}
    metadata["input_sha256"] = hashlib.sha256(json.dumps(
        source, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    directory = write_reports(output, metadata, tuple(observations))
    return WorkflowResult("noop" if all(row.status == "noop" for row in runs) else "completed",
                          directory, len(examples))


def write_reports(
    output: Path, metadata: dict, observations: tuple[ReplayObservation, ...],
) -> Path:
    """Render both files before replacing either, preserving reports for other inputs."""
    json_report = render_json(metadata, observations)
    markdown = render_markdown(metadata, observations)
    if metadata["data_label"] == "Synthetic demonstration":
        markdown = SYNTHETIC_NOTICE + markdown
    existing = output / "baseline-report.json"
    if existing.exists() or (output / "baseline-report.md").exists():
        try:
            previous_hash = json.loads(existing.read_text(encoding="utf-8"))["metadata"][
                "input_sha256"
            ]
        except (OSError, ValueError, KeyError, TypeError):
            previous_hash = None
        if previous_hash != metadata["input_sha256"]:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            output = output.with_name(f"{output.name}-{stamp}")
            output.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name, content in (("baseline-report.json", json_report),
                              ("baseline-report.md", markdown)):
            temporary = output / f"{name}.tmp"
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                created.append(temporary)
                stream.write(content)
        for temporary in created:
            temporary.replace(temporary.with_suffix(""))
    finally:
        for temporary in created:
            temporary.unlink(missing_ok=True)
    return output
