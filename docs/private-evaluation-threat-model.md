# Private evaluation threat model

This model covers the offline Gate A workflow. It assumes the workstation and
the operator's account are trusted. It does not claim protection from a
compromised operating system or a user deliberately publishing private files.

## Protected assets

- Transaction rows, provider identifiers, balances, commitments and paydays.
- Private paths, chosen floors, generated metrics and reports.
- Database credentials and the isolated local database.
- Immutable source and stage evidence used to justify a Gate A decision.

## Boundaries and controls

### Git leakage

Real inputs, canonical bundles and generated reports live only below ignored
`data/private/` and `outputs/private/` roots. Public fixtures are invented and
labelled synthetic. Verification checks ignore rules and tracked files before a
change is handed off. This cannot prevent a deliberate forced add, so review
must still reject any personal row or generated private report.

### Terminal and log leakage

The CLI prints only stage, stable status, safe report basenames and aggregate
counts. Its error boundary reports an exception type, never exception text.
Operators must not add debug logging around private parsers or paste generated
private metrics into chat, issues or CI logs.

### Path escape

Configuration must itself be below `data/private/`. Input paths resolve below
that root and output paths below `outputs/private/`; symlinks and Windows
junctions are resolved before use. Input and output roots are separate. A path
outside either boundary is rejected before source or database access.

### Source mutation and races

Preparation reads exact source bytes and records source, physical-line and
canonical-file hashes. It stages a complete canonical bundle before publication
and refuses to overwrite a different existing bundle. Evaluation revalidates
the complete preparation evidence before import. The local filesystem remains
the trust boundary: another process with write access could race a read, so run
Gate A only while exports are closed and retain the original immutable export.

### Stale or partial stage evidence

Each stage verifies schema versions, file sets, row counts, content hashes,
account attestation and policy identity. Existing differing preparation,
evaluation or assessment outputs fail closed. `assess` reads saved evidence and
does not create a fresh replay, preventing an apparently complete decision from
silently mixing stages. Evaluation evidence and final reports use separate
children of the private output root so a completed assessment cannot mutate or
invalidate replay evidence.

### Database isolation

Only the loopback cluster at `127.0.0.1:55433`, database `safe_to_save`, is
accepted. Host-address and service overrides are rejected before connection.
Integration tests may clear application tables in that dedicated database, so
they must never target Budget V2 or another PostgreSQL cluster.

### External services

Gate A makes no Monzo API, HTTP, dashboard, LLM, telemetry or other external
service call. The CSV is supplied manually and all processing is local. This
absence avoids network disclosure but provides no automatic freshness or
provider-side account verification; the operator attests the account scope.

## Residual risk

The reports are model-readiness evidence, not proof that a live financial
recommendation is safe. Backups, shell history, endpoint monitoring and local
administrator access remain outside the application boundary.
