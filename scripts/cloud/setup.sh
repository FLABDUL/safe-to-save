#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v uv >/dev/null || { echo 'Install uv before running setup.' >&2; exit 1; }
[[ -d .venv ]] || uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
[[ -n "${PG_BIN:-}" ]] || bash scripts/cloud/install-postgres.sh
.venv/bin/python scripts/cloud/db.py start
.venv/bin/python scripts/cloud/db.py run -- .venv/bin/safe-to-save db migrate
echo 'Run bash scripts/cloud/verify.sh for synthetic-only verification.'
