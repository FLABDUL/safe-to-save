#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
unset DATABASE_URL
.venv/bin/python -m ruff check src tests scripts/cloud
.venv/bin/python -m unittest discover -s scripts/cloud -p 'test_*.py' -v
.venv/bin/python -m pytest -m 'not integration' -q
# Integration tests clear this dedicated synthetic Cloud database between cases.
.venv/bin/python scripts/cloud/db.py run -- .venv/bin/python -m pytest -m integration -q
.venv/bin/python scripts/cloud/db.py run -- .venv/bin/safe-to-save history import \
  --directory tests/fixtures/synthetic_history
.venv/bin/python scripts/cloud/db.py run -- .venv/bin/safe-to-save backtest run \
  --dataset-id synthetic-v2 --review-weekday 6 --review-hour 18 --timezone Europe/London \
  --floor-minor 25000 --data-label 'Synthetic demonstration' --output outputs/cloud-synthetic-v2
echo 'Synthetic report ready in outputs/cloud-synthetic-v2/.'
