# Private Evaluation Gate A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, reproducible Monzo CSV evaluation gate that converts
private history, replays the existing baselines at three cash floors, and
returns one evidence-backed readiness decision.

**Architecture:** A strict private configuration and path boundary feeds a
versioned Monzo CSV adapter, which writes the existing canonical history
contract plus a source-evidence manifest. The current replay engine runs at a
fixed Monday 06:00 London schedule for three floors; a separate assessment
module verifies the resulting hashes and applies deterministic readiness rules.

**Tech Stack:** Python 3.11+, standard-library `csv`, `datetime`, `decimal`,
`hashlib` and `json`; Pydantic 2; PostgreSQL 14; pytest, Hypothesis and Ruff.

**Spec:** `docs/superpowers/specs/2026-10-09-private-evaluation-gate-a-design.md`

## Global Constraints

- Work in an isolated Git worktree created with `superpowers:using-git-worktrees`
  when implementation starts.
- Inspect the local export's header and timestamp shape before pinning the
  `monzo-csv-v1` profile; never print or copy transaction rows.
- Accept one personal GBP current account only; Pot movements are savings
  transfers, while Pot balances and joint-account activity remain excluded.
- Store money as signed integer minor units and never use binary floating point.
- Store timestamps as aware UTC; derive Monday 06:00 checkpoints in
  `Europe/London`.
- Keep source exports, overrides, canonical private history, floor values and
  real reports under ignored project-local private paths.
- Preserve immutable evidence and fail closed on an unknown schema, ambiguous
  row, missing coverage, failed reconciliation or point-in-time uncertainty.
- Do not call Monzo, use an LLM, train a model, serve a dashboard, move money or
  access Budget V2.
- Console and exception output may contain only filenames, counts, dates,
  statuses, row numbers and stable reason codes.
- Preserve existing Milestone 1 interfaces and behaviour unless this plan
  explicitly extends them.
- Do not commit, push, deploy or publish unless Hakim gives explicit
  authorisation. Each task ends at a review checkpoint until then.

## Review Focus

- A UTF-8 BOM, CRLF records, quoted commas or Unicode text must parse without
  corrupting fields; Task 2 pins this with `test_csv_dialect_variants_parse`.
- A local timestamp in a missing or ambiguous daylight-saving hour must fail
  closed unless the source supplies an unambiguous offset; Task 2 pins this with
  `test_ambiguous_or_nonexistent_timestamp_is_rejected`.
- Repeated or conflicting source rows must never be silently collapsed; Task 2
  pins this with `test_duplicate_source_identity_is_rejected`.
- A symlink or junction must not escape `data/private` or `outputs/private`;
  Task 1 pins this with `test_resolved_private_path_cannot_escape_root`.
- A stale or partially written stage manifest must not be consumed; Tasks 3 and
  5 pin this with `test_changed_prepare_input_preserves_existing_output` and
  `test_assessment_rejects_evidence_hash_mismatch`.

---

### Task 1: Private configuration and path boundary

**Files:**
- Create: `src/safe_to_save/gate_config.py`
- Create: `config/gate-a.example.json`
- Test: `tests/unit/test_gate_config.py`

**Interfaces:**
- Consumes: a JSON config stored beneath `data/private/` and an explicit project
  root.
- Produces: `FloorScenarios`, `GateAConfig`,
  `load_gate_config(path: Path, project_root: Path) -> GateAConfig`, and
  `require_within(path: Path, root: Path, label: str) -> Path`.

- [ ] **Step 1: Write configuration and path-security tests**

  Add `test_three_floors_must_be_non_negative_and_strictly_ordered`,
  `test_period_is_six_to_twelve_complete_months_before_as_of`,
  `test_private_inputs_and_outputs_are_resolved_under_separate_roots`, and
  `test_resolved_private_path_cannot_escape_root`. Assert the model accepts
  `lower=10000`, `primary=25000`, `higher=40000`, rejects equal or negative
  values, accepts six and twelve complete months, rejects partial/current
  months, and rejects `..`, symlink or junction escapes after resolution.

- [ ] **Step 2: Run the new tests and confirm the expected failure**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_gate_config.py -v`

  Expected: FAIL because `safe_to_save.gate_config` does not exist.

- [ ] **Step 3: Implement the frozen Pydantic configuration models**

  `GateAConfig` must pin `schema_version="gate-a-v1"`, `as_of_date`,
  `period_start`, `period_end_exclusive`, `dataset_id`, `account_id`, source and
  auxiliary paths, canonical and report directories, and `FloorScenarios`.
  Resolve the config and all input paths beneath `<project>/data/private`; resolve
  outputs beneath `<project>/outputs/private`. Validate that the period begins
  and ends on the first of a month, ends at the first day of the `as_of_date`
  month, and spans 6–12 complete months.

- [ ] **Step 4: Add a placeholder-only public configuration example**

  The example must use invented identifiers and relative ignored paths. Label
  every money value illustrative and do not include an operational personal
  floor.

- [ ] **Step 5: Run Task 1 verification**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_gate_config.py -v`

  Expected: PASS.

- [ ] **Step 6: Stop at the review checkpoint**

  Show `git diff --check` and the Task 1 diff. Commit only if explicit commit
  authorisation has been supplied; if authorised, use
  `feat: add private evaluation configuration`.

### Task 2: Versioned Monzo CSV adapter

**Precondition:** `data/private/gate-a/monzo.csv` exists locally. Inspect only
its ordered header and timestamp/currency shapes. If it is absent, pause this
task and ask Hakim to export the agreed date range; do not invent the schema.

**Files:**
- Create: `src/safe_to_save/monzo_export.py`
- Create: `tests/fixtures/monzo_export/header-only.csv`
- Create: `tests/fixtures/monzo_export/minimal-synthetic.csv`
- Create: `tests/unit/test_monzo_export.py`

**Interfaces:**
- Consumes: the Task 1 source path and optional line-number-only overrides.
- Produces: `MonzoOverride`, `ParsedMonzoExport`,
  `load_overrides(path: Path | None) -> dict[int, MonzoOverride]`, and
  `parse_monzo_export(path: Path, overrides: Mapping[int, MonzoOverride]) -> ParsedMonzoExport`.
- `ParsedMonzoExport` exposes `source_sha256`, ordered per-line SHA-256 hashes,
  the supported profile name and canonical transactions.

- [ ] **Step 1: Record the real non-sensitive profile contract**

  Pin the exact ordered header as `MONZO_CSV_V1_HEADERS`, the timestamp format
  and whether the timestamp carries an offset. Save a header-only public fixture;
  never copy a personal row. Set `MONZO_PROFILE_VERSION = "monzo-csv-v1"`.

- [ ] **Step 2: Write failing parser and money tests**

  Add `test_exact_header_is_required`, `test_amount_uses_decimal_to_create_signed_pence`,
  `test_stable_ids_and_line_hashes_are_deterministic`,
  `test_non_gbp_and_mixed_account_rows_are_rejected`, and
  `test_csv_dialect_variants_parse`. Use invented merchants and assert an amount
  such as `-12.34` becomes `-1234`, never a float.

- [ ] **Step 3: Write failing classification and safety tests**

  Add `test_refund_remains_positive_expense`,
  `test_pot_movement_is_savings_transfer`,
  `test_unknown_type_requires_line_number_override`,
  `test_ambiguous_or_nonexistent_timestamp_is_rejected`, and
  `test_duplicate_source_identity_is_rejected`. Assert raised errors expose a
  reason code and line number but not the invented merchant, amount or row.

- [ ] **Step 4: Run the adapter tests and confirm failure**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_monzo_export.py -v`

  Expected: FAIL because the adapter is not implemented.

- [ ] **Step 5: Implement strict parsing and classification**

  Use `csv.DictReader`, `Decimal`, explicit timestamp parsing and `ZoneInfo` only.
  Reject any header difference, blank identity, unsupported currency, duplicate
  source identity or transaction outside the configured account. Create stable
  private IDs from the immutable file hash and source line. Map only evidenced
  Monzo types; an unmapped or ambiguous row requires `MonzoOverride` containing
  `source_line`, `economic_type`, `committed` and optional `category`.

- [ ] **Step 6: Run Task 2 verification**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_monzo_export.py -v`

  Expected: PASS.

- [ ] **Step 7: Stop at the review checkpoint**

  Run `git diff --check` and review the fixture for personal data. Commit only
  with explicit authorisation; if authorised, use
  `feat: convert supported Monzo exports`.

### Task 3: Canonical preparation and evidence manifest

**Files:**
- Create: `src/safe_to_save/gate_prepare.py`
- Create: `tests/unit/test_gate_prepare.py`
- Modify: `src/safe_to_save/balances.py:18-61`
- Modify: `tests/unit/test_balances.py:10-99`

**Interfaces:**
- Consumes: `GateAConfig` and `ParsedMonzoExport` from Tasks 1–2, plus canonical
  private anchor, payday and commitment JSONL files.
- Produces: `PreparationResult` with status `completed`, `noop` or
  `fix_data_or_policy`, and
  `prepare_private_history(config: GateAConfig) -> PreparationResult`.
- Adds `validate_anchor_reconciliation(bundle: HistoryBundle) -> None` without
  changing `reconstruct_balance_minor` behaviour.

- [ ] **Step 1: Extract and pin whole-history reconciliation**

  Add `test_validation_requires_anchors_at_both_coverage_boundaries`,
  `test_validation_reconciles_every_signed_flow`, and a regression test proving
  `reconstruct_balance_minor` retains its closed-right boundary. Run
  `tests/unit/test_balances.py` and confirm the new validation tests fail.

- [ ] **Step 2: Implement `validate_anchor_reconciliation`**

  Require anchors exactly at the London-midnight UTC instants for
  `period_start` and `period_end_exclusive`; compare every adjacent anchor with
  all signed flows in `(earlier, later]`. Reuse this helper inside
  `reconstruct_balance_minor` so there is one reconciliation rule.

- [ ] **Step 3: Write failing preparation tests**

  Add `test_prepare_writes_existing_five_file_contract`,
  `test_prepare_manifest_hashes_source_rows_and_transform_versions`,
  `test_identical_prepare_is_a_noop`,
  `test_changed_prepare_input_preserves_existing_output`, and
  `test_reconciliation_failure_returns_fix_decision_without_partial_directory`.
  Assert the generated
  bundle passes `load_history`, and that no source descriptions or amounts
  appear in status objects or exception strings.

- [ ] **Step 4: Run the preparation tests and confirm failure**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_gate_prepare.py tests/unit/test_balances.py -v`

  Expected: FAIL because preparation is not implemented.

- [ ] **Step 5: Implement atomic canonical preparation**

  Generate the existing five files in stable order and a private
  `preparation-manifest.json` containing source/file hashes, row counts,
  `monzo-csv-v1`, config schema and canonical input hash. Write to a sibling
  staging directory, validate with `load_history` and
  `validate_anchor_reconciliation`, then rename into place. Return `noop` for
  an identical manifest; reject a changed input without overwriting the prior
  directory. Convert expected schema, classification, coverage and reconciliation
  failures into a terminal `fix_data_or_policy` status with safe reason codes;
  publish no canonical directory for that outcome.

- [ ] **Step 6: Run Task 3 verification**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_gate_prepare.py tests/unit/test_balances.py tests/unit/test_history.py -v`

  Expected: PASS.

- [ ] **Step 7: Stop at the review checkpoint**

  Run `git diff --check`. Commit only with explicit authorisation; if authorised,
  use `feat: prepare reconciled private history`.

### Task 4: Three-floor replay coordinator

**Files:**
- Create: `src/safe_to_save/gate_evaluate.py`
- Create: `tests/unit/test_gate_evaluate.py`
- Create: `tests/integration/test_gate_evaluate.py`
- Modify: `src/safe_to_save/workflow.py:64-117`
- Modify: `tests/unit/test_workflow.py:10-57`

**Interfaces:**
- Consumes: canonical history from Task 3, `HistoryRepository`, and Task 1 floor
  scenarios.
- Produces: `GateScenarioResult`, `GateEvaluationResult`, and
  `evaluate_gate(config: GateAConfig, repository: HistoryRepository) -> GateEvaluationResult`.
- Extends `WorkflowResult` with immutable `examples`, `observations` and
  `input_sha256` fields while preserving its existing fields.

- [ ] **Step 1: Write failing fixed-schedule and scenario tests**

  Add `test_gate_uses_monday_0600_london_through_dst`,
  `test_gate_runs_lower_primary_higher_in_stable_order`, and
  `test_scenario_directories_and_manifest_are_deterministic`. Assert exactly
  three scenario results and that the schedule is `ReviewSchedule(0, time(6),
  "Europe/London")`.

- [ ] **Step 2: Write failing integration tests for immutable replay**

  Add `test_gate_imports_once_and_persists_three_runs_per_baseline` and
  `test_unchanged_gate_evaluation_is_noop_and_byte_stable`. With three existing
  baselines, assert nine immutable backtest runs and unchanged report bytes on
  rerun.

- [ ] **Step 3: Run the new tests and confirm failure**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_gate_evaluate.py tests/integration/test_gate_evaluate.py -v`

  Expected: FAIL because the coordinator and extended result do not exist.

- [ ] **Step 4: Extend `run_backtest` without changing its reports**

  Return the generated examples, observations and report input hash on
  `WorkflowResult`. Keep current CLI output, persistence, report content and
  no-op semantics unchanged; update existing workflow tests accordingly.

- [ ] **Step 5: Implement `evaluate_gate`**

  Load and import the prepared bundle, run `run_backtest` once per floor into
  `lower/`, `primary/` and `higher/`, then atomically write
  `evaluation-manifest.json` with preparation hash, schedule, feature/baseline
  versions and each report hash. Never place floor values in console status.

- [ ] **Step 6: Run Task 4 verification**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_workflow.py tests/unit/test_gate_evaluate.py -v`

  Run with the isolated database:
  `.\.venv\Scripts\python -m pytest tests/integration/test_gate_evaluate.py tests/integration/test_offline_workflow.py -v`

  Expected: PASS.

- [ ] **Step 7: Stop at the review checkpoint**

  Run `git diff --check`. Commit only with explicit authorisation; if authorised,
  use `feat: replay three private floor scenarios`.

### Task 5: Deterministic gate assessment and evidence pack

**Files:**
- Create: `src/safe_to_save/gate_assess.py`
- Create: `src/safe_to_save/gate_report.py`
- Create: `tests/unit/test_gate_assess.py`
- Create: `tests/unit/test_gate_report.py`

**Interfaces:**
- Consumes: verified preparation/evaluation manifests, the canonical
  `HistoryBundle`, examples and per-scenario observations from Task 4.
- Produces: `GateDecision`, `GateAssessment`,
  `load_gate_evidence(output: Path) -> GateEvidence`,
  `assess_gate(config: GateAConfig, bundle: HistoryBundle, evidence: GateEvidence) -> GateAssessment`,
  and `write_gate_report(output: Path, assessment: GateAssessment) -> Path`.

- [ ] **Step 1: Write failing integrity and decision-order tests**

  Add `test_integrity_or_policy_failure_returns_fix_data_or_policy`,
  `test_insufficient_valid_history_returns_collect_more_evidence`,
  `test_all_thresholds_met_returns_proceed_to_modelling`, and
  `test_all_missed_thresholds_are_reported`. Pin six complete months, six
  completed cycles, 12 scored primary-floor checkpoints per baseline, 70%
  post-warm-up coverage and at least three distinct realised target values.

- [ ] **Step 2: Write failing evidence and sensitivity tests**

  Add `test_assessment_rejects_evidence_hash_mismatch`,
  `test_primary_floor_drives_decision`, and
  `test_material_floor_sensitivity_is_highlighted_without_changing_decision`.
  Pin a material flag at an absolute shortfall-rate change of `0.05` or useful-
  yield change of `0.10` between primary and either sensitivity scenario.

- [ ] **Step 3: Write failing report tests**

  Add `test_json_is_sorted_and_reproducible`,
  `test_markdown_distinguishes_readiness_from_live_safety`,
  `test_report_groups_metrics_by_days_to_payday`, and
  `test_reports_contain_no_transaction_identity_or_description`. Exercise all
  three decisions with synthetic assessments.

- [ ] **Step 4: Run the assessment tests and confirm failure**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_gate_assess.py tests/unit/test_gate_report.py -v`

  Expected: FAIL because assessment and reporting do not exist.

- [ ] **Step 5: Implement versioned decision rules**

  Set `GATE_DECISION_VERSION = "gate-a-v1"`. Exclude the existing 28-day and
  three-cycle warm-up reason codes from the post-warm-up denominator. Count a
  checkpoint as policy-evaluable when its completed example begins at or above
  the primary floor. Return stable reason codes and all missed thresholds; do
  not make baseline performance a live-use approval.

- [ ] **Step 6: Implement the private JSON and Markdown evidence pack**

  Include validation status, coverage, decision reasons, per-floor/per-baseline
  summaries, days-to-payday breakdowns, sensitivity flags, hashes and explicit
  limitations. Render both files before replacing either and preserve existing
  outputs when hashes differ, following `workflow.write_reports` semantics.

- [ ] **Step 7: Run Task 5 verification**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_gate_assess.py tests/unit/test_gate_report.py -v`

  Expected: PASS.

- [ ] **Step 8: Stop at the review checkpoint**

  Run `git diff --check`. Commit only with explicit authorisation; if authorised,
  use `feat: assess private evaluation readiness`.

### Task 6: Guided CLI, synthetic proof and documentation

**Files:**
- Modify: `src/safe_to_save/cli.py:24-78`
- Modify: `tests/unit/test_cli.py:1-31`
- Create: `tests/integration/test_gate_cli.py`
- Create: `tests/fixtures/gate_a_synthetic/` (invented CSV and auxiliary files)
- Modify: `README.md:154-177`
- Modify: `AGENTS.md:1-31`
- Create: `docs/private-evaluation-threat-model.md`
- Create: `docs/gate-a-synthetic-evaluation.md`

**Interfaces:**
- Consumes: Task 1–5 services.
- Produces: `safe-to-save gate-a prepare --config PATH`,
  `safe-to-save gate-a evaluate --config PATH`, and
  `safe-to-save gate-a assess --config PATH`.

- [ ] **Step 1: Write failing CLI parsing and privacy tests**

  Add `test_gate_subcommands_require_private_config`,
  `test_prepare_does_not_require_database_environment`,
  `test_evaluate_requires_isolated_database`, and
  `test_prepare_integrity_failure_prints_fix_data_or_policy`, and
  `test_gate_failure_output_excludes_row_values_paths_credentials_and_floors`.
  Assert success output contains only stage, status, filenames, counts and dates.

- [ ] **Step 2: Write the failing end-to-end synthetic test**

  Build an invented 6–12-month fixture with at least seven recorded paydays,
  12 scoreable Monday checkpoints, refunds, internal transfers, Pot movements,
  commitments and reconciling boundary anchors. Assert prepare → evaluate →
  assess completes, writes `gate-a-report.json` and `gate-a-report.md`, and
  produces the fixture's pinned decision without leaking invented row text to
  captured output.

- [ ] **Step 3: Run the CLI tests and confirm failure**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_cli.py tests/integration/test_gate_cli.py -v`

  Expected: FAIL because the `gate-a` command group is absent.

- [ ] **Step 4: Wire the three guided stages into the CLI**

  Parse config before database setup. `prepare` calls Task 3 without reading
  `DATABASE_URL`; `evaluate` creates the isolated repository and calls Task 4;
  `assess` verifies saved evidence and calls Task 5. Preserve the existing broad
  safe-error boundary so private exception contents never reach stderr.

- [ ] **Step 5: Document operation, privacy and interpretation**

  Replace the manual five-file private instructions with the three Gate A
  commands, state that real reports remain local, and document header-only
  schema discovery. The threat model must cover Git, terminal/log, path escape,
  source mutation, stale-stage and external-service risks. Generate the public
  synthetic report only from the invented fixture and label it non-personal.

- [ ] **Step 6: Run focused and full verification**

  Run: `.\.venv\Scripts\python -m pytest tests/unit/test_cli.py tests/integration/test_gate_cli.py -v`

  Run: `.\scripts\test.ps1`

  Expected: Ruff, all unit tests and all isolated PostgreSQL integration tests
  pass. Confirm `git check-ignore data/private/example outputs/private/example`
  names both paths and `git grep` finds no private source filename or value.

- [ ] **Step 7: Review the public evidence and repository diff**

  Run `git diff --check`, inspect every fixture as invented, and verify
  `git status --short` contains no private or generated real-data artefact.

- [ ] **Step 8: Stop at the final implementation checkpoint**

  Do not commit, push or open a pull request without explicit authorisation. If
  commits have been authorised task-by-task, request a whole-branch review with
  `superpowers:requesting-code-review` before asking how to integrate the branch.
