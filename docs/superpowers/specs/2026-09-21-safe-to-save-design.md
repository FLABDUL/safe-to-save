# Safe to Save design

**Status:** Approved design

**Date:** 21 September 2026
**Primary portfolio target:** Machine Learning Engineering at Monzo

## 1. Purpose

Safe to Save is a personal Monzo companion that recommends how much money can be moved into savings each week without creating an unacceptable risk of the main spending account falling below a user-defined minimum balance before the next payday.

The product exists for two related outcomes:

1. Give Hakim a calm, useful weekly saving decision that adapts to current spending, known commitments and uncertainty.
2. Demonstrate an end-to-end, product-aware ML system suitable for a Monzo Machine Learning Engineering portfolio.

It is not a generic spending dashboard. Monzo already provides connected accounts, Trends, Salary Sorter, scheduled Pot transfers and roundups. Safe to Save addresses the narrower gap between fixed saving rules and a personalised, uncertainty-aware weekly saving recommendation.

## 2. Product principles

- **Weekly, not noisy:** financial events arrive continuously, but the product issues one stable weekly review.
- **Decision first:** lead with the recommended amount, range and risk; make technical detail available beneath it or in a separate model-health view.
- **Safety over yield:** avoiding a pre-payday shortfall is the hard constraint. Identifying more saving is valuable only within that constraint.
- **Evidence before language:** deterministic calculations and model outputs establish the recommendation. An LLM must never calculate or alter the amount.
- **Explain uncertainty:** show a range, shortfall probability, account coverage and data freshness rather than false precision.
- **Human control:** V1 is advisory. A later phase may support an explicit, user-approved Pot transfer. Automatic transfers are outside scope.
- **Fail closed:** withhold the amount when inputs, model state or coverage are not trustworthy.
- **Private by design:** personal financial data, personal model artefacts and real derived values never enter the public repository.

## 3. User experience

### 3.1 Weekly Safe-to-Save Review

The primary screen shows:

- recommended amount to save;
- conservative recommendation range;
- estimated probability of falling below the configured minimum balance before payday;
- days until the next expected payday;
- expected known bills and variable spending until payday;
- the two or three factors that materially changed the recommendation;
- source coverage and freshness;
- the outcome of the previous recommendation: remained safe, became tight, or would have required reversal.

The user explicitly configures both the minimum balance floor and maximum tolerated shortfall probability before live recommendations are enabled. The product does not silently choose financial risk preferences.

### 3.2 Cadence

- Monzo events are ingested continuously.
- Features are kept current after reconciliation.
- A review is generated once per week on a user-configured local day and time.
- Ordinary transactions do not create unsolicited extra insights.
- A material data failure marks the current recommendation stale; it does not generate a replacement recommendation until inputs are reconciled.

### 3.3 Action phases

- **Phase 1 — advisory:** show the recommendation and evidence only.
- **Phase 2 — review and transfer:** after the live-evaluation gate passes, add an explicit confirmation that deposits the approved amount into a user-selected, API-eligible Monzo Pot.
- **Excluded:** automatic saving without per-transfer approval.

## 4. Scope

### 4.1 Initial scope

- One private Monzo user.
- Monzo current-account transactions, balance and available Pot information exposed by the Developer API.
- Backfill followed by webhook-driven incremental ingestion.
- Weekly payday-aware forecasts.
- Public synthetic-data mode using the same pipeline, model and user interface.
- A customer-facing weekly review and a separate model-health view.

### 4.2 Later scope

- A versioned, read-only Budget V2 adapter for Amex and Nationwide state.
- Explicit freshness and coverage indicators for every included account.
- User-approved Pot transfers after the promotion gate.

### 4.3 Non-goals

- A public or multi-user financial product.
- Financial advice or regulated suitability claims.
- Automatic transfers.
- Rebuilding Budget V2's canonical ledger or monthly insight workflow.
- Direct Amex or Nationwide API integrations in V1.
- Deep learning without evidence that it materially improves temporal hold-out performance.
- LLM-authored insights in V1.
- Claims that a model trained on one person's history generalises to Monzo's customer base.

## 5. System boundaries

Safe to Save is a separate repository and deployable application. Budget V2 remains an independent upstream financial system.

```text
Monzo OAuth + API + webhooks
              |
              v
ingestion and reconciliation
              |
              v
immutable provider evidence -> canonical Monzo transactions and balances
                                      |
                                      v
                           point-in-time feature snapshots
                                      |
                                      v
             quantile forecast -> cash-flow simulation -> policy guardrail
                                      |
                                      v
                            weekly recommendation record
                             |                     |
                             v                     v
                    weekly review UI       model-health UI

Later: Budget V2 versioned snapshot -------^
```

Budget V2 integration uses a documented export or API contract. Safe to Save does not read Budget V2's internal tables directly and does not share its database.

## 6. Components

### 6.1 Monzo connector

- Implements confidential OAuth with server-side secret storage and refresh-token rotation.
- Backfills available transaction history after authentication.
- Registers and receives account webhooks over HTTPS.
- Treats webhooks as notifications rather than complete evidence: every event is reconciled with the API.
- Handles duplicate, delayed and out-of-order delivery idempotently.
- Stores provider identifiers and immutable raw payload versions.
- Uses integer minor units for money.

The connector is suitable only for the owner's account or Monzo's explicitly allowed small-user development scope. The project is not presented as a public Monzo application.

### 6.2 Canonical financial layer

- Normalises transaction status, timestamps, signs and account identity.
- Preserves pending-to-settled changes without deleting provider evidence.
- Represents transfers and Pot movements explicitly so they are not treated as consumption.
- Stores reconciled balance observations separately from transaction-derived cash flow.
- Records data gaps and freshness rather than guessing missing activity.

### 6.3 Feature pipeline

Every feature snapshot is point-in-time correct and reproducible from information available at the snapshot timestamp.

Initial features include:

- days until payday and position within the payday cycle;
- current reconciled balance and configured minimum balance floor;
- known dated bills and commitments before payday;
- trailing spending pace over several windows;
- spending volatility and recent forecast residuals;
- weekday, month and seasonal effects;
- category and essential/discretionary mix where classifications are available;
- recurring-payment signals;
- recent unusual-spending indicators;
- account coverage and source freshness.

Features that cannot be reconstructed historically without leakage are excluded from training and backtesting.

### 6.4 Model training

Each historical weekly checkpoint is a training or evaluation example. The target is uncertain variable spending from the checkpoint until the next payday. Known dated commitments remain deterministic inputs rather than labels for the ML model.

Required baselines:

- recent trailing median;
- comparable position in previous payday periods;
- conservative rules-only estimate.

The initial candidate is an interpretable quantile gradient-boosted model. It predicts multiple spending quantiles rather than one point estimate. A neural sequence model may be evaluated later only as a challenger.

Training records:

- dataset and feature versions;
- training cutoff;
- model parameters and code version;
- baseline and candidate metrics;
- calibration results;
- artefact checksum;
- promotion decision and rationale.

### 6.5 Cash-flow simulation and decision policy

The simulator combines:

- current available balance;
- known dated inflows and outflows;
- the predicted distribution of variable spending;
- the configured minimum balance floor;
- explicit penalties for stale or incomplete data.

Essential Monzo balance or transaction gaps always withhold the recommendation. A data-quality penalty applies only to an optional later source, such as a temporarily stale Budget V2 snapshot, when the configured coverage policy permits Monzo-only inference. That policy and penalty are versioned and disclosed in the review.

It produces possible balance paths until payday. The policy chooses the largest non-negative recommendation whose estimated probability of crossing the minimum balance floor is no greater than the user's configured risk threshold.

Conceptually:

```text
current available balance
- known commitments
- conservative variable-spend estimate
- minimum balance floor
- data-quality penalty
= safe-to-save recommendation
```

The full simulation, rather than this shorthand equation, determines the final amount.

### 6.6 Explanation layer

V1 explanations are deterministic. They compare the current recommendation with the previous weekly recommendation and name only material drivers supported by model inputs or scheduled commitments.

An explanation must not infer motivation, label spending as morally good or bad, or present correlation as causation. If an LLM is added later, it may paraphrase a structured evidence object but cannot add facts, change numbers or suppress risk and coverage warnings.

### 6.7 Weekly recommendation service

- Runs as an idempotent scheduled job.
- Reconciles Monzo data before inference.
- Materialises the exact feature snapshot used.
- Links every recommendation to model, feature and policy versions.
- Prevents duplicate reviews for the same configured weekly checkpoint.
- Persists withheld recommendations and their reason.

### 6.8 User interface

The primary interface uses the approved decision-first layout. It prioritises the amount, range, shortfall risk, material drivers and coverage status.

The model-health view exposes:

- performance against baselines;
- quantile coverage and calibration;
- error by position in the payday cycle;
- recommendation and outcome history;
- feature and residual drift;
- webhook, reconciliation and data-freshness health;
- current model, feature and policy versions.

## 7. Public and private modes

### 7.1 Public repository

The public project contains:

- ingestion interfaces and a Monzo connector without credentials;
- canonical schema and migrations;
- feature, training, evaluation and inference code;
- policy and explanation code;
- tests and replay fixtures;
- a deterministic synthetic financial-history generator;
- the weekly review and model-health interfaces;
- architecture documentation, model card and evaluation report generated from synthetic or explicitly non-personal data.

### 7.2 Private deployment

The private deployment supplies ignored environment configuration, encrypted credentials, personal database contents and personal model artefacts. Public examples must not reproduce real merchant names, transaction amounts, balances, recommendation history, feature summaries or fitted parameters derived from personal data.

Synthetic data exists to make the system reproducible and demonstrable. It is not used as evidence that the personal model is accurate.

## 8. Evaluation

### 8.1 Backtesting

Evaluation uses rolling, payday-aware temporal splits. For every checkpoint, all labels and future-derived features are inaccessible until the simulated future period completes.

The system reports results overall and by:

- distance from payday;
- normal versus unusually high-spending periods;
- data coverage and freshness state;
- season and available history length.

### 8.2 Product metrics

- **Shortfall violation rate:** proportion of recommendations that would have caused the balance to cross the configured floor before payday.
- **Reversal proxy:** proportion that would have required some or all of the recommended amount to return before payday.
- **Useful savings yield:** safely spare money identified without violating the risk constraint.
- **Recommendation stability:** unreasonable movement between adjacent weekly recommendations without a corresponding change in evidence.

### 8.3 Model metrics

- quantile coverage and calibration;
- pinball loss;
- spending-forecast error by horizon;
- performance relative to every required baseline;
- feature and residual drift.

### 8.4 Promotion gate

A candidate model is promoted only when it:

1. beats the required baselines on rolling temporal evaluation;
2. satisfies the configured shortfall-risk constraint within the uncertainty of the available sample;
3. does not create materially worse recommendation instability;
4. passes data, leakage, reproducibility and policy-invariant tests;
5. has a completed model card documenting intended use, limitations and failure modes.

Phase 2 transfers additionally require at least two complete payday cycles of live advisory operation, no unresolved reconciliation gaps, acceptable live calibration and explicit user approval to activate transfers.

## 9. Monitoring and failure behaviour

Monitor three layers:

- **System:** webhook receipt, API reconciliation, job success, latency and storage health.
- **Data and features:** freshness, missingness, unexpected distributions, payday detection and account coverage.
- **Model and product:** quantile coverage, residual drift, shortfall violations, reversal proxy, recommendation yield and stability.

The system withholds a recommendation when:

- transaction or balance data is stale or incomplete;
- a webhook gap cannot be reconciled;
- payday or known commitments cannot be established;
- inputs are materially outside the model's supported range;
- model, feature and policy versions are incompatible;
- inference or simulation fails;
- the user's risk configuration is missing.

The UI states the specific reason and the last successfully reconciled time. It never converts a failure into a fabricated £0 recommendation.

## 10. Security and privacy

- Keep OAuth client secrets and refresh tokens server-side and encrypted at rest.
- Never log access tokens, refresh tokens or complete raw payloads in application logs.
- Validate OAuth state and redirect URIs.
- Authenticate the private dashboard even though it has one user.
- Verify webhook payloads through subsequent API reconciliation rather than trusting the incoming body as final evidence.
- Apply least privilege to the database and deployment environment.
- Support immediate token revocation and deletion of the private deployment's data.
- Separate public demo infrastructure from the private financial database.

## 11. Testing

Required automated coverage includes:

- Monzo API contract fixtures and authentication-state handling;
- webhook replay, duplication, delay and out-of-order delivery;
- pending-to-settled transaction transitions;
- integer-money and sign invariants;
- canonical idempotency and reconciliation;
- point-in-time feature correctness and explicit leakage tests;
- deterministic training and artefact checksums where supported;
- rolling temporal backtests;
- quantile calibration and baseline comparisons;
- property tests for the policy, including monotonic responses to a higher floor or stricter risk threshold;
- withheld-recommendation failure paths;
- weekly-job deduplication;
- end-to-end synthetic history through dashboard output;
- Phase 2 transfer confirmation, idempotency and duplicate-prevention tests before that phase is enabled.

## 12. Technical shape

- Python owns ingestion, reconciliation, feature generation, training, evaluation, inference and the API.
- PostgreSQL stores evidence, canonical records, features, recommendation history and model metadata.
- SQL handles auditable set-based transformations; Python handles modelling and simulation.
- A small HTTP service receives OAuth callbacks and webhooks and serves the private API.
- Idempotent command-line jobs perform backfills, training, evaluation and weekly inference so the deployment scheduler remains replaceable.
- The web interface remains intentionally thin because the portfolio focus is ML engineering rather than frontend complexity.
- Model artefacts are content-addressed and linked to immutable metadata. A separate model-platform dependency is not required for V1.

Exact framework and hosting choices belong in the implementation plan, but they must preserve these boundaries and support a public synthetic mode plus a physically separate private data store.

## 13. Delivery milestones

### Milestone 1 — Offline baseline

- Define canonical Monzo records and payday periods.
- Build point-in-time weekly examples.
- Implement required baselines and historical replay.
- Produce an initial evaluation report.

### Milestone 2 — Probabilistic decision engine

- Train and calibrate the quantile model.
- Implement balance-path simulation and policy guardrails.
- Add deterministic explanations and the model card.
- Demonstrate that promotion criteria are enforceable.

### Milestone 3 — Public synthetic product

- Generate deterministic synthetic histories.
- Run the complete pipeline without private data.
- Deliver the decision-first weekly review and model-health view.
- Document architecture, assumptions and failure cases.

### Milestone 4 — Private Monzo shadow mode

- Complete OAuth, backfill, webhooks and reconciliation.
- Deploy the private data path securely.
- Generate weekly advisory reviews without moving money.
- Monitor at least two complete payday cycles.

### Milestone 5 — Approved Pot transfer

- Review live calibration and operational evidence.
- Add explicit transfer confirmation and duplicate prevention.
- Enable transfers only for an API-eligible selected Pot.

### Milestone 6 — Budget V2 enrichment

- Define a versioned Budget V2 snapshot contract.
- Add Amex and Nationwide context with source freshness.
- Re-evaluate calibration and recommendation value before promotion.

### Planning boundary

This product design intentionally spans the full path from offline evidence to controlled action, but it is too large for one implementation plan. The first implementation plan covers Milestone 1 only. Each later milestone receives its own reviewed scope and implementation plan after the preceding milestone meets its acceptance criteria. Approval of this design does not approve automatic progression between milestones.

## 14. Portfolio evidence

The public project should make the following easy to inspect:

- the customer problem and why fixed saving rules do not answer it;
- the asymmetric cost of recommending too much versus too little;
- temporal feature and evaluation discipline;
- baseline choice and model trade-offs;
- calibrated uncertainty and explicit decision policy;
- model and system monitoring;
- honest limitations of personal data and synthetic demonstrations;
- a documented failure that changed the design;
- the path from offline evaluation to shadow mode and controlled action.

This directly supports discussion of problem framing, product collaboration, end-to-end ML solution design, model lifecycle ownership, practical trade-offs and measurable impact.

## 15. External constraints and references

- Monzo's Developer API is intended for the owner's account or a small explicitly permitted set of users, not a public application: <https://docs.monzo.com/>.
- The API provides OAuth, transactions, balances, Pots and account webhooks, subject to the documented permissions and lifecycle.
- Monzo already offers fixed saving and budgeting mechanisms including Salary Sorter, roundups and Trends; Safe to Save must remain differentiated by personalised, uncertainty-aware weekly decision support.
- Monzo's published ML material emphasises personalisation, product-aware solution design, complete model lifecycles, monitoring, explainability and safe deployment: <https://monzo.com/blog/machine-learning-at-monzo-in-2025> and <https://monzo.com/blog/interviewing-for-machine-learning-at-monzo>.

## 16. Acceptance criteria for this design

The design is ready for implementation planning when the implementation plan preserves:

- the separate-project boundary;
- weekly decision cadence with continuous ingestion;
- recommendation-only V1;
- shortfall avoidance as the hard constraint;
- hybrid deterministic plus probabilistic modelling;
- point-in-time evaluation and baseline promotion gates;
- fail-closed behaviour;
- public synthetic and private real-data separation;
- Monzo-first delivery followed by Budget V2 enrichment;
- user approval before every Phase 2 transfer.
