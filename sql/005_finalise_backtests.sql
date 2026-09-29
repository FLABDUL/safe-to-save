-- Completed replay evidence cannot grow after its summary has been finalised.
-- Migration 004 remains unchanged: existing completed runs are already valid.
ALTER TABLE backtest_runs DROP CONSTRAINT backtest_runs_status_check;
ALTER TABLE backtest_runs ALTER COLUMN completed_at DROP NOT NULL;
ALTER TABLE backtest_runs ALTER COLUMN completed_at DROP DEFAULT;
ALTER TABLE backtest_runs ALTER COLUMN summary DROP NOT NULL;
ALTER TABLE backtest_runs ADD CONSTRAINT backtest_run_lifecycle CHECK (
  (status = 'running' AND completed_at IS NULL AND summary IS NULL)
  OR
  (status = 'completed' AND completed_at IS NOT NULL AND summary IS NOT NULL)
);

CREATE FUNCTION enforce_backtest_lifecycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'running' THEN
      RAISE EXCEPTION 'backtest must be constructed before finalisation';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'running' AND NEW.status = 'completed'
    AND ROW(NEW.run_id, NEW.dataset_id, NEW.feature_version, NEW.baseline_name,
            NEW.baseline_version, NEW.floor_minor, NEW.input_sha256, NEW.started_at)
      IS NOT DISTINCT FROM
        ROW(OLD.run_id, OLD.dataset_id, OLD.feature_version, OLD.baseline_name,
            OLD.baseline_version, OLD.floor_minor, OLD.input_sha256, OLD.started_at)
  THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'backtest evidence is immutable except one-way finalisation';
END;
$$;
DROP TRIGGER immutable_backtest_runs ON backtest_runs;
CREATE TRIGGER immutable_backtest_runs BEFORE INSERT OR UPDATE OR DELETE ON backtest_runs
FOR EACH ROW EXECUTE FUNCTION enforce_backtest_lifecycle();

CREATE FUNCTION enforce_backtest_observation_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  parent_status text;
BEGIN
  -- The row lock serialises observation construction against finalisation.
  SELECT status INTO parent_status FROM backtest_runs WHERE run_id = NEW.run_id FOR UPDATE;
  IF parent_status IS DISTINCT FROM 'running' THEN
    RAISE EXCEPTION 'completed or missing backtest is immutable';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER backtest_observation_construction BEFORE INSERT ON backtest_observations
FOR EACH ROW EXECUTE FUNCTION enforce_backtest_observation_insert();
