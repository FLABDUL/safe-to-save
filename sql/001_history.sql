CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  sha256 text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE history_imports (
  import_id uuid PRIMARY KEY,
  dataset_id text NOT NULL,
  content_sha256 text NOT NULL,
  status text NOT NULL CHECK (status IN ('completed','noop','rejected')),
  records_seen integer NOT NULL CHECK (records_seen >= 0),
  error_details jsonb,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ix_history_imports_content ON history_imports(content_sha256, status);

CREATE TABLE raw_history_records (
  raw_record_id uuid PRIMARY KEY,
  import_id uuid NOT NULL REFERENCES history_imports(import_id),
  source_name text NOT NULL,
  source_line integer NOT NULL CHECK (source_line > 0),
  source_transaction_id text,
  source_payload jsonb NOT NULL,
  source_hash text NOT NULL,
  UNIQUE(import_id, source_name, source_line)
);
CREATE INDEX ix_raw_history_transaction ON raw_history_records(source_transaction_id);
CREATE FUNCTION reject_raw_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'raw history evidence is immutable';
END;
$$;
CREATE TRIGGER raw_history_is_immutable BEFORE UPDATE OR DELETE ON raw_history_records
FOR EACH ROW EXECUTE FUNCTION reject_raw_history_mutation();

CREATE TABLE history_datasets (
  dataset_id text PRIMARY KEY,
  account_id text NOT NULL,
  currency text NOT NULL CHECK (currency = 'GBP'),
  coverage_start timestamptz NOT NULL,
  coverage_end timestamptz NOT NULL,
  CHECK (coverage_end > coverage_start)
);
CREATE TABLE transactions (
  dataset_id text NOT NULL REFERENCES history_datasets(dataset_id),
  transaction_id text NOT NULL,
  raw_record_id uuid NOT NULL REFERENCES raw_history_records(raw_record_id),
  occurred_at timestamptz NOT NULL,
  amount_minor bigint NOT NULL,
  currency text NOT NULL CHECK (currency = 'GBP'),
  status text NOT NULL CHECK (status = 'settled'),
  economic_type text NOT NULL CHECK (economic_type IN
    ('expense','income','internal_transfer','savings_transfer','adjustment')),
  committed boolean NOT NULL DEFAULT false,
  category text,
  canonical_fingerprint text NOT NULL,
  PRIMARY KEY(dataset_id, transaction_id)
);
CREATE TABLE balance_anchors (
  dataset_id text NOT NULL REFERENCES history_datasets(dataset_id),
  captured_at timestamptz NOT NULL,
  balance_minor bigint NOT NULL,
  currency text NOT NULL CHECK (currency = 'GBP'),
  raw_record_id uuid NOT NULL REFERENCES raw_history_records(raw_record_id),
  PRIMARY KEY(dataset_id, captured_at)
);
CREATE TABLE paydays (
  dataset_id text NOT NULL REFERENCES history_datasets(dataset_id),
  payday date NOT NULL,
  known_from date NOT NULL,
  raw_record_id uuid NOT NULL REFERENCES raw_history_records(raw_record_id),
  PRIMARY KEY(dataset_id, payday)
);
CREATE TABLE commitments (
  dataset_id text NOT NULL REFERENCES history_datasets(dataset_id),
  commitment_id text NOT NULL,
  known_from timestamptz NOT NULL,
  due_at timestamptz NOT NULL,
  amount_minor bigint NOT NULL CHECK (amount_minor <= 0),
  description text NOT NULL,
  raw_record_id uuid NOT NULL REFERENCES raw_history_records(raw_record_id),
  PRIMARY KEY(dataset_id, commitment_id),
  CHECK (due_at > known_from)
);
