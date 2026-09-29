# Offline Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Milestone 1 of Safe to Save: a PostgreSQL-backed, point-in-time-correct offline pipeline that imports canonical Monzo history, creates weekly payday-aware examples, evaluates three deterministic spending baselines, and emits a reproducible baseline report.

**Architecture:** A Python package imports an explicit offline history contract into an isolated PostgreSQL database while retaining immutable source evidence. Pure domain functions reconstruct balances, create leakage-safe feature rows and run rolling historical replay; a thin repository persists inputs and results, and an `argparse` CLI composes the workflow. Milestone 1 does not call the Monzo API, train an ML model, serve a dashboard or move money.

**Tech Stack:** Python 3.11+, PostgreSQL 14+, Pydantic 2, psycopg 3, pytest 8, Hypothesis 6, PowerShell, standard-library `argparse`, `zoneinfo`, `statistics`, `decimal` and `hashlib`.

**Spec:** `docs/superpowers/specs/2026-09-21-safe-to-save-design.md`

## Global Constraints

- Store money as signed integer minor units: inflows positive and outflows negative; never use binary floating point for money.
- Store timestamps as timezone-aware UTC; derive the weekly schedule in `Europe/London` so daylight-saving transitions are correct.
- Preserve every imported source line as immutable evidence; canonical rows may point to a newer evidence version but raw rows are never updated or deleted.
- Keep personal inputs, database files, credentials, generated reports from real data and fitted personal artefacts outside Git.
- Public fixtures must be invented and labelled synthetic; never copy real merchant names, balances, amounts or aggregates.
- A missing balance anchor, coverage gap, missing payday, invalid risk floor or unavailable baseline produces an explicit withheld result, never a fabricated zero forecast.
- Milestone 1 is offline only: no OAuth, webhooks, HTTP service, dashboard, probabilistic model, LLM or Pot transfer.
- Use a separate PostgreSQL cluster on `127.0.0.1:55433`, database `safe_to_save`; never share Budget V2's cluster or tables.
- Commit commands below are prepared for an authorised Git workflow but must not be run unless Hakim explicitly authorises commits.

## Review Focus

- Conflicting records with the same provider transaction ID: retain both raw lines, reject the canonical import and report the conflict instead of silently choosing one. Task 2 pins this behaviour.
- Review checkpoints around the UK daylight-saving transition: generate the configured local time once, convert it to UTC and never duplicate or skip a valid weekly checkpoint. Task 4 pins this behaviour.
- A checkpoint outside complete transaction coverage or without a later balance anchor: return a withheld example with a precise reason. Tasks 4 and 5 pin this behaviour.
- Refunds, transfers and committed expenses: refunds reduce variable spend, while internal/savings transfers and committed expenses do not enter the variable-spend target. Task 5 pins this behaviour.
- Too little comparable history: mark each unavailable baseline explicitly and exclude it from aggregate metrics; do not interpret missing history as zero spend. Tasks 6 and 7 pin this behaviour.

---

## File Structure

The milestone creates these focused units:

```text
safe-to-save/
├── AGENTS.md                         project invariants and verification commands
├── README.md                         milestone purpose and local operating guide
├── pyproject.toml                    package, runtime and test dependencies
├── .gitignore                        secrets, private data, database and generated output
├── config/example-history.json       documented non-secret import configuration
├── scripts/
│   ├── init-local-db.ps1             isolated PostgreSQL creation and startup
│   ├── apply-migrations.ps1          ordered SQL migration runner
│   ├── stop-local-db.ps1             clean local database shutdown
│   └── test.ps1                      unit and integration verification entrypoint
├── sql/
│   ├── 001_history.sql               immutable evidence and canonical history tables
│   ├── 002_examples.sql              payday, commitment and feature-example tables
│   ├── 003_baselines.sql             baseline prediction table
│   └── 004_backtests.sql             replay run and observation tables
├── src/safe_to_save/
│   ├── __init__.py                   package version
│   ├── cli.py                        command composition only
│   ├── settings.py                   explicit environment/config parsing
│   ├── contracts.py                  validated domain and import models
│   ├── history.py                    file parsing, hashing and conflict checks
│   ├── db.py                         connection and migration primitives
│   ├── repository.py                 PostgreSQL persistence interfaces
│   ├── paycycles.py                  payday periods and local weekly checkpoints
│   ├── balances.py                   point-in-time balance reconstruction
│   ├── features.py                   leakage-safe features and realised labels
│   ├── baselines.py                  three deterministic forecast strategies
│   ├── replay.py                     rolling replay and policy arithmetic
│   ├── metrics.py                    aggregate product/model metrics
│   └── report.py                     stable JSON and Markdown rendering
└── tests/
    ├── conftest.py                    isolated PostgreSQL fixtures
    ├── factories.py                   synthetic domain-object builders
    ├── fixtures/synthetic_history/   invented complete offline dataset
    ├── unit/                         pure contract, feature, baseline and metric tests
    └── integration/                  PostgreSQL and end-to-end CLI tests
```

## Task 1: Establish the package and operating contract

**Files:**
- Create: `AGENTS.md`
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/safe_to_save/__init__.py`
- Create: `src/safe_to_save/cli.py`
- Create: `src/safe_to_save/settings.py`
- Create: `scripts/test.ps1`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: none.
- Produces: `safe_to_save.cli.main(argv: Sequence[str] | None = None) -> int`; `safe_to_save.settings.Settings`; the `safe-to-save` console command used by every later task.

- [ ] **Step 1: Write the failing CLI smoke test**

```python
# tests/unit/test_cli.py
import pytest

from safe_to_save.cli import main


def test_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "offline safe-to-save baseline" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run the test and confirm the package does not exist**

Run:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest tests/unit/test_cli.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'safe_to_save'` before the package files are added.

- [ ] **Step 3: Create the package metadata and minimal CLI**

Use this dependency boundary in `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "safe-to-save"
version = "0.1.0"
description = "Payday-aware offline safe-to-save evaluation"
requires-python = ">=3.11"
dependencies = [
  "psycopg[binary]>=3.2",
  "pydantic>=2.9",
]

[project.optional-dependencies]
dev = [
  "hypothesis>=6.112",
  "pytest>=8.3",
  "pytest-cov>=5.0",
  "ruff>=0.6",
]

[project.scripts]
safe-to-save = "safe_to_save.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["integration: requires the isolated PostgreSQL database"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

Create the entrypoint:

```python
# src/safe_to_save/cli.py
from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="safe-to-save",
        description="Offline Safe-to-Save baseline and historical replay",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0
```

`settings.py` must expose an immutable `Settings(database_url: str)` model and a `from_environment()` constructor that raises `RuntimeError("DATABASE_URL is required")` when absent. Do not provide an embedded credential or fallback database.

Create `scripts/test.ps1` to resolve the project root, require `.venv\Scripts\python.exe`, load `DATABASE_URL` from ignored `.env.local` when present, then run:

```powershell
& $python -m ruff check src tests
& $python -m pytest -m "not integration" -v
if ($env:DATABASE_URL) { & $python -m pytest -m integration -v }
```

Each command must stop the script on a non-zero exit code.

- [ ] **Step 4: Add repository protections and operating instructions**

`.gitignore` must include:

```gitignore
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.coverage
.env.local
.postgres-data/
.postgres-log/
data/private/
outputs/private/
*.joblib
*.pkl
```

`AGENTS.md` must state the money, UTC, immutable-evidence, no-private-fixtures, fail-closed and no-cross-database rules from Global Constraints. `README.md` must label the project as unaffiliated with Monzo and Milestone 1 as offline evaluation only.

- [ ] **Step 5: Run the focused and static checks**

Run:

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m ruff check src tests
.\.venv\Scripts\python -m pytest tests/unit/test_cli.py -v
```

Expected: both commands pass.

- [ ] **Step 6: Prepare the task commit, but execute it only with explicit authorisation**

```powershell
git add AGENTS.md .gitignore README.md pyproject.toml scripts/test.ps1 src/safe_to_save tests/unit/test_cli.py
git commit -m "chore: establish safe-to-save project contract"
```

## Task 2: Define and validate the offline history contract

**Files:**
- Create: `config/example-history.json`
- Create: `src/safe_to_save/contracts.py`
- Create: `src/safe_to_save/history.py`
- Test: `tests/unit/test_history.py`
- Create: `tests/factories.py`
- Test fixture: `tests/fixtures/synthetic_history/manifest.json`
- Test fixture: `tests/fixtures/synthetic_history/transactions.jsonl`
- Test fixture: `tests/fixtures/synthetic_history/anchors.jsonl`
- Test fixture: `tests/fixtures/synthetic_history/paydays.jsonl`
- Test fixture: `tests/fixtures/synthetic_history/commitments.jsonl`

**Interfaces:**
- Consumes: standard library paths and JSON text.
- Produces: `EconomicType`; `CanonicalTransaction`; `BalanceAnchor`; `Payday`; `Commitment`; `HistoryManifest`; `HistoryBundle`; `load_history(directory: Path) -> HistoryBundle`; `HistoryConflictError`.

- [ ] **Step 1: Write failing contract tests for valid history and conflicting provider IDs**

```python
# tests/unit/test_history.py
from pathlib import Path

import pytest

from safe_to_save.history import HistoryConflictError, load_history


FIXTURE = Path("tests/fixtures/synthetic_history")


def test_load_history_preserves_signed_minor_units_and_aware_times() -> None:
    history = load_history(FIXTURE)
    purchase = next(row for row in history.transactions if row.transaction_id == "synthetic-tx-001")
    assert purchase.amount_minor == -4200
    assert purchase.occurred_at.utcoffset() is not None
    assert history.manifest.currency == "GBP"


def test_conflicting_provider_id_is_rejected_but_both_raw_lines_are_returned(tmp_path: Path) -> None:
    source = FIXTURE / "transactions.jsonl"
    conflicting = tmp_path / "transactions.jsonl"
    conflicting.write_text(source.read_text(encoding="utf-8") +
        '{"transaction_id":"synthetic-tx-001","occurred_at":"2026-01-03T12:00:00Z",'
        '"amount_minor":-9999,"currency":"GBP","status":"settled",'
        '"economic_type":"expense","committed":false}\n', encoding="utf-8")
    for name in ("manifest.json", "anchors.jsonl", "paydays.jsonl", "commitments.jsonl"):
        (tmp_path / name).write_text((FIXTURE / name).read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(HistoryConflictError) as exc:
        load_history(tmp_path)
    assert exc.value.transaction_id == "synthetic-tx-001"
    assert len(exc.value.raw_records) == 2
```

- [ ] **Step 2: Run the tests and verify missing contract types**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/unit/test_history.py -v
```

Expected: FAIL because `safe_to_save.history` and its types do not exist.

- [ ] **Step 3: Implement immutable Pydantic contracts**

Define these exact public shapes in `contracts.py`:

```python
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class EconomicType(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    INTERNAL_TRANSFER = "internal_transfer"
    SAVINGS_TRANSFER = "savings_transfer"
    ADJUSTMENT = "adjustment"


class CanonicalTransaction(BaseModel):
    model_config = ConfigDict(frozen=True)
    transaction_id: str = Field(min_length=1)
    occurred_at: AwareDatetime
    amount_minor: int
    currency: Literal["GBP"]
    status: Literal["settled"]
    economic_type: EconomicType
    committed: bool = False
    category: str | None = None


class BalanceAnchor(BaseModel):
    model_config = ConfigDict(frozen=True)
    captured_at: AwareDatetime
    balance_minor: int
    currency: Literal["GBP"]


class Payday(BaseModel):
    model_config = ConfigDict(frozen=True)
    payday: date
    known_from: date


class Commitment(BaseModel):
    model_config = ConfigDict(frozen=True)
    commitment_id: str = Field(min_length=1)
    known_from: AwareDatetime
    due_at: AwareDatetime
    amount_minor: int = Field(le=0)
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def due_after_known(self) -> "Commitment":
        if self.due_at <= self.known_from:
            raise ValueError("due_at must be after known_from")
        return self


class HistoryManifest(BaseModel):
    model_config = ConfigDict(frozen=True)
    dataset_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    currency: Literal["GBP"]
    coverage_start: AwareDatetime
    coverage_end: AwareDatetime


class RawHistoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_name: str
    source_line: int
    payload: dict[str, object]
    source_hash: str


class HistoryBundle(BaseModel):
    model_config = ConfigDict(frozen=True)
    manifest: HistoryManifest
    raw_records: tuple[RawHistoryRecord, ...]
    transactions: tuple[CanonicalTransaction, ...]
    anchors: tuple[BalanceAnchor, ...]
    paydays: tuple[Payday, ...]
    commitments: tuple[Commitment, ...]
```

Add manifest validation that `coverage_end > coverage_start`, and reject a transaction or anchor outside the manifest's account currency.

- [ ] **Step 4: Implement deterministic JSON/JSONL parsing and conflict detection**

`history.py` must:

1. read the five named files only;
2. hash the exact UTF-8 bytes of every JSONL line with SHA-256;
3. retain each line as a `RawHistoryRecord`;
4. accept byte-identical duplicate transaction lines once canonically while retaining both raw rows;
5. raise `HistoryConflictError(transaction_id, raw_records)` when the same ID has different canonical content;
6. sort transactions and anchors by timestamp, and paydays by date;
7. reject missing files, blank lines and malformed JSON with filename and line number in the exception.

The conflict key is the full canonical JSON representation, not only amount and time:

```python
fingerprint = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
```

- [ ] **Step 5: Add shared domain factories and entirely invented fixtures**

Create `tests/factories.py` with dependency-safe builders for Task 2's public contracts:

```python
from datetime import datetime, timezone

from safe_to_save.contracts import (
    BalanceAnchor, CanonicalTransaction, EconomicType, HistoryManifest,
)


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
        dataset_id="synthetic-v1",
        account_id="synthetic-current",
        currency="GBP",
        coverage_start="2025-09-01T00:00:00+00:00",
        coverage_end="2026-05-01T00:00:00+00:00",
    )
```

Extend this file in later tasks only after the corresponding production types exist, so early test collection never imports a not-yet-created module.

Create at least four synthetic payday periods, 20 transactions, two balance anchors, one refund, one internal transfer, one savings transfer and three commitments. Use names such as `Synthetic Grocer` and amounts not copied from personal data. `config/example-history.json` documents the five filenames, `Europe/London`, review weekday `6` (Sunday), review hour `18`, and an illustrative floor of `25000` minor units; label every value illustrative.

- [ ] **Step 6: Add validation tests and run the full unit file**

Add parametrised tests that reject a naive timestamp, non-GBP currency, positive commitment amount, coverage end before start and malformed JSONL.

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/unit/test_history.py -v
```

Expected: PASS.

- [ ] **Step 7: Prepare the task commit, subject to explicit authorisation**

```powershell
git add config src/safe_to_save/contracts.py src/safe_to_save/history.py tests/unit/test_history.py tests/fixtures/synthetic_history
git commit -m "feat: define offline history contract"
```

## Task 3: Persist immutable history in isolated PostgreSQL

**Files:**
- Create: `sql/001_history.sql`
- Create: `src/safe_to_save/db.py`
- Create: `src/safe_to_save/repository.py`
- Create: `scripts/init-local-db.ps1`
- Create: `scripts/apply-migrations.ps1`
- Create: `scripts/stop-local-db.ps1`
- Create: `tests/conftest.py`
- Test: `tests/integration/test_history_repository.py`
- Modify: `scripts/test.ps1`

**Interfaces:**
- Consumes: `HistoryBundle` from Task 2 and `Settings.database_url` from Task 1.
- Produces: `apply_migrations(dsn: str, sql_dir: Path) -> tuple[str, ...]`; `ImportResult(status: str, canonical_transactions_created: int)`; `HistoryRepository.import_bundle(bundle: HistoryBundle) -> ImportResult`; `HistoryRepository.load_bundle(dataset_id: str) -> HistoryBundle`; `HistoryRepository.count_raw_versions(transaction_id: str) -> int`.

- [ ] **Step 1: Write failing integration tests for idempotency, raw retention and conflict rollback**

```python
# tests/integration/test_history_repository.py
import hashlib
import json
from pathlib import Path

import pytest

from safe_to_save.contracts import RawHistoryRecord
from safe_to_save.history import load_history
from safe_to_save.repository import HistoryRepository

pytestmark = pytest.mark.integration


def test_identical_import_is_a_recorded_noop(repository: HistoryRepository) -> None:
    bundle = load_history(Path("tests/fixtures/synthetic_history"))
    first = repository.import_bundle(bundle)
    second = repository.import_bundle(bundle)
    assert first.canonical_transactions_created == len(bundle.transactions)
    assert second.canonical_transactions_created == 0
    assert second.status == "noop"


def test_conflicting_canonical_transaction_rolls_back_without_losing_raw_evidence(
    repository: HistoryRepository,
) -> None:
    original = load_history(Path("tests/fixtures/synthetic_history"))
    repository.import_bundle(original)
    changed = original.transactions[0].model_copy(update={"amount_minor": -9999})
    payload = changed.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    conflict_raw = RawHistoryRecord(
        source_name="transactions.jsonl",
        source_line=999,
        payload=payload,
        source_hash=hashlib.sha256(encoded).hexdigest(),
    )
    conflicting = original.model_copy(update={
        "raw_records": (*original.raw_records, conflict_raw),
        "transactions": (
            changed,
            *original.transactions[1:],
        )
    })
    with pytest.raises(ValueError, match="canonical transaction conflict"):
        repository.import_bundle(conflicting)
    assert repository.count_raw_versions(original.transactions[0].transaction_id) == 2
    assert repository.load_bundle(original.manifest.dataset_id).transactions[0] == original.transactions[0]
```

- [ ] **Step 2: Run the integration test and confirm the database layer is absent**

Run:

```powershell
.\scripts\init-local-db.ps1
.\scripts\apply-migrations.ps1
.\.venv\Scripts\python -m pytest tests/integration/test_history_repository.py -v
```

Expected: FAIL because the scripts, migration and repository do not exist.

- [ ] **Step 3: Create the isolated database scripts**

Adapt the already verified Budget V2 local-cluster pattern, but use:

```powershell
param([int]$Port = 55433)
$pgBin = 'C:\Program Files\PostgreSQL\14\bin'
$data = Join-Path $project '.postgres-data'
$database = 'safe_to_save'
$admin = 'sts_admin'
```

Generate a random password, store only `DATABASE_URL=postgresql://...@127.0.0.1:55433/safe_to_save` in ignored `.env.local`, require SCRAM for TCP connections, and verify every resolved data/log path remains under the Safe to Save project root before creating directories. `stop-local-db.ps1` must resolve the same explicit `.postgres-data` path and call `pg_ctl stop --wait` only after verifying `PG_VERSION` exists there.

Create `tests/conftest.py` with an integration-only repository fixture. It reads `DATABASE_URL`, applies migrations, discovers every current public table except `schema_migrations`, truncates those tables with `psycopg.sql.Identifier` and `CASCADE`, then returns `HistoryRepository(dsn)`. Never assemble table names through string interpolation.

```python
@pytest.fixture
def repository() -> HistoryRepository:
    dsn = os.environ["DATABASE_URL"]
    apply_migrations(dsn, Path("sql"))
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public' AND tablename <> 'schema_migrations'
            ORDER BY tablename
        """)
        for (table_name,) in cursor.fetchall():
            cursor.execute(
                sql.SQL("TRUNCATE TABLE {} CASCADE").format(sql.Identifier(table_name))
            )
    return HistoryRepository(dsn)
```

- [ ] **Step 4: Create the immutable history migration**

`sql/001_history.sql` must create:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  sha256 text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS history_imports (
  import_id uuid PRIMARY KEY,
  dataset_id text NOT NULL,
  content_sha256 text NOT NULL,
  status text NOT NULL CHECK (status IN ('completed','noop','rejected')),
  records_seen integer NOT NULL CHECK (records_seen >= 0),
  error_details jsonb,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS ix_history_imports_content
  ON history_imports(content_sha256, status);

CREATE TABLE IF NOT EXISTS raw_history_records (
  raw_record_id uuid PRIMARY KEY,
  import_id uuid NOT NULL REFERENCES history_imports(import_id),
  source_name text NOT NULL,
  source_line integer NOT NULL CHECK (source_line > 0),
  source_transaction_id text,
  source_payload jsonb NOT NULL,
  source_hash text NOT NULL,
  UNIQUE(import_id, source_name, source_line)
);

CREATE TABLE IF NOT EXISTS history_datasets (
  dataset_id text PRIMARY KEY,
  account_id text NOT NULL,
  currency text NOT NULL CHECK (currency = 'GBP'),
  coverage_start timestamptz NOT NULL,
  coverage_end timestamptz NOT NULL,
  CHECK (coverage_end > coverage_start)
);

CREATE TABLE IF NOT EXISTS transactions (
  dataset_id text NOT NULL REFERENCES history_datasets(dataset_id),
  transaction_id text NOT NULL,
  raw_record_id uuid NOT NULL REFERENCES raw_history_records(raw_record_id),
  occurred_at timestamptz NOT NULL,
  amount_minor bigint NOT NULL,
  currency text NOT NULL CHECK (currency = 'GBP'),
  status text NOT NULL CHECK (status = 'settled'),
  economic_type text NOT NULL CHECK (economic_type IN
    ('expense','income','internal_transfer','savings_transfer','adjustment')),
  committed boolean NOT NULL DEFAULT false,
  category text,
  canonical_fingerprint text NOT NULL,
  PRIMARY KEY(dataset_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS balance_anchors (
  dataset_id text NOT NULL REFERENCES history_datasets(dataset_id),
  captured_at timestamptz NOT NULL,
  balance_minor bigint NOT NULL,
  currency text NOT NULL CHECK (currency = 'GBP'),
  raw_record_id uuid NOT NULL REFERENCES raw_history_records(raw_record_id),
  PRIMARY KEY(dataset_id, captured_at)
);

CREATE TABLE IF NOT EXISTS paydays (
  dataset_id text NOT NULL REFERENCES history_datasets(dataset_id),
  payday date NOT NULL,
  known_from date NOT NULL,
  raw_record_id uuid NOT NULL REFERENCES raw_history_records(raw_record_id),
  PRIMARY KEY(dataset_id, payday)
);

CREATE TABLE IF NOT EXISTS commitments (
  dataset_id text NOT NULL REFERENCES history_datasets(dataset_id),
  commitment_id text NOT NULL,
  known_from timestamptz NOT NULL,
  due_at timestamptz NOT NULL,
  amount_minor bigint NOT NULL CHECK (amount_minor <= 0),
  description text NOT NULL,
  raw_record_id uuid NOT NULL REFERENCES raw_history_records(raw_record_id),
  PRIMARY KEY(dataset_id, commitment_id),
  CHECK (due_at > known_from)
);
```

The repository inserts attempted raw records first in a transaction, checks the existing canonical fingerprint, and on conflict records a separate rejected import plus its raw lines before raising. Use two database transactions so conflict evidence survives while canonical changes roll back.

- [ ] **Step 5: Implement ordered, checksum-protected migration application**

`apply_migrations()` reads `sql/[0-9][0-9][0-9]_*.sql` in lexical order. Store both version and SHA-256 in `schema_migrations`. Refuse to continue when an applied filename has a different current checksum.

- [ ] **Step 6: Run integration and full tests**

Run:

```powershell
.\scripts\init-local-db.ps1
.\scripts\apply-migrations.ps1
.\scripts\test.ps1
```

Expected: unit tests pass; integration tests pass against port 55433; the second import reports `noop`; the conflicting attempt leaves two raw versions and the original canonical row.

- [ ] **Step 7: Prepare the task commit, subject to explicit authorisation**

```powershell
git add sql/001_history.sql scripts src/safe_to_save/db.py src/safe_to_save/repository.py tests/integration/test_history_repository.py
git commit -m "feat: persist immutable offline history"
```

## Task 4: Build payday periods, weekly checkpoints and balances

**Files:**
- Create: `sql/002_examples.sql`
- Create: `src/safe_to_save/paycycles.py`
- Create: `src/safe_to_save/balances.py`
- Modify: `src/safe_to_save/repository.py`
- Modify: `tests/factories.py`
- Test: `tests/unit/test_paycycles.py`
- Test: `tests/unit/test_balances.py`

**Interfaces:**
- Consumes: sorted `Payday`, `CanonicalTransaction`, `BalanceAnchor` and manifest coverage from Tasks 2–3.
- Produces: `PayCycle`; `ReviewSchedule`; `build_pay_cycles(paydays: Sequence[Payday]) -> tuple[PayCycle, ...]`; `next_known_payday(checkpoint: date, paydays: Sequence[Payday]) -> date | None`; `weekly_checkpoints(cycle: PayCycle, schedule: ReviewSchedule) -> tuple[datetime, ...]`; `reconstruct_balance_minor(...) -> int`; `CoverageError`.

- [ ] **Step 1: Write failing payday and daylight-saving tests**

```python
# tests/unit/test_paycycles.py
from datetime import date, time
from zoneinfo import ZoneInfo

from safe_to_save.contracts import Payday
from safe_to_save.paycycles import (
    PayCycle, ReviewSchedule, next_known_payday, weekly_checkpoints,
)


def test_sunday_review_stays_at_1800_london_across_dst() -> None:
    cycle = PayCycle(start=date(2026, 3, 20), payday=date(2026, 4, 20))
    schedule = ReviewSchedule(weekday=6, local_time=time(18, 0), timezone="Europe/London")
    checkpoints = weekly_checkpoints(cycle, schedule)
    london = ZoneInfo("Europe/London")
    assert all(value.astimezone(london).hour == 18 for value in checkpoints)
    assert len(checkpoints) == len(set(checkpoints))


def test_payday_unknown_at_checkpoint_is_not_used() -> None:
    paydays = (
        Payday(payday=date(2026, 2, 27), known_from=date(2026, 1, 1)),
        Payday(payday=date(2026, 3, 27), known_from=date(2026, 3, 20)),
    )
    # A 15 March checkpoint cannot use the second payday because it was only known on 20 March.
    assert next_known_payday(date(2026, 3, 15), paydays) is None
```

- [ ] **Step 2: Write failing balance reconstruction tests**

```python
# tests/unit/test_balances.py
from datetime import datetime, timezone

import pytest

from safe_to_save.balances import CoverageError, reconstruct_balance_minor
from tests.factories import balance_anchor, manifest, transaction


def test_reconstructs_backwards_from_later_anchor() -> None:
    checkpoint = datetime(2026, 1, 10, tzinfo=timezone.utc)
    anchor = balance_anchor("2026-01-20T00:00:00Z", 100_000)
    later = [transaction("a", "2026-01-12T00:00:00Z", -2_000),
             transaction("b", "2026-01-15T00:00:00Z", 5_000)]
    assert reconstruct_balance_minor(checkpoint, (anchor,), later, manifest()) == 97_000


def test_checkpoint_before_coverage_is_withheld() -> None:
    with pytest.raises(CoverageError, match="before coverage_start"):
        reconstruct_balance_minor(
            datetime(2025, 8, 1, tzinfo=timezone.utc), (), (), manifest()
        )
```

- [ ] **Step 3: Implement the pure payday and balance functions**

Use immutable dataclasses:

```python
@dataclass(frozen=True)
class PayCycle:
    start: date
    payday: date


@dataclass(frozen=True)
class ReviewSchedule:
    weekday: int
    local_time: time
    timezone: str


def reconstruct_balance_minor(checkpoint, anchors, transactions, manifest) -> int:
    if checkpoint < manifest.coverage_start:
        raise CoverageError("checkpoint is before coverage_start")
    anchor = min((a for a in anchors if a.captured_at >= checkpoint),
                 key=lambda a: a.captured_at, default=None)
    if anchor is None:
        raise CoverageError("no balance anchor on or after checkpoint")
    if anchor.captured_at > manifest.coverage_end:
        raise CoverageError("anchor is after coverage_end")
    flow_after_checkpoint = sum(
        row.amount_minor
        for row in transactions
        if checkpoint < row.occurred_at <= anchor.captured_at
    )
    return anchor.balance_minor - flow_after_checkpoint
```

`build_pay_cycles()` uses consecutive explicit paydays; a cycle starts on the previous payday and ends at the next payday. `next_known_payday()` filters `known_from <= checkpoint.date()` before choosing the first later payday. `weekly_checkpoints()` constructs local datetimes with `ZoneInfo`, converts each to UTC, and excludes checkpoints at or after payday.

- [ ] **Step 4: Add example persistence**

Task 3 already persists explicit paydays and commitments as part of the imported history contract. `sql/002_examples.sql` creates `feature_examples` with a unique `(dataset_id, checkpoint_at, feature_version)` key plus JSONB `features`, nullable `target_variable_spend_minor`, `status IN ('complete','withheld')`, and `withheld_reason` required exactly when status is `withheld`.

- [ ] **Step 5: Run focused and integration tests**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/unit/test_paycycles.py tests/unit/test_balances.py -v
.\scripts\apply-migrations.ps1
.\.venv\Scripts\python -m pytest -m integration -v
```

Expected: PASS, including the UK DST test and explicit coverage failures.

- [ ] **Step 6: Prepare the task commit, subject to explicit authorisation**

```powershell
git add sql/002_examples.sql src/safe_to_save/paycycles.py src/safe_to_save/balances.py src/safe_to_save/repository.py tests/unit
git commit -m "feat: build payday checkpoints and balances"
```

## Task 5: Create point-in-time features and realised labels

**Files:**
- Create: `src/safe_to_save/features.py`
- Modify: `src/safe_to_save/repository.py`
- Modify: `tests/factories.py`
- Test: `tests/unit/test_features.py`
- Test: `tests/integration/test_feature_repository.py`

**Interfaces:**
- Consumes: `PayCycle`, checkpoint UTC datetime, reconstructed balance, transactions and commitments.
- Produces: `FeatureVector`; `CompletedExample`; `WithheldExample`; `build_example(dataset_id: str, checkpoint_at: datetime, payday: date, balance_minor: int, coverage_start: datetime, transactions: Sequence[CanonicalTransaction], commitments: Sequence[Commitment]) -> CompletedExample | WithheldExample`; repository `save_example()` and `load_completed_examples()`.

- [ ] **Step 1: Write failing leakage and economic-semantics tests**

```python
# tests/unit/test_features.py
import pytest

from safe_to_save.features import build_example
from tests.factories import history_case, replace_future_expense, transaction


@pytest.fixture
def history_inputs():
    return history_case()


def test_future_amount_changes_label_but_not_features(history_inputs) -> None:
    original = build_example(**history_inputs)
    changed_future = replace_future_expense(history_inputs, delta_minor=-5_000)
    changed = build_example(**changed_future)
    assert changed.features == original.features
    assert changed.target_variable_spend_minor == original.target_variable_spend_minor + 5_000


def test_target_nets_refunds_and_excludes_transfers_and_committed_expenses(history_inputs) -> None:
    rows = [
        transaction("expense", "2026-04-20T12:00:00+00:00", -10_000,
                    economic_type="expense", committed=False),
        transaction("refund", "2026-04-21T12:00:00+00:00", 2_000,
                    economic_type="expense", committed=False),
        transaction("bill", "2026-04-22T12:00:00+00:00", -4_000,
                    economic_type="expense", committed=True),
        transaction("pot", "2026-04-23T12:00:00+00:00", -3_000,
                    economic_type="savings_transfer", committed=False),
    ]
    example = build_example(**history_case(future_transactions=rows))
    assert example.target_variable_spend_minor == 8_000
```

Also test that a commitment with `known_from` after the checkpoint is absent from `known_commitments_minor`, and that insufficient pre-checkpoint coverage returns `WithheldExample(reason="insufficient_trailing_history")`.

- [ ] **Step 2: Run the tests and verify the feature builder is missing**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/unit/test_features.py -v
```

Expected: FAIL because `safe_to_save.features` does not exist.

- [ ] **Step 3: Implement the immutable feature and example types**

```python
@dataclass(frozen=True)
class FeatureVector:
    checkpoint_at: datetime
    payday: date
    days_to_payday: int
    balance_minor: int
    known_commitments_minor: int
    variable_spend_7d_minor: int
    variable_spend_28d_minor: int
    daily_spend_stddev_28d_minor: int
    weekday: int
    month: int
    coverage_age_days: int


@dataclass(frozen=True)
class CompletedExample:
    dataset_id: str
    cycle_payday: date
    features: FeatureVector
    target_variable_spend_minor: int


@dataclass(frozen=True)
class WithheldExample:
    dataset_id: str
    checkpoint_at: datetime
    reason: str
```

Use only transactions with `occurred_at <= checkpoint_at` for features. Variable spend is net expense flow with `committed is False`: `max(0, -sum(amount_minor))`. Aggregate daily spend in integer pence and calculate population standard deviation without binary floats:

```python
def integer_pstdev(values: Sequence[int]) -> int:
    if not values:
        return 0
    mean = Decimal(sum(values)) / Decimal(len(values))
    variance = sum((Decimal(value) - mean) ** 2 for value in values) / Decimal(len(values))
    return int(variance.sqrt().quantize(Decimal("1"), rounding=ROUND_HALF_UP))
```

Require 28 complete days of coverage for a completed example. Keep the target separate from `FeatureVector` so future values cannot enter downstream predictors accidentally.

Extend `tests/factories.py` with `history_case(future_transactions=())`, which returns exactly the keyword arguments in the `build_example()` interface. Generate one small synthetic variable expense on each of the 28 days before a fixed checkpoint and append the supplied future transactions between checkpoint and payday. Add `replace_future_expense(case, delta_minor)` that returns a copied dictionary with only the first eligible post-checkpoint expense amount changed. Do not mutate the original tuple.

Compute `known_commitments_minor` as the positive reserve `-sum(commitment.amount_minor)` for commitments satisfying `known_from <= checkpoint_at < due_at` and `due_at.date() <= payday`. This establishes the positive value consumed by Task 7's subtraction.

- [ ] **Step 4: Persist versioned examples idempotently**

Use `feature_version="offline-v1"`. Serialise the dataclass fields to JSON with ISO-8601 timestamps and sorted keys. On rerun, require byte-equivalent features and target for the unique key; raise `ValueError("feature example conflict")` rather than updating a historical example.

- [ ] **Step 5: Add Hypothesis coverage for point-in-time isolation**

Generate arbitrary post-checkpoint expenses, transfers and refunds. Assert that appending them never changes `FeatureVector`, while only eligible variable expenses/refunds change the label. Cap generated lists at 30 rows and amounts at ±1,000,000 minor units to keep the test fast and realistic.

- [ ] **Step 6: Run focused and repository tests**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/unit/test_features.py tests/integration/test_feature_repository.py -v
```

Expected: PASS with no float-money assertions.

- [ ] **Step 7: Prepare the task commit, subject to explicit authorisation**

```powershell
git add src/safe_to_save/features.py src/safe_to_save/repository.py tests/unit/test_features.py tests/integration/test_feature_repository.py
git commit -m "feat: create point-in-time training examples"
```

## Task 6: Implement the three required deterministic baselines

**Files:**
- Create: `sql/003_baselines.sql`
- Create: `src/safe_to_save/baselines.py`
- Modify: `src/safe_to_save/repository.py`
- Modify: `tests/factories.py`
- Test: `tests/unit/test_baselines.py`
- Test: `tests/integration/test_baseline_repository.py`

**Interfaces:**
- Consumes: current `CompletedExample` features and only earlier completed examples.
- Produces: `BaselineForecast`; `UnavailableForecast`; `TrailingMedianBaseline.predict()`; `ComparablePositionBaseline.predict()`; `ConservativeRulesBaseline.predict()`.

- [ ] **Step 1: Write failing baseline tests**

```python
# tests/unit/test_baselines.py
from datetime import date

from safe_to_save.baselines import (
    ComparablePositionBaseline, ConservativeRulesBaseline,
    TrailingMedianBaseline, UnavailableForecast,
)
from tests.factories import baseline_case


def test_trailing_median_uses_three_latest_earlier_cycles() -> None:
    current, historical_examples = baseline_case()
    forecast = TrailingMedianBaseline().predict(current, historical_examples)
    assert forecast.point_minor == 12_000
    assert forecast.training_paydays == (
        date(2026, 1, 28), date(2026, 2, 27), date(2026, 3, 27)
    )


def test_comparable_position_uses_only_plus_or_minus_one_day() -> None:
    current, historical_examples = baseline_case()
    forecast = ComparablePositionBaseline().predict(current, historical_examples)
    assert all(abs(days - current.features.days_to_payday) <= 1
               for days in forecast.training_days_to_payday)


def test_no_comparable_history_is_unavailable_not_zero() -> None:
    current, _ = baseline_case()
    result = ConservativeRulesBaseline().predict(current, ())
    assert isinstance(result, UnavailableForecast)
    assert result.reason == "fewer_than_three_comparable_cycles"
```

- [ ] **Step 2: Run the tests and verify the baseline module is missing**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/unit/test_baselines.py -v
```

Expected: FAIL because `safe_to_save.baselines` does not exist.

- [ ] **Step 3: Implement shared forecast types and integer quantiles**

```python
@dataclass(frozen=True)
class BaselineForecast:
    baseline_name: str
    point_minor: int
    upper_minor: int
    sample_size: int
    training_paydays: tuple[date, ...]
    training_days_to_payday: tuple[int, ...]


@dataclass(frozen=True)
class UnavailableForecast:
    baseline_name: str
    reason: str


def nearest_rank(values: Sequence[int], percentile: Decimal) -> int:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("values must not be empty")
    rank = (Decimal(len(ordered)) * percentile).to_integral_value(rounding=ROUND_CEILING)
    return ordered[max(0, int(rank) - 1)]
```

All baselines must filter to examples with `cycle_payday < current.cycle_payday` before computing anything.

- [ ] **Step 4: Implement exact baseline rules**

- `TrailingMedianBaseline`: use the latest example from each of the three most recent earlier payday cycles; point is the integer median target and upper is the nearest-rank 90th percentile.
- `ComparablePositionBaseline`: use at most the five most recent earlier cycles whose `days_to_payday` differs by no more than one; require at least three; point is median and upper is the 90th percentile.
- `ConservativeRulesBaseline`: require at least three comparable cycles; calculate pace with `Decimal(variable_spend_7d_minor) / Decimal(7) * Decimal(days_to_payday) * Decimal("1.25")`, rounded with `ROUND_CEILING`, and set both point and upper to `max(pace, comparable_90th_percentile)`.

Return `UnavailableForecast` with a stable reason for too little history; never fall back silently from one strategy to another.

Extend `tests/factories.py` with `baseline_case() -> tuple[CompletedExample, tuple[CompletedExample, ...]]`. Build four earlier cycles and one current cycle with targets chosen so the three most recent targets have an integer median of `12000`; give comparable rows days-to-payday values within one day and one older non-comparable row outside that window. All timestamps and paydays must be strictly earlier than the current cycle except the current example itself.

- [ ] **Step 5: Persist predictions without overwriting history**

`sql/003_baselines.sql` creates `baseline_predictions` keyed by `(dataset_id, checkpoint_at, feature_version, baseline_name, baseline_version)`, with nullable forecast fields, `status IN ('available','withheld')`, a required reason for withheld rows and JSONB provenance. Use baseline version `v1`.

- [ ] **Step 6: Run baseline and persistence tests**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/unit/test_baselines.py tests/integration/test_baseline_repository.py -v
```

Expected: PASS; test rows with future payday dates must not affect any earlier prediction.

- [ ] **Step 7: Prepare the task commit, subject to explicit authorisation**

```powershell
git add sql/003_baselines.sql src/safe_to_save/baselines.py src/safe_to_save/repository.py tests/unit/test_baselines.py tests/integration/test_baseline_repository.py
git commit -m "feat: add deterministic spending baselines"
```

## Task 7: Replay historical recommendations and calculate metrics

**Files:**
- Create: `sql/004_backtests.sql`
- Create: `src/safe_to_save/replay.py`
- Create: `src/safe_to_save/metrics.py`
- Create: `src/safe_to_save/report.py`
- Modify: `src/safe_to_save/repository.py`
- Modify: `tests/factories.py`
- Test: `tests/unit/test_replay.py`
- Test: `tests/unit/test_metrics.py`
- Test: `tests/unit/test_report.py`
- Test: `tests/integration/test_backtest_repository.py`

**Interfaces:**
- Consumes: examples, forecasts, actual transactions through payday and an explicit `floor_minor`.
- Produces: `ReplayObservation`; `BacktestSummary`; `replay_one(...)`; `summarise(...)`; `render_json(...)`; `render_markdown(...)`.

- [ ] **Step 1: Write failing policy and path tests**

```python
# tests/unit/test_replay.py
import pytest

from safe_to_save.replay import replay_one
from tests.factories import sample_actual_path, sample_example, sample_forecast


def test_recommendation_reserves_commitments_forecast_and_floor() -> None:
    observation = replay_one(
        example=sample_example(balance_minor=100_000, known_commitments_minor=20_000),
        forecast=sample_forecast(upper_minor=30_000),
        actual_path=(100_000, 85_000, 63_000, 55_000),
        floor_minor=25_000,
    )
    assert observation.recommended_minor == 25_000
    assert observation.minimum_balance_after_saving_minor == 30_000
    assert observation.shortfall_violation is False
    assert observation.reversal_required_minor == 0


def test_higher_floor_never_increases_recommendation() -> None:
    example = sample_example()
    forecast = sample_forecast()
    actual_path = sample_actual_path()
    low = replay_one(example, forecast, actual_path, floor_minor=20_000)
    high = replay_one(example, forecast, actual_path, floor_minor=30_000)
    assert high.recommended_minor <= low.recommended_minor


def test_negative_floor_is_rejected() -> None:
    with pytest.raises(ValueError, match="floor_minor must be non-negative"):
        replay_one(sample_example(), sample_forecast(), sample_actual_path(), floor_minor=-1)
```

Add a Hypothesis version of the monotonic-floor test across non-negative balances, commitments, forecasts and floors.

- [ ] **Step 2: Write failing aggregate-metric tests**

```python
# tests/unit/test_metrics.py
from safe_to_save.metrics import summarise
from tests.factories import (
    available_observation, scored_observation, withheld_observation,
)


def test_unavailable_forecasts_are_counted_but_excluded_from_error_metrics() -> None:
    summary = summarise((available_observation(error_minor=500), withheld_observation()))
    assert summary.checkpoints_total == 2
    assert summary.checkpoints_scored == 1
    assert summary.checkpoints_withheld == 1
    assert summary.mean_absolute_error_minor == 500


def test_useful_yield_is_capped_by_hindsight_safe_amount() -> None:
    summary = summarise((scored_observation(recommended=8_000, hindsight_safe=5_000),))
    assert summary.useful_savings_minor == 5_000
    assert summary.hindsight_safe_minor == 5_000
```

- [ ] **Step 3: Implement replay arithmetic in integer pence**

```python
@dataclass(frozen=True)
class ReplayObservation:
    checkpoint_at: datetime
    cycle_payday: date
    baseline_name: str
    status: str
    withheld_reason: str | None
    forecast_minor: int | None
    forecast_upper_minor: int | None
    actual_variable_spend_minor: int | None
    recommended_minor: int | None
    minimum_balance_after_saving_minor: int | None
    hindsight_safe_minor: int | None
    shortfall_violation: bool | None
    reversal_required_minor: int | None


def replay_one(example, forecast, actual_path, floor_minor):
    if floor_minor < 0:
        raise ValueError("floor_minor must be non-negative")
    if isinstance(forecast, UnavailableForecast):
        return withheld_observation(example, forecast.reason)
    recommended = max(
        0,
        example.features.balance_minor
        - example.features.known_commitments_minor
        - forecast.upper_minor
        - floor_minor,
    )
    minimum_after = min(actual_path) - recommended
    hindsight_safe = max(0, min(actual_path) - floor_minor)
    return ReplayObservation(
        checkpoint_at=example.features.checkpoint_at,
        cycle_payday=example.cycle_payday,
        baseline_name=forecast.baseline_name,
        status="scored",
        withheld_reason=None,
        forecast_minor=forecast.point_minor,
        forecast_upper_minor=forecast.upper_minor,
        actual_variable_spend_minor=example.target_variable_spend_minor,
        recommended_minor=recommended,
        minimum_balance_after_saving_minor=minimum_after,
        hindsight_safe_minor=hindsight_safe,
        shortfall_violation=minimum_after < floor_minor,
        reversal_required_minor=max(0, floor_minor - minimum_after),
    )
```

`actual_path` starts with the reconstructed checkpoint balance and applies every signed main-account transaction through payday in timestamp order. A missing path or coverage gap returns a withheld observation before `replay_one()`.

- [ ] **Step 4: Implement stable aggregate metrics**

`BacktestSummary` contains counts, withheld reasons, MAE, median absolute error, shortfall violation count/rate, reversal count/total minor, recommended total, useful savings minor, hindsight safe minor, useful-yield ratio and median absolute week-to-week recommendation change. Ratios use `Decimal` and serialise as fixed strings with four decimal places; a zero denominator serialises as `null`.

Do not pool different baselines. `summarise()` accepts observations for one baseline and raises on mixed names.

Extend `tests/factories.py` with `sample_example()`, `sample_forecast()`, `sample_actual_path()`, `available_observation(error_minor)`, `withheld_observation()` and `scored_observation(recommended, hindsight_safe)`. Each builder returns the production dataclass type created in Tasks 5–7, uses baseline name `conservative_rules`, and accepts only the overrides named in these tests. Keep all defaults internally consistent so `summarise()` never receives mixed baseline names accidentally.

- [ ] **Step 5: Persist immutable replay runs**

`sql/004_backtests.sql` creates:

- `backtest_runs(run_id uuid primary key, dataset_id, feature_version, baseline_name, baseline_version, floor_minor, input_sha256, started_at, completed_at, status, summary jsonb)`;
- `backtest_observations(run_id, checkpoint_at, status, withheld_reason, forecast_minor, forecast_upper_minor, actual_variable_spend_minor, recommended_minor, minimum_balance_after_saving_minor, hindsight_safe_minor, shortfall_violation, reversal_required_minor, primary key(run_id, checkpoint_at))`.

Create a deterministic `input_sha256` from dataset ID, feature version, baseline version, floor and sorted checkpoint IDs. An identical completed run is a recorded no-op; never mutate its observations.

- [ ] **Step 6: Render deterministic JSON and Markdown reports**

The JSON report uses sorted keys and contains run metadata, one summary per baseline and chronological observations. The Markdown report contains:

1. an explicit `Synthetic demonstration` or `Private evaluation` data label;
2. coverage dates and checkpoint counts;
3. a side-by-side baseline metric table;
4. withheld reason counts;
5. worst three shortfall/reversal cases using checkpoint dates only, with no merchant data;
6. limitations stating that baseline results are not model promotion evidence.

Test both renderers against exact stable strings for the small fixture.

- [ ] **Step 7: Run replay, metric, report and persistence tests**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/unit/test_replay.py tests/unit/test_metrics.py tests/unit/test_report.py tests/integration/test_backtest_repository.py -v
```

Expected: PASS, including the monotonic-floor property test and unavailable-baseline exclusion.

- [ ] **Step 8: Prepare the task commit, subject to explicit authorisation**

```powershell
git add sql/004_backtests.sql src/safe_to_save/replay.py src/safe_to_save/metrics.py src/safe_to_save/report.py src/safe_to_save/repository.py tests
git commit -m "feat: replay and report baseline recommendations"
```

## Task 8: Compose the end-to-end offline workflow

**Files:**
- Modify: `src/safe_to_save/cli.py`
- Modify: `README.md`
- Modify: `scripts/test.ps1`
- Create: `docs/milestone-1-evaluation.md`
- Test: `tests/integration/test_offline_workflow.py`

**Interfaces:**
- Consumes: all public interfaces from Tasks 1–7.
- Produces: CLI commands `db migrate`, `history import` and `backtest run`; a reproducible synthetic report; Milestone 1 verification instructions.

- [ ] **Step 1: Write the failing end-to-end CLI test**

```python
# tests/integration/test_offline_workflow.py
import json

import pytest

from safe_to_save.cli import main

pytestmark = pytest.mark.integration


def test_synthetic_history_runs_through_all_three_baselines(tmp_path) -> None:
    assert main(["db", "migrate"]) == 0
    assert main(["history", "import", "--directory",
                 "tests/fixtures/synthetic_history"]) == 0
    output = tmp_path / "report"
    assert main([
        "backtest", "run",
        "--dataset-id", "synthetic-v1",
        "--review-weekday", "6",
        "--review-hour", "18",
        "--timezone", "Europe/London",
        "--floor-minor", "25000",
        "--data-label", "Synthetic demonstration",
        "--output", str(output),
    ]) == 0
    payload = json.loads((output / "baseline-report.json").read_text(encoding="utf-8"))
    assert set(payload["baselines"]) == {
        "trailing_median", "comparable_position", "conservative_rules"
    }
    assert (output / "baseline-report.md").exists()
```

- [ ] **Step 2: Run the test and verify subcommands are absent**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/integration/test_offline_workflow.py -v
```

Expected: FAIL because the CLI subcommands have not been composed.

- [ ] **Step 3: Compose subcommands without placing domain logic in the CLI**

`build_parser()` must expose:

```text
safe-to-save db migrate
safe-to-save history import --directory PATH
safe-to-save backtest run --dataset-id ID --review-weekday 0..6
  --review-hour 0..23 --timezone Europe/London --floor-minor INTEGER
  --data-label "Synthetic demonstration|Private evaluation" --output PATH
```

Require all backtest options explicitly. Reject a negative floor, an unknown timezone or a data label outside the two allowed values before touching the database. The handlers call repository and pure-domain services; they do not duplicate feature, baseline or replay calculations.

Write output atomically by creating files with a `.tmp` suffix in the selected output directory and replacing the final files only after both renders succeed. Refuse to overwrite an existing report whose `input_sha256` differs; write a timestamped sibling directory instead.

- [ ] **Step 4: Produce the public synthetic evaluation document**

Run the end-to-end command against `tests/fixtures/synthetic_history` and copy its Markdown output to `docs/milestone-1-evaluation.md`. The document must begin:

```markdown
> **Synthetic demonstration:** These values come from invented fixtures. They prove
> reproducibility and evaluation behaviour, not accuracy on Hakim's finances or
> generalisation to Monzo customers.
```

Do not add conclusions not directly supported by the generated metrics.

- [ ] **Step 5: Document private-data operation without including private values**

`README.md` must provide commands for creating `data/private/history/`, importing it and writing reports to `outputs/private/`. State that these directories are ignored, that terminal/chat output should contain only filenames, counts, coverage dates and validation status, and that private results must not be copied into public documentation.

- [ ] **Step 6: Run proportional final verification**

Run:

```powershell
.\scripts\init-local-db.ps1
.\scripts\apply-migrations.ps1
.\scripts\test.ps1
.\.venv\Scripts\safe-to-save.exe history import --directory tests/fixtures/synthetic_history
.\.venv\Scripts\safe-to-save.exe backtest run --dataset-id synthetic-v1 --review-weekday 6 --review-hour 18 --timezone Europe/London --floor-minor 25000 --data-label "Synthetic demonstration" --output outputs/synthetic
git diff --check
```

Expected:

- Ruff and every unit/integration test pass.
- Import reports only synthetic filename, counts, coverage dates and validation status.
- JSON and Markdown reports are generated deterministically.
- The second identical import and backtest are recorded no-ops.
- `git diff --check` reports no whitespace errors when the directory is under Git.
- No file under `data/private/`, `outputs/private/`, `.env.local` or `.postgres-data/` is staged or printed.

- [ ] **Step 7: Perform the Milestone 1 acceptance review**

Confirm with evidence that:

- canonical inputs and balance anchors are validated;
- source evidence is append-only and canonical conflicts fail safely;
- weekly examples are point-in-time correct;
- all three baselines use earlier cycles only;
- coverage and insufficient-history cases are withheld explicitly;
- historical replay produces the required product and error metrics;
- the synthetic report is reproducible and clearly labelled;
- no Milestone 2 modelling or Milestone 4 Monzo connectivity has entered scope.

- [ ] **Step 8: Prepare the milestone commit, subject to explicit authorisation**

```powershell
git add README.md docs/milestone-1-evaluation.md src/safe_to_save/cli.py scripts/test.ps1 tests/integration/test_offline_workflow.py
git commit -m "feat: complete offline baseline milestone"
```
