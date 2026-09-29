# Safe to Save project rules

Safe to Save is an offline-first, unaffiliated portfolio project. Milestone 1
is an offline evaluation pipeline only: it must not call Monzo, expose an HTTP
service, serve a dashboard, use an LLM or probabilistic model, or move money.

## Data invariants

- Store money as signed integer minor units: inflows are positive and outflows
  are negative. Never use binary floating point for money.
- Store timestamps as timezone-aware UTC. Derive weekly schedules in
  `Europe/London` so daylight-saving changes remain correct.
- Preserve every imported source line as immutable evidence. Canonical rows may
  point to a newer evidence version; raw evidence is never updated or deleted.
- Personal inputs, credentials, database files, generated real-data reports and
  fitted personal artefacts stay outside Git. Public fixtures must be invented
  and labelled synthetic.
- Missing balance anchors, coverage, paydays, valid risk floors or baselines
  must produce an explicit withheld result, never a fabricated zero.

## Database boundary

Use the dedicated PostgreSQL cluster at `127.0.0.1:55433`, database
`safe_to_save`. Never share Budget V2's cluster or tables.

## Verification

From the project root, run `scripts/test.ps1` after creating `.venv` and
installing the development extra. Do not commit, push, deploy or publish
without explicit authorisation.
