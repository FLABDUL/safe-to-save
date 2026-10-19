# Gate A synthetic evaluation

This checked-in evaluation description is based only on invented, non-personal
evidence in `tests/fixtures/gate_a_synthetic/`. It contains no copied account
row, personal identifier, private report or operational floor.

## Evidence

The fixture covers seven complete months, from October 2025 through April 2026.
It records thirty explicit weekly paydays and enough Monday checkpoints for all
three deterministic baselines to score at least twelve primary-floor examples.
Its sixty invented transactions include card spending, refunds, internal
transfers, Pot movements, commitments and positive income. Two exact
London-midnight boundary anchors reconcile with every signed flow.

The CLI integration proof runs the complete `prepare`, `evaluate`, `assess`
sequence twice. The second preparation and evaluation are safe no-ops, both
final assessment reports remain byte-identical, and aggregate database checks
prove neither assessment creates replay runs. Evaluation evidence and final
reports occupy separate children of the configured output root, so assessment
publication cannot invalidate a later evaluation. Captured terminal output is
checked for transaction text, identifiers, private paths, floors and database
credentials.

## Pinned result

The synthetic fixture's decision is `proceed_to_modelling`. That result shows
that the deterministic pipeline can satisfy its pinned evidence thresholds on
constructed data. It is portfolio and model-readiness evidence only. It does
not demonstrate performance on personal finances, validate a live model, prove
a benefit over Monzo, or approve any live savings recommendation or transfer.
