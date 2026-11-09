# Cloud setup verification — 9 November 2026

Source baseline: `f77e368` on `main`.
Setup branch: `codex/cloud-mobile-setup-2026-11-09`.

The attached Linux workspace uses Python 3.12.14.
Ruff passes for application, tests and Cloud helpers. The non-integration suite
passes with 333 tests, three platform-specific skips and 39 integration tests
deselected. Four Cloud ownership/routing boundary checks also pass.

The complete `bash scripts/cloud/verify.sh` command passed. All 39 integration
tests passed against the separate PostgreSQL 14.24 cluster, including the
synthetic Gate A workflow. Five migrations applied successfully. The synthetic
baseline CLI completed with 22 transactions, 34 checkpoints and reports under
`outputs/cloud-synthetic-v2/`. Re-import and replay returned idempotent no-ops.
Cloud credentials have mode 0600; state and cluster directories have mode 0700.
Stop/start and repeated startup preserve the owned endpoint and generated
synthetic reports. The port probe rejects active listeners and permits a clean
restart after recently closed connections.

Only invented fixtures are used. Private inputs and reports remain on the
original machine, and no private Gate A result or modelling approval is inferred
from synthetic verification. No live API, dashboard or money movement is added.

## Reusable environment settings

- Repository: `https://github.com/FLABDUL/safe-to-save.git`.
- Branch while reviewing setup: `codex/cloud-mobile-setup-2026-11-09`.
- Runtime: Linux, Python 3.11+ and `uv`.
- Setup command: `bash scripts/cloud/setup.sh`.
- Verification command: `bash scripts/cloud/verify.sh`.
- Database: owned synthetic PostgreSQL 14 cluster, loopback port 55433.
- Secrets: no external credential needed for this development setup.
- Network: official PostgreSQL source downloads, Python packages and, if needed,
  the managed Python distribution selected by `uv`.

Save these settings as a reusable Cloud environment in the product. Until this
branch is merged, new checkouts must select it to obtain the setup scripts.
No account-level environment or ChatGPT Project was created.

See `docs/cloud.md` for roadmap documents, laptop chat-search terms and the
separate-project contract with Budget V2. No original Safe to Save chat URL
could be verified from the repository.
