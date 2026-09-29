"""Persist offline history; rejected attempts retain their original evidence."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from safe_to_save.baselines import BASELINE_VERSION, BaselineForecast, UnavailableForecast
from safe_to_save.contracts import (
    BalanceAnchor,
    CanonicalTransaction,
    Commitment,
    HistoryBundle,
    HistoryEvidence,
    HistoryManifest,
    Payday,
    RawHistoryRecord,
)
from safe_to_save.db import connect, isolated_dsn
from safe_to_save.features import FEATURE_VERSION, CompletedExample, FeatureVector, WithheldExample
from safe_to_save.metrics import summarise
from safe_to_save.replay import ReplayObservation
from safe_to_save.report import json_value


@dataclass(frozen=True)
class ImportResult:
    status: str
    canonical_transactions_created: int


@dataclass(frozen=True)
class BacktestResult:
    status: str
    run_id: UUID
    input_sha256: str


def _fingerprint(model: BaseModel) -> str:
    encoded = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class HistoryRepository:
    def __init__(self, dsn: str):
        self.dsn = isolated_dsn(dsn)

    def _insert_evidence(
        self, connection: psycopg.Connection, bundle: HistoryEvidence,
        status: str, error: str | None = None,
    ) -> tuple[UUID, list[tuple[RawHistoryRecord, UUID]]]:
        import_id = uuid4()
        connection.execute(
            """INSERT INTO history_imports
            (import_id, dataset_id, content_sha256, status, records_seen, error_details)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (import_id, bundle.manifest.dataset_id, _fingerprint(bundle), status,
             len(bundle.raw_records), Jsonb({"reason": error}) if error else None),
        )
        records = []
        for raw in bundle.raw_records:
            raw_id = uuid4()
            connection.execute(
                """INSERT INTO raw_history_records
                (raw_record_id, import_id, source_name, source_line, source_transaction_id,
                 source_payload, source_hash) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (raw_id, import_id, raw.source_name, raw.source_line,
                 raw.payload.get("transaction_id")
                 if PurePosixPath(raw.source_name).name == "transactions.jsonl"
                 else None, Jsonb(raw.payload), raw.source_hash),
            )
            records.append((raw, raw_id))
        return import_id, records

    @staticmethod
    def _raw_for(
        records: list[tuple[RawHistoryRecord, UUID]], source: str, model: BaseModel,
    ) -> UUID:
        for raw, raw_id in records:
            if (
                PurePosixPath(raw.source_name).name == source
                and type(model).model_validate(raw.payload) == model
            ):
                return raw_id
        raise ValueError(f"Missing matching raw evidence for {source}")

    def reject_history(self, evidence: HistoryEvidence, reason: str) -> None:
        """Audit a parsed but conflicting import without touching canonical rows."""
        with connect(self.dsn) as connection:
            self._insert_evidence(connection, evidence, "rejected", reason)

    def import_bundle(self, bundle: HistoryBundle) -> ImportResult:
        try:
            with connect(self.dsn) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(55433, 2)")
                import_id, raw_records = self._insert_evidence(connection, bundle, "completed")
                manifest = bundle.manifest
                existing = connection.execute(
                    "SELECT * FROM history_datasets WHERE dataset_id = %s",
                    (manifest.dataset_id,),
                )
                existing.row_factory = dict_row
                row = existing.fetchone()
                if row and HistoryManifest.model_validate(row) != manifest:
                    raise ValueError("canonical dataset conflict")
                created = 0
                if row is None:
                    connection.execute(
                        """INSERT INTO history_datasets
                        (dataset_id, account_id, currency, coverage_start, coverage_end)
                        VALUES (%s, %s, %s, %s, %s)""",
                        (manifest.dataset_id, manifest.account_id, manifest.currency,
                         manifest.coverage_start, manifest.coverage_end),
                    )
                    created += 1
                transaction_count = 0
                for transaction in bundle.transactions:
                    inserted = self._insert_canonical(
                        connection, manifest.dataset_id, "transactions", "transaction_id",
                        transaction, self._raw_for(raw_records, "transactions.jsonl", transaction),
                    )
                    transaction_count += inserted
                    created += inserted
                for rows, table, key, source in (
                    (bundle.anchors, "balance_anchors", "captured_at", "anchors.jsonl"),
                    (bundle.paydays, "paydays", "payday", "paydays.jsonl"),
                    (bundle.commitments, "commitments", "commitment_id", "commitments.jsonl"),
                ):
                    for model in rows:
                        created += self._insert_canonical(
                            connection, manifest.dataset_id, table, key, model,
                            self._raw_for(raw_records, source, model),
                        )
                status = "completed" if created else "noop"
                connection.execute(
                    "UPDATE history_imports SET status = %s WHERE import_id = %s",
                    (status, import_id),
                )
                return ImportResult(status, transaction_count)
        except ValueError as exc:
            # The first transaction has rolled back. A separate committed attempt
            # retains every supplied raw record without changing canonical history.
            with connect(self.dsn) as connection:
                self._insert_evidence(connection, bundle, "rejected", str(exc))
            raise

    @staticmethod
    def _insert_canonical(
        connection: psycopg.Connection, dataset_id: str, table: str,
        key: str, model: BaseModel, raw_id: UUID,
    ) -> int:
        cursor = connection.cursor(row_factory=dict_row)
        existing = cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE dataset_id = %s AND {} = %s").format(
                sql.Identifier(table), sql.Identifier(key),
            ), (dataset_id, getattr(model, key)),
        ).fetchone()
        if existing:
            if type(model).model_validate(existing) != model or (
                table == "transactions" and existing["canonical_fingerprint"] != _fingerprint(model)
            ):
                label = "transaction" if table == "transactions" else table
                raise ValueError(f"canonical {label} conflict")
            return 0
        values = {"dataset_id": dataset_id, "raw_record_id": raw_id, **model.model_dump()}
        if table == "transactions":
            values["canonical_fingerprint"] = _fingerprint(model)
        cursor.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(", ").join(map(sql.Identifier, values)),
                sql.SQL(", ").join(sql.Placeholder() for _ in values),
            ), tuple(values.values()),
        )
        return 1

    def load_bundle(self, dataset_id: str) -> HistoryBundle:
        """Export the complete accepted dataset with unambiguous evidence identities.

        Imports that established canonical history supply the export's evidence.
        With multiple contributing imports, source names are qualified by import
        UUID; source line numbers, payloads and hashes remain unchanged. No-op and
        rejected attempts remain separately available in the audit tables.
        """
        with connect(self.dsn) as connection:
            # Keep evidence and canonical rows at the same committed snapshot.
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor = connection.cursor(row_factory=dict_row)
            manifest = cursor.execute(
                "SELECT * FROM history_datasets WHERE dataset_id = %s", (dataset_id,),
            ).fetchone()
            if manifest is None:
                raise ValueError("Unknown history dataset")
            raw = cursor.execute("""
                SELECT r.import_id, r.source_name, r.source_line, r.source_hash,
                    r.source_payload AS payload
                FROM raw_history_records r JOIN history_imports i USING (import_id)
                WHERE i.dataset_id = %s AND i.status = 'completed'
                ORDER BY i.created_at, i.import_id, r.source_line
            """, (dataset_id,)).fetchall()
            source_order = {
                "transactions.jsonl": 0, "anchors.jsonl": 1,
                "paydays.jsonl": 2, "commitments.jsonl": 3,
            }
            raw.sort(key=lambda row: (
                source_order.get(PurePosixPath(row["source_name"]).name, 4),
            ))
            if len({row["import_id"] for row in raw}) > 1:
                for row in raw:
                    row["source_name"] = f"imports/{row['import_id']}/{row['source_name']}"
            collections = {}
            for field, table, model, order in (
                ("transactions", "transactions", CanonicalTransaction, "occurred_at"),
                ("anchors", "balance_anchors", BalanceAnchor, "captured_at"),
                ("paydays", "paydays", Payday, "payday"),
                ("commitments", "commitments", Commitment, None),
            ):
                ordering = sql.SQL("i.created_at, i.import_id, r.source_line")
                if order:
                    ordering = sql.SQL("c.{}, ").format(sql.Identifier(order)) + ordering
                rows = cursor.execute(
                    sql.SQL("""SELECT c.* FROM {} c
                        JOIN raw_history_records r USING (raw_record_id)
                        JOIN history_imports i USING (import_id)
                        WHERE c.dataset_id = %s ORDER BY {}""").format(
                        sql.Identifier(table), ordering,
                    ), (dataset_id,),
                ).fetchall()
                collections[field] = tuple(model.model_validate(row) for row in rows)
            return HistoryBundle(
                manifest=HistoryManifest.model_validate(manifest),
                raw_records=tuple(RawHistoryRecord.model_validate(row) for row in raw),
                **collections,
            )

    def count_raw_versions(self, transaction_id: str) -> int:
        with connect(self.dsn) as connection:
            return connection.execute(
                """SELECT count(DISTINCT source_hash) FROM raw_history_records
                WHERE source_transaction_id = %s""", (transaction_id,),
            ).fetchone()[0]

    def save_example(
        self, example: CompletedExample | WithheldExample,
        feature_version: str = FEATURE_VERSION,
    ) -> None:
        """Insert an immutable result or verify an identical versioned rerun."""
        payload = None
        target = None
        reason = None
        if isinstance(example, CompletedExample):
            if example.cycle_payday != example.features.payday:
                raise ValueError("cycle payday must match feature payday")
            checkpoint = example.features.checkpoint_at
            # A canonical JSON encoding gives equivalent dataclasses the same
            # bytes. Compare canonical encodings after JSONB round-trip as JSONB
            # does not preserve input whitespace or key order.
            payload = asdict(example.features)
            payload["checkpoint_at"] = checkpoint.isoformat()
            payload["payday"] = example.features.payday.isoformat()
            target = example.target_variable_spend_minor
            status = "complete"
        else:
            checkpoint = example.checkpoint_at
            reason = example.reason
            status = "withheld"
        with connect(self.dsn) as connection:
            connection.execute("""INSERT INTO feature_examples
                (dataset_id, checkpoint_at, feature_version, features,
                 target_variable_spend_minor, status, withheld_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (dataset_id, checkpoint_at, feature_version) DO NOTHING""",
                (example.dataset_id, checkpoint, feature_version,
                 Jsonb(payload, dumps=_canonical_json) if payload is not None else None,
                 target, status, reason),
            )
            row = connection.execute("""SELECT features, target_variable_spend_minor,
                status, withheld_reason FROM feature_examples
                WHERE dataset_id = %s AND checkpoint_at = %s AND feature_version = %s""",
                (example.dataset_id, checkpoint, feature_version),
            ).fetchone()
            if (_canonical_json(row[0]), *row[1:]) != (
                _canonical_json(payload), target, status, reason,
            ):
                raise ValueError("feature example conflict")

    def load_completed_examples(
        self, dataset_id: str, feature_version: str = FEATURE_VERSION,
    ) -> tuple[CompletedExample, ...]:
        with connect(self.dsn) as connection:
            rows = connection.execute("""SELECT features, target_variable_spend_minor
                FROM feature_examples
                WHERE dataset_id = %s AND feature_version = %s AND status = 'complete'
                ORDER BY checkpoint_at""", (dataset_id, feature_version),
            ).fetchall()
        examples = []
        for payload, target in rows:
            payload["checkpoint_at"] = datetime.fromisoformat(payload["checkpoint_at"])
            payload["payday"] = date.fromisoformat(payload["payday"])
            features = FeatureVector(**payload)
            examples.append(CompletedExample(dataset_id, features.payday, features, target))
        return tuple(examples)

    def save_forecast(
        self, example: CompletedExample, forecast: BaselineForecast | UnavailableForecast,
        feature_version: str = FEATURE_VERSION, baseline_version: str = BASELINE_VERSION,
    ) -> None:
        """Keep each versioned forecast immutable; identical reruns are idempotent."""
        if isinstance(forecast, BaselineForecast):
            status, reason = "available", None
            point, upper, sample_size = (
                forecast.point_minor, forecast.upper_minor, forecast.sample_size,
            )
            provenance = {
                "training_paydays": [payday.isoformat() for payday in forecast.training_paydays],
                "training_days_to_payday": list(forecast.training_days_to_payday),
            }
        else:
            status, reason = "withheld", forecast.reason
            point = upper = sample_size = None
            provenance = {}
        key = (example.dataset_id, example.features.checkpoint_at, feature_version,
               forecast.baseline_name, baseline_version)
        values = (point, upper, sample_size, status, reason)
        with connect(self.dsn) as connection:
            connection.execute("""INSERT INTO baseline_predictions
                (dataset_id, checkpoint_at, feature_version, baseline_name, baseline_version,
                 point_minor, upper_minor, sample_size, status, withheld_reason, provenance)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (dataset_id, checkpoint_at, feature_version,
                             baseline_name, baseline_version) DO NOTHING""",
                (*key, *values, Jsonb(provenance)),
            )
            row = connection.execute("""SELECT point_minor, upper_minor, sample_size,
                status, withheld_reason, provenance FROM baseline_predictions
                WHERE dataset_id = %s AND checkpoint_at = %s AND feature_version = %s
                    AND baseline_name = %s AND baseline_version = %s""", key).fetchone()
            if row != (*values, provenance):
                raise ValueError("baseline forecast conflict")

    def save_backtest(
        self, dataset_id: str, baseline_name: str, floor_minor: int,
        observations: tuple[ReplayObservation, ...],
        feature_version: str = FEATURE_VERSION, baseline_version: str = BASELINE_VERSION,
    ) -> BacktestResult:
        """Atomically record a completed run or an identical, immutable no-op.

        Identity pins versions, policy and checkpoint set. Changed content under
        that identity is a conflict, requiring a new feature/baseline version.
        No-op is returned with the existing run ID; no duplicate run is inserted.
        """
        if type(floor_minor) is not int or floor_minor < 0:
            raise ValueError("floor_minor must be a non-negative integer")
        ordered = tuple(sorted(observations, key=lambda row: row.checkpoint_at))
        if any(row.baseline_name != baseline_name for row in ordered):
            raise ValueError("backtest accepts one baseline only")
        if len({row.checkpoint_at for row in ordered}) != len(ordered):
            raise ValueError("duplicate backtest checkpoint")
        identity = json_value({
            "dataset_id": dataset_id, "feature_version": feature_version,
            "baseline_name": baseline_name, "baseline_version": baseline_version,
            "floor_minor": floor_minor, "checkpoints": [row.checkpoint_at for row in ordered],
        })
        digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
        summary = json_value(asdict(summarise(ordered)))
        with connect(self.dsn) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(55433, 7)")
            existing = connection.execute(
                "SELECT run_id, summary, status FROM backtest_runs WHERE input_sha256 = %s",
                (digest,),
            ).fetchone()
            if existing:
                saved = self._load_backtest_observations(connection, existing[0])
                if saved != ordered or existing[1] != summary or existing[2] != "completed":
                    raise ValueError("backtest conflict")
                return BacktestResult("noop", existing[0], digest)
            run_id = uuid4()
            connection.execute("""INSERT INTO backtest_runs
                (run_id, dataset_id, feature_version, baseline_name, baseline_version,
                 floor_minor, input_sha256, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'running')""",
                (run_id, dataset_id, feature_version, baseline_name, baseline_version,
                 floor_minor, digest),
            )
            for row in ordered:
                values = asdict(row)
                del values["baseline_name"]
                values = {"run_id": run_id, **values}
                connection.execute(sql.SQL("INSERT INTO backtest_observations ({}) VALUES ({})")
                                   .format(sql.SQL(", ").join(map(sql.Identifier, values)),
                                           sql.SQL(", ").join(sql.Placeholder() for _ in values)),
                                   tuple(values.values()))
            connection.execute("""UPDATE backtest_runs
                SET status = 'completed', completed_at = clock_timestamp(), summary = %s
                WHERE run_id = %s""", (Jsonb(summary), run_id))
        return BacktestResult("completed", run_id, digest)

    @staticmethod
    def _load_backtest_observations(connection, run_id: UUID) -> tuple[ReplayObservation, ...]:
        rows = connection.cursor(row_factory=dict_row).execute("""
            SELECT o.*, r.baseline_name FROM backtest_observations o
            JOIN backtest_runs r USING (run_id) WHERE run_id = %s ORDER BY checkpoint_at
        """, (run_id,)).fetchall()
        for row in rows:
            del row["run_id"]
        return tuple(ReplayObservation(**row) for row in rows)

    def load_backtest_observations(self, run_id: UUID) -> tuple[ReplayObservation, ...]:
        with connect(self.dsn) as connection:
            return self._load_backtest_observations(connection, run_id)

    def load_forecasts(
        self, dataset_id: str, feature_version: str = FEATURE_VERSION,
        baseline_version: str = BASELINE_VERSION,
    ) -> tuple[tuple[datetime, BaselineForecast | UnavailableForecast], ...]:
        """Load one dataset/version in stable checkpoint and strategy order."""
        with connect(self.dsn) as connection:
            cursor = connection.cursor(row_factory=dict_row)
            rows = cursor.execute("""SELECT * FROM baseline_predictions
                WHERE dataset_id = %s AND feature_version = %s AND baseline_version = %s
                ORDER BY checkpoint_at, baseline_name""",
                (dataset_id, feature_version, baseline_version),
            ).fetchall()
        results = []
        for row in rows:
            if row["status"] == "withheld":
                forecast = UnavailableForecast(row["baseline_name"], row["withheld_reason"])
            else:
                provenance = row["provenance"]
                forecast = BaselineForecast(
                    row["baseline_name"], row["point_minor"], row["upper_minor"], row["sample_size"],
                    tuple(date.fromisoformat(value) for value in provenance["training_paydays"]),
                    tuple(provenance["training_days_to_payday"]),
                )
            results.append((row["checkpoint_at"], forecast))
        return tuple(results)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
