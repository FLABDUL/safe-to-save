# Cloud development and context

Repository: `FLABDUL/safe-to-save`. Suggested environment label:
**Finance — Safe to Save**. This is a suggested label, not a verified existing
ChatGPT Project or an environment created through account settings.

## Start and verify

Use Linux, Python 3.11 or newer, `uv`, and PostgreSQL 14.
Run `bash scripts/cloud/setup.sh`, then `bash scripts/cloud/verify.sh`.
The setup creates a virtual environment if absent, installs the development
extra, starts its owned synthetic cluster on `127.0.0.1:55433`, database
`safe_to_save`, and applies migrations through the existing CLI.

Set `PG_BIN` to an existing PostgreSQL 14 `bin` directory if available.
Otherwise setup builds checksum-pinned PostgreSQL 14.24 from the official
source archive into the user's cache. This needs `curl`, `tar`, `sha256sum`,
`flock`, GCC and Make, and network access to `ftp.postgresql.org` and package indexes.
No system service or sudo is required. Sharing PostgreSQL binaries with Budget
V2 does not share database state.

Cloud credentials, cluster state and logs stay under ignored `.cloud/`.
Checkout markers, loopback ports and database names are checked before use;
inherited libpq routing options are cleared. Laptop `.env.local` is not read.
Restart with `.venv/bin/python scripts/cloud/db.py start`; stop with
`.venv/bin/python scripts/cloud/db.py stop`. `db.py run -- COMMAND` passes the
generated connection setting privately to that command.

Verification runs Ruff, ownership-boundary checks, unit tests and the existing
integration tests, then imports the synthetic history and produces the baseline
report in `outputs/cloud-synthetic-v2/`. Integration tests clear application
tables in this development database. Keep this cluster synthetic-only; never
use it for retained private evaluation evidence.

Private Gate A inputs and reports remain on the original machine under the
existing private-data policy. No real statements, private evaluations, Monzo
credentials or Budget V2 data are copied by Cloud setup.

## Relationship to Budget V2

Budget V2 is the independent ledger and monthly reporting system.
Safe to Save evaluates whether a weekly payday-aware savings recommendation
would have respected an account floor under historical replay.

The accepted design explicitly uses a separate repository and database.
A read-only versioned Budget V2 snapshot is planned for a later enrichment
milestone. This Cloud setup preserves that separation and the existing
offline-only scope: no live API, dashboard, modelling or money movement.

## Resume context on laptop or phone

Open/select the `safe-to-save` checkout in Codex and read:

1. `AGENTS.md` and `README.md`.
2. `docs/superpowers/specs/2026-09-21-safe-to-save-design.md` for the accepted
   product boundary and longer roadmap.
3. `docs/milestone-1-evaluation.md` for the offline baseline evidence.
4. `docs/superpowers/specs/2026-10-09-private-evaluation-gate-a-design.md`
   and `docs/gate-a-synthetic-evaluation.md` for Gate A.
5. `docs/private-evaluation-threat-model.md` and `docs/cloud-status.md`.

No exact ChatGPT Project name or original chat URL is recorded in this checkout.
On the laptop, search chat history for `Safe to Save`, `safe-to-save`,
`Private Evaluation Gate A` or `2026-10-09`. These are search terms rather than
verified chat titles. Read `Budget project status` for Budget V2 decisions;
do not assume it owns Safe to Save's roadmap.

The checked-in code implements Milestone 1 and Gate A, with synthetic evidence.
There is no checked-in proof that a private-data assessment passed. A synthetic
`proceed_to_modelling` decision is not approval to advance the real project.

Repository setup commands do not save an account-level Cloud environment.
Register this repository and setup command in the product's environment settings
to reuse it in new tasks. This session has no tool for publishing that setting.
