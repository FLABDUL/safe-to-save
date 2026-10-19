from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from safe_to_save.db import apply_migrations
from safe_to_save.gate_assess import GateDecision, assess_gate, load_gate_evidence
from safe_to_save.gate_config import load_gate_config
from safe_to_save.gate_evaluate import evaluate_gate
from safe_to_save.gate_prepare import prepare_private_history
from safe_to_save.gate_report import write_gate_report
from safe_to_save.history import HistoryConflictError, load_history
from safe_to_save.paycycles import ReviewSchedule
from safe_to_save.repository import HistoryRepository
from safe_to_save.settings import Settings
from safe_to_save.workflow import run_backtest


def _non_negative(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("floor must be a non-negative integer") from None
    if number < 0:
        raise argparse.ArgumentTypeError("floor must be a non-negative integer")
    return number


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise argparse.ArgumentTypeError("unknown timezone") from None
    if value != "Europe/London":
        raise argparse.ArgumentTypeError("timezone must be Europe/London for this milestone")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safe-to-save",
        description="Offline Safe-to-Save baseline and historical replay",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    db = commands.add_parser("db").add_subparsers(dest="action", required=True)
    db.add_parser("migrate")
    history = commands.add_parser("history").add_subparsers(dest="action", required=True)
    history.add_parser("import").add_argument("--directory", type=Path, required=True)
    backtest = commands.add_parser("backtest").add_subparsers(dest="action", required=True)
    run = backtest.add_parser("run")
    run.add_argument("--dataset-id", required=True)
    run.add_argument("--review-weekday", type=int, choices=range(7), required=True)
    run.add_argument("--review-hour", type=int, choices=range(24), required=True)
    run.add_argument("--timezone", type=_timezone, required=True)
    run.add_argument("--floor-minor", type=_non_negative, required=True)
    run.add_argument("--data-label", choices=("Synthetic demonstration", "Private evaluation"),
                     required=True)
    run.add_argument("--output", type=Path, required=True)
    gate = commands.add_parser("gate-a").add_subparsers(dest="action", required=True)
    for action in ("prepare", "evaluate", "assess"):
        gate.add_parser(action).add_argument("--config", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    stage = f"gate-a {args.action}" if args.command == "gate-a" else None
    try:
        if args.command == "gate-a":
            config = load_gate_config(args.config, Path.cwd())
            if args.action == "prepare":
                result = prepare_private_history(config)
                if result.status == "fix_data_or_policy":
                    print(
                        f"{stage}: status=fix_data_or_policy; error=PreparationRejected; "
                        "no report was published.",
                        file=sys.stderr,
                    )
                    return 1
                print(f"{stage}: status={result.status}.")
                return 0
            if args.action == "evaluate":
                settings = Settings.from_environment()
                repository = HistoryRepository(settings.database_url)
                evaluation_config = config.model_copy(update={
                    "output_directory": config.output_directory / "evaluation",
                })
                result = evaluate_gate(evaluation_config, repository)
                if result.status == "fix_data_or_policy":
                    print(
                        f"{stage}: status=fix_data_or_policy; error=EvaluationRejected; "
                        "no report was published.",
                        file=sys.stderr,
                    )
                    return 1
                print(f"{stage}: status={result.status}; scenarios={len(result.scenarios)}.")
                return 0
            bundle = load_history(config.canonical_directory)
            evidence = load_gate_evidence(config.output_directory / "evaluation")
            assessment = assess_gate(config, bundle, evidence)
            write_gate_report(config.output_directory / "assessment", assessment)
            if assessment.decision is GateDecision.FIX_DATA_OR_POLICY:
                print(
                    f"{stage}: status=fix_data_or_policy; error=AssessmentRejected; "
                    "aggregate report was published.",
                    file=sys.stderr,
                )
                return 1
            print(
                f"{stage}: status={assessment.decision.value}; "
                "files=gate-a-report.json,gate-a-report.md."
            )
            return 0

        settings = Settings.from_environment()
        repository = HistoryRepository(settings.database_url)
        if args.command == "db":
            migrations = apply_migrations(settings.database_url,
                                          Path(__file__).resolve().parents[2] / "sql")
            print(f"Validation passed: {len(migrations)} migrations applied.")
        elif args.command == "history":
            try:
                bundle = load_history(args.directory)
            except HistoryConflictError as conflict:
                repository.reject_history(conflict.evidence, "intra_import_transaction_conflict")
                raise
            result = repository.import_bundle(bundle)
            print(f"Validation passed: {args.directory.name}; status={result.status}; "
                  f"transactions={len(bundle.transactions)}; "
                  f"created={result.canonical_transactions_created}; "
                  f"coverage={bundle.manifest.coverage_start.date()} "
                  f"to {bundle.manifest.coverage_end.date()}.")
        else:
            schedule = ReviewSchedule(args.review_weekday, time(args.review_hour), args.timezone)
            result = run_backtest(repository, args.dataset_id, schedule, args.floor_minor,
                                  args.data_label, args.output)
            print(f"Validation passed: status={result.status}; checkpoints={result.checkpoint_count}; "
                  f"files={result.output_directory / 'baseline-report.json'}, "
                  f"{result.output_directory / 'baseline-report.md'}.")
    except (ValueError, RuntimeError, OSError, psycopg.Error) as exc:
        # Validation/database exceptions can contain raw payloads, personal values
        # or connection details. Keep terminal output to a safe validation status.
        if stage is not None:
            publication = (
                "report publication was not confirmed."
                if stage == "gate-a assess" else "no report was published."
            )
            print(
                f"{stage}: status=failed; error={type(exc).__name__}; {publication}",
                file=sys.stderr,
            )
        else:
            print(
                f"Validation failed ({type(exc).__name__}); no report was published.",
                file=sys.stderr,
            )
        return 1
    return 0
