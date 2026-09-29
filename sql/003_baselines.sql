CREATE TABLE baseline_predictions (
  dataset_id text NOT NULL,
  checkpoint_at timestamptz NOT NULL,
  feature_version text NOT NULL,
  baseline_name text NOT NULL,
  baseline_version text NOT NULL,
  point_minor bigint,
  upper_minor bigint,
  sample_size integer,
  status text NOT NULL CHECK (status IN ('available', 'withheld')),
  withheld_reason text,
  provenance jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (dataset_id, checkpoint_at, feature_version, baseline_name, baseline_version),
  FOREIGN KEY (dataset_id, checkpoint_at, feature_version)
    REFERENCES feature_examples(dataset_id, checkpoint_at, feature_version),
  CHECK (
    (status = 'available' AND point_minor IS NOT NULL AND upper_minor IS NOT NULL
      AND upper_minor >= point_minor AND sample_size >= 3 AND sample_size IS NOT NULL
      AND withheld_reason IS NULL)
    OR
    (status = 'withheld' AND point_minor IS NULL AND upper_minor IS NULL
      AND sample_size IS NULL AND withheld_reason IS NOT NULL
      AND length(trim(withheld_reason)) > 0)
  ),
  CHECK (jsonb_typeof(provenance) = 'object')
);
