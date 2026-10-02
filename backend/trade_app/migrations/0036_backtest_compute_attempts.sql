ALTER TABLE backtest_runs ADD COLUMN attempt_id TEXT;
ALTER TABLE backtest_runs ADD COLUMN attempt_number INTEGER NOT NULL DEFAULT 0;
ALTER TABLE backtest_runs ADD COLUMN result_sha256 TEXT;
