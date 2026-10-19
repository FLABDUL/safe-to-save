# Private Evaluation Gate A Design

**Status:** Approved design; implementation not started
**Date:** 2026-10-09
**Position in roadmap:** Between Milestone 1 (offline baseline) and Milestone 2
(probabilistic decision engine)

## 1. Purpose

Private Evaluation Gate A determines whether Safe to Save has enough reliable,
point-in-time historical evidence to justify starting probabilistic modelling.
It evaluates the existing deterministic baselines against private Monzo history
without exposing personal financial data or introducing a live API.

The gate returns exactly one decision:

- `proceed_to_modelling`;
- `collect_more_evidence`; or
- `fix_data_or_policy`.

`proceed_to_modelling` means only that the historical evidence is suitable for
Milestone 2. It is not approval for live use and does not assert that a baseline
or future model is safe.

## 2. Goals

- Convert an offline Monzo CSV export into the existing canonical five-file
  history contract.
- Preserve immutable source provenance and fail closed on uncertain data.
- Evaluate Monday-morning decisions using only information available at each
  historical checkpoint.
- Test a private primary cash floor and lower and higher sensitivity floors.
- Produce a reproducible private evidence pack and deterministic gate decision.
- Demonstrate portfolio-relevant data and ML engineering through synthetic
  public fixtures, tests and documentation.

## 3. Non-goals

Gate A will not:

- call the Monzo API or use OAuth, webhooks or automatic Google Sheets exports;
- include joint-account history or Pot balances;
- train or calibrate a probabilistic model;
- serve a dashboard or HTTP API;
- use Budget V2 tables, infrastructure or data;
- use an LLM or any external AI service;
- move money or issue a recommendation for live action; or
- publish private inputs, configuration, results or fitted artefacts.

## 4. Scope

The first evaluation covers one personal Monzo current account. Pot movements
are represented as internal or savings transfers rather than variable spending,
but Pot balances are outside the account balance path. Joint-account activity is
excluded.

The requested history is the latest 12 complete calendar months. Six complete
months is the minimum fallback. The current partial month is excluded.

The canonical checkpoint is Monday at 06:00 in `Europe/London`. Timestamps are
stored as timezone-aware UTC and schedules are derived in London time so
daylight-saving transitions remain correct.

## 5. Architecture

```text
Private Monzo CSV
        +
Private balance anchors, paydays and commitments
        |
        v
Fail-closed validation and reconciliation
        |
        v
Existing five-file canonical history contract
        |
        v
Monday point-in-time replay at three cash floors
        |
        v
Private evidence pack and three-way gate decision
```

Gate A extends rather than replaces Milestone 1. A Monzo adapter and private
evaluation coordinator feed the existing history validation, feature generation,
baselines, replay and metric code. The assessment layer consumes the resulting
metrics and applies versioned decision rules.

## 6. Private input contract

The operator supplies four local inputs:

1. an original Monzo CSV export for the selected date range;
2. balance anchors sufficient to prove the opening and closing balances;
3. recorded paydays, including when each payday was known; and
4. known commitments with amount, due time and `known_from` time.

All inputs remain under ignored private paths. The original export is never
modified. Its SHA-256 hash is recorded in the private run manifest.

Monzo does not publish a stable CSV column contract in its export help. Before
implementation, an actual local export must therefore be inspected for header
names and non-sensitive structural properties. Only a supported, versioned
header profile is accepted. Unknown or changed profiles fail closed rather than
being guessed.

## 7. Ingestion and canonicalisation

The adapter will:

- validate the supported header profile, GBP currency, date range and account
  scope;
- parse monetary values with decimal arithmetic and store signed integer pence;
- generate stable private transaction identifiers and retain source-row
  provenance;
- classify ordinary income and spending, refunds, current-account transfers and
  Pot movements;
- require an explicit local override for an ambiguous row;
- reject malformed rows, duplicate evidence, mixed accounts and unsupported
  currencies;
- reconcile signed flows against supplied balance anchors; and
- generate the existing canonical files:
  `manifest.json`, `transactions.jsonl`, `anchors.jsonl`, `paydays.jsonl` and
  `commitments.jsonl`.

Canonical history continues through the existing validated import path. Every
source line remains immutable evidence. A later transaction occurrence cannot
be used to infer that a bill or payday was known earlier: the recorded
`known_from` value controls what a checkpoint may see.

Validation fails before evaluation when evidence is incomplete, ambiguous or
unreconciled. Diagnostics expose only filenames, row numbers, counts and reason
codes, never row contents.

## 8. Evaluation protocol

The evaluation is an expanding-window historical replay.

- Each checkpoint is Monday at 06:00 `Europe/London`.
- Each checkpoint sees only evidence known by that instant.
- Its target window runs until the next known payday, using the existing
  exclusive London-midnight payday boundary.
- Only fully completed earlier payday cycles may inform a baseline.
- The existing 28-day feature-history rule and baseline warm-up rules remain in
  force.
- Incomplete or unsafe examples are withheld with an explicit reason.

The operator supplies three non-negative private floor values labelled `lower`,
`primary` and `higher`. They must satisfy `lower < primary < higher`. The
software neither derives nor prints these values.

Every required baseline is replayed independently at all three floors. Reports
include:

- candidate, eligible, scored and withheld checkpoint counts;
- forecast error and empirical upper-bound coverage;
- floor-crossing violations;
- reversal frequency and amount;
- safely useful savings yield;
- adjacent-week recommendation stability;
- results by days remaining until payday; and
- withheld-reason distribution.

Weekly checkpoints are independent counterfactual opportunities. Their windows
overlap, so recommendation or yield totals must not be described as cumulative
annual savings.

## 9. Reproducibility

A private run manifest records hashes or versions for:

- original source evidence;
- canonical history;
- local evaluation configuration;
- Monzo transformation profile;
- feature, baseline, replay and decision-rule versions; and
- the generated report inputs.

Repeating unchanged inputs must return an equivalent decision and byte-stable
machine-readable results where existing deterministic report contracts allow
it. A changed input or version creates a distinguishable experiment; it never
silently overwrites earlier evidence.

## 10. Three-way decision rules

Rules are applied in the following order and are based on the primary floor.
Lower and higher floors are sensitivity evidence, not alternative votes.

### 10.1 `fix_data_or_policy`

Return `fix_data_or_policy` when the result cannot be trusted or interpreted,
including:

- failed balance reconciliation;
- unresolved transaction classification;
- missing or contradictory payday evidence;
- a gap inside an asserted complete coverage interval;
- invalid or unordered floor scenarios;
- detected point-in-time leakage; or
- otherwise sufficient history containing fewer than 12 policy-evaluable
  checkpoints whose pre-transfer balance is at or above the primary floor.

No performance conclusion is emitted in this state. The last condition
distinguishes an unsuitable primary floor or product policy from a simple lack
of elapsed history.

### 10.2 `collect_more_evidence`

Return `collect_more_evidence` when inputs are valid and the policy is
interpretable, but any of these evidence thresholds is missed:

- six complete calendar months;
- six completed payday cycles;
- 12 scored Monday checkpoints for every required baseline at the primary
  floor;
- 70% scoreable coverage for every required baseline after the 28-day and
  three-cycle warm-up periods; or
- sufficient variation in realised variable spending to support meaningful
  modelling.

The report names every missed threshold.

### 10.3 `proceed_to_modelling`

Return `proceed_to_modelling` only when all integrity, policy and
minimum-evidence checks pass.

Baseline results then become the challenge for Milestone 2. A future model must
improve probabilistic forecast quality, satisfy the configured shortfall-risk
constraint, avoid worse floor violations or reversals, avoid materially worse
recommendation instability, and preserve the same point-in-time and
reproducibility guarantees.

Material performance changes across the lower and higher floors are highlighted
as policy sensitivity. They do not silently change the primary decision.

## 11. Operator workflow and outputs

The implementation will expose one guided local workflow with three stages:

1. **Prepare:** validate and reconcile inputs, then create canonical history.
2. **Evaluate:** import history and run every baseline and floor scenario.
3. **Assess:** apply the gate rules and create the evidence pack.

Private outputs comprise:

- machine-readable JSON results;
- a concise Markdown review report;
- validation and reconciliation summaries; and
- the private run manifest.

Console output is restricted to filenames, counts, coverage dates, status and
reason codes. Private amounts and metrics remain in ignored local reports.

## 12. Privacy and path controls

- Raw exports, overrides, canonical history, database contents and reports stay
  under ignored local directories.
- Commands refuse private output paths outside the approved project-local
  private directories.
- Logs and exceptions do not contain merchants, descriptions, balances,
  transaction amounts or floor values.
- Errors identify source row numbers without echoing source rows.
- No telemetry, upload, external API or AI service is permitted.
- Private reports are never copied into public documentation.
- Public fixtures contain invented values and are labelled synthetic.

## 13. Public portfolio evidence

The public repository may include:

- this architecture and evaluation methodology;
- the supported Monzo header contract without personal rows;
- parser, validation, reconciliation and privacy tests;
- synthetic fixtures for refunds, transfers, Pot movements and failure cases;
- synthetic demonstrations of all three gate decisions; and
- an explicit privacy threat model and limitations section.

This demonstrates ingestion, immutable evidence, reconciliation,
point-in-time correctness, temporal evaluation, policy sensitivity and
reproducibility without disclosing personal results.

## 14. Error handling

All essential failures are fail-closed. The workflow must not substitute zero,
drop an uncertain row, infer a missing balance or continue after reconciliation
failure. Each failure has a stable reason code suitable for tests and private
operator guidance.

Partial artefacts from a failed prepare or evaluation stage are not eligible for
assessment. A successful stage records its input hash and completion status so a
later stage cannot consume a different or incomplete dataset accidentally.

## 15. Verification strategy

Public automated coverage must demonstrate:

- header-profile detection and unknown-profile rejection;
- exact pounds-to-pence conversion without binary floating point;
- stable identifiers and deterministic conversion;
- refund, transfer and Pot-movement classification;
- explicit ambiguous-row overrides;
- rejection of duplicate, malformed, mixed-account and non-GBP inputs;
- balance-anchor reconciliation;
- payday and commitment leakage prevention;
- Monday scheduling through London daylight-saving transitions;
- three ordered floors without value exposure;
- all three gate decisions through deterministic synthetic fixtures;
- equivalent manifests and reports for identical inputs;
- absence of sensitive fixture values from console output and logs;
- end-to-end prepare, evaluate and assess behaviour; and
- no regression in existing Milestone 1 tests.

## 16. Completion criteria

Gate A implementation is complete when:

- the repository's full verification suite passes;
- a fresh local setup can run the documented synthetic workflow;
- every integrity failure is fail-closed with a specific reason code;
- the synthetic evidence pack is reproducible;
- private paths and generated artefacts remain ignored;
- documentation distinguishes readiness to model from safety for live use;
- the supported Monzo schema is derived from an actual header-only export
  inspection rather than an assumed format; and
- no live API, model, dashboard or Budget V2 dependency has entered the scope.

## 17. Implementation prerequisite

Implementation needs a local Monzo CSV export to establish the real header
profile. Only the header and non-sensitive structural properties need to be
inspected. Transaction contents remain private and must not be pasted into chat
or committed to Git.

## 18. References

- Monzo transaction export help:
  <https://monzo.com/help/budgeting-overdrafts-savings/exporting-my-transactions>
- Monzo statement export help:
  <https://monzo.com/help/account-and-profile/bank-statement-how-to>
- Existing product design:
  `docs/superpowers/specs/2026-09-21-safe-to-save-design.md`
