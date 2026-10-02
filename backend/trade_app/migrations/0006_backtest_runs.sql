CREATE TABLE backtest_runs (
  id TEXT PRIMARY KEY,
  dataset_id TEXT NOT NULL REFERENCES market_datasets(id),
  strategy_id TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  execution_version TEXT NOT NULL,
  calculation_version TEXT NOT NULL,
  params_json TEXT NOT NULL,
  config_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  result_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX backtest_runs_state_created ON backtest_runs(state, created_at);
