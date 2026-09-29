CREATE TABLE backtest_runs (
  run_id uuid PRIMARY KEY,
  dataset_id text NOT NULL REFERENCES history_datasets(dataset_id),
  feature_version text NOT NULL,
  baseline_name text NOT NULL,
  baseline_version text NOT NULL,
  floor_minor bigint NOT NULL CHECK (floor_minor >= 0),
  input_sha256 text NOT NULL UNIQUE,
  started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL CHECK (status = 'completed'),
  summary jsonb NOT NULL
);
CREATE TABLE backtest_observations (
  run_id uuid NOT NULL REFERENCES backtest_runs(run_id),
  checkpoint_at timestamptz NOT NULL,
  cycle_payday date,
  status text NOT NULL CHECK (status IN ('scored', 'withheld')),
  withheld_reason text,
  forecast_minor bigint,
  forecast_upper_minor bigint,
  actual_variable_spend_minor bigint,
  recommended_minor bigint CHECK (recommended_minor >= 0),
  minimum_balance_after_saving_minor bigint,
  hindsight_safe_minor bigint CHECK (hindsight_safe_minor >= 0),
  shortfall_violation boolean,
  reversal_required_minor bigint CHECK (reversal_required_minor >= 0),
  PRIMARY KEY (run_id, checkpoint_at),
  CHECK (
    (status = 'scored' AND withheld_reason IS NULL AND cycle_payday IS NOT NULL
      AND forecast_minor IS NOT NULL AND forecast_upper_minor IS NOT NULL
      AND actual_variable_spend_minor IS NOT NULL AND recommended_minor IS NOT NULL
      AND minimum_balance_after_saving_minor IS NOT NULL AND hindsight_safe_minor IS NOT NULL
      AND shortfall_violation IS NOT NULL AND reversal_required_minor IS NOT NULL)
    OR
    (status = 'withheld' AND length(trim(withheld_reason)) > 0 AND withheld_reason IS NOT NULL
      AND forecast_minor IS NULL AND forecast_upper_minor IS NULL
      AND actual_variable_spend_minor IS NULL AND recommended_minor IS NULL
      AND minimum_balance_after_saving_minor IS NULL AND hindsight_safe_minor IS NULL
      AND shortfall_violation IS NULL AND reversal_required_minor IS NULL)
  )
);
CREATE FUNCTION prevent_backtest_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'backtest evidence is immutable';
END;
$$;
CREATE TRIGGER immutable_backtest_runs BEFORE UPDATE OR DELETE ON backtest_runs
FOR EACH ROW EXECUTE FUNCTION prevent_backtest_mutation();
CREATE TRIGGER immutable_backtest_observations BEFORE UPDATE OR DELETE ON backtest_observations
FOR EACH ROW EXECUTE FUNCTION prevent_backtest_mutation();
