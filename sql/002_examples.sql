CREATE TABLE feature_examples (
  dataset_id text NOT NULL REFERENCES history_datasets(dataset_id),
  checkpoint_at timestamptz NOT NULL,
  feature_version text NOT NULL,
  features jsonb,
  target_variable_spend_minor bigint,
  status text NOT NULL CHECK (status IN ('complete', 'withheld')),
  withheld_reason text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (dataset_id, checkpoint_at, feature_version),
  CHECK ((status = 'withheld') = (withheld_reason IS NOT NULL)),
  CHECK (
    (status = 'complete' AND features IS NOT NULL
      AND target_variable_spend_minor IS NOT NULL)
    OR
    (status = 'withheld' AND features IS NULL
      AND target_variable_spend_minor IS NULL)
  )
);
