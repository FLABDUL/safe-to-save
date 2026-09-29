# Safe to Save

Safe to Save is a portfolio project for evaluating a payday-aware weekly
spending recommendation from historical transaction data. It is unaffiliated
with Monzo and does not represent Monzo's products, services or advice.

## Milestone 1

Milestone 1 is offline evaluation only. It uses synthetic public fixtures and a
separate local PostgreSQL database to demonstrate immutable history ingestion,
point-in-time-safe features, deterministic baselines and historical replay.
There is no live Monzo API connection, dashboard, LLM, probabilistic model or
money movement in this milestone.

Personal data, credentials, database files and generated reports belong in
ignored paths such as `data/private/` and `outputs/private/`; do not add them
to the repository.

## Product hypothesis and milestone status

The hypothesis is that one weekly, payday-aware suggestion can help someone
decide how much they could set aside while keeping their current account above
their chosen floor. The recommendation reserves known commitments and an upper
spending estimate. Historical replay measures when that reserve would have
failed; a forecast does not guarantee the floor.

Milestone 1 is implemented: validated history import, weekly examples, three
deterministic baselines, persisted replay and reproducible reports. The public
[synthetic evaluation](docs/milestone-1-evaluation.md) demonstrates the workflow
and its withholding behaviour. Its fixtures are deliberately small: two
strategies lack enough comparable cycles to score. It is not evidence of
performance on personal finances or of a benefit over Monzo's app.

Later milestones cover private-data evaluation, quantile modelling and model
comparison, then live ingestion and a dashboard. Insights remain **weekly**.
Any future Pot transfer needs explicit user approval; automatic money movement
is outside this project phase. No model has been trained or promoted here.

## Local setup and verification

Use PowerShell from this directory, Python 3.11+ and PostgreSQL 14 executables
under `C:\Program Files\PostgreSQL\14\bin` (the local scripts' configured path).
The database scripts create an isolated cluster listening only on
`127.0.0.1:55433`, database `safe_to_save`, and store its connection setting in
ignored `.env.local`. They refuse other ports or unrelated clusters.

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\scripts\init-local-db.ps1
.\scripts\apply-migrations.ps1
.\scripts\test.ps1
```

Full verification runs Ruff, unit tests and PostgreSQL integration tests; it
fails if the isolated database is unavailable. `scripts/test.ps1 -UnitOnly`
explicitly runs the checks that do not need PostgreSQL.

The integration fixture **clears application tables in this dedicated
database** between tests. Run verification before private evaluation, retain
the original private history files and reimport them if tests are run later.
It never targets Budget V2's database. Shut the local cluster down with
`scripts/stop-local-db.ps1` when finished.

## Reproduce the synthetic report

Load the local connection setting into the current shell without displaying it,
then run these commands. All scheduling/policy options are explicit; weekday
`6` means Sunday and the hour is London local time, including daylight saving.

```powershell
$env:DATABASE_URL = ((Get-Content .env.local | Where-Object { $_ -match '^DATABASE_URL=' }) -replace '^DATABASE_URL=', '')
.\.venv\Scripts\safe-to-save.exe db migrate
.\.venv\Scripts\safe-to-save.exe history import --directory tests/fixtures/synthetic_history
.\.venv\Scripts\safe-to-save.exe backtest run --dataset-id synthetic-v2 --review-weekday 6 --review-hour 18 --timezone Europe/London --floor-minor 25000 --data-label "Synthetic demonstration" --output outputs/synthetic-v2
```

Outputs are `outputs/synthetic-v2/baseline-report.json` and `baseline-report.md`.
Rerunning the same import records a no-op audit attempt; rerunning the same
backtest returns the existing immutable runs and byte-identical reports.
An input/configuration hash identifies each report. Different inputs targeting
an existing report are written to a timestamped sibling directory. Both files
are rendered before either final file is replaced, with `.tmp` staging and
atomic replacement of each individual file. The pair is not a filesystem
transaction if a process stops between the two replacements.

The CLI prints only filenames, counts, coverage dates and validation status.
Detailed validation/database exceptions are suppressed because they can contain
private values or connection details; a failed command exits non-zero. These
commands require the editable installation from this checkout, whose SQL files
are used by `db migrate`.

## Offline history contract

Supply five UTF-8 files in a directory. JSONL files contain one JSON object per
line, with no blank lines. `config/example-history.json` describes filenames
and illustrative settings; `tests/fixtures/synthetic_history` contains invented
examples. Money uses signed integer GBP minor units and timestamps must have
an explicit timezone; ingestion normalises them to UTC.
Booleans, numeric strings and floating-point values are rejected for all money
fields, including apparently integral floats.

| File | Required content |
| --- | --- |
| `manifest.json` | `dataset_id`, `account_id`, `currency` (`GBP`), `coverage_start`, `coverage_end`; the interval attests continuous, complete history. |
| `transactions.jsonl` | `transaction_id`, `occurred_at`, `amount_minor`, `currency`, `status` (`settled`), `economic_type`; optional `committed` (default false) and `category`. |
| `anchors.jsonl` | `captured_at`, `balance_minor`, `currency`; a trustworthy account balance at an explicit instant. |
| `paydays.jsonl` | `payday` and `known_from`, both calendar dates; dates must be explicitly recorded, not guessed from income. |
| `commitments.jsonl` | `commitment_id`, `known_from`, `due_at`, non-positive `amount_minor`, `description`. |

The five economic types are `expense`, `income`, `internal_transfer`,
`savings_transfer` and `adjustment`. Expenses are negative; refunds are positive
expense rows and reduce variable spend. Committed expenses and transfers do
not enter the variable-spend target, but every signed account flow enters the
realised balance path. Use a fresh dataset ID for a revised dataset: conflicting
canonical identities are rejected rather than overwritten. Imported raw lines
are retained as immutable audit evidence; malformed files fail validation.
An otherwise valid import containing conflicting transaction IDs is recorded
as rejected with every parsed source record and its manifest; it creates no
canonical rows. Malformed files fail before this parsed-evidence audit path.
No Monzo export converter or live API ingestion is included yet.

## Leakage controls and interpretation

Weekly checkpoints span the coverage interval in `Europe/London`. An explicit
pay cycle and its next payday must already be known at the checkpoint. The
entire target window through London midnight starting payday must be covered
before an example can supply a label. A later anchor reconstructs the balance
using signed intervening flows; absent or unusable evidence withholds a result.
Anchors across fully covered intervals must reconcile with all signed flows;
contradictions explicitly withhold the checkpoint. Known commitments use the
same exclusive London midnight payday boundary as the realised target window.
Trailing spending features need 28 days of complete history.

The corrected public history is `synthetic-v2`: its closing anchor reconciles
with its opening anchor and complete signed flows. Feature version `offline-v2`
includes the corrected payday commitment boundary. Earlier dataset and feature
versions are not overwritten; new report input hashes distinguish this evidence.

Only earlier cycles whose payday has passed can train a baseline. Each baseline
uses at most one row per cycle. Trailing median needs three earlier cycles;
comparable position and conservative rules require three cycles within one day
of the current days-to-payday position. Their upper estimates are deterministic
rules/empirical quantiles, not calibrated probabilistic predictions.

Reports retain withheld checkpoints in coverage counts, exclude them from
scored error/safety denominators and report undefined metrics as `null`, not
zero. They include forecast error, empirical upper coverage, floor violations,
reversals, useful savings and recommendation stability. Checkpoints are
independent counterfactual opportunities: their windows overlap, so totals
must not be read as cumulative savings. Reversal amounts may include a floor
deficit that existed even without a suggested transfer.

## Private evaluation

Keep private inputs and results local. Both `data/private/` and all `outputs/`
are ignored, as are `.env.local` and the database/log directories. The data label
is the operator's declaration; the software cannot establish whether data is
synthetic. Never label personal inputs as a synthetic demonstration.

```powershell
New-Item -ItemType Directory -Force data/private/history | Out-Null
# Place the five validated history files here, with a non-public dataset ID.
.\.venv\Scripts\safe-to-save.exe history import --directory data/private/history
.\.venv\Scripts\safe-to-save.exe backtest run --dataset-id YOUR_DATASET_ID --review-weekday 6 --review-hour 18 --timezone Europe/London --floor-minor YOUR_FLOOR_IN_MINOR_UNITS --data-label "Private evaluation" --output outputs/private
```

Load `DATABASE_URL` privately as in the synthetic instructions. Replace the two
placeholders with the manifest ID and a non-negative integer floor. Terminal
and chat output should contain only filenames, counts, coverage dates and
validation status. Do not paste transactions, balances, merchants, credentials
or generated private metrics into chat. Private reports must never be copied
into public documentation; the checked-in evaluation is synthetic only.

The immediate next milestone is an evidence-led private evaluation against
these baselines, including enough completed comparable cycles to measure
coverage and safety. Advance to modelling only after that evidence is reviewed.
