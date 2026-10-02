CREATE TABLE research_runs (
  id TEXT PRIMARY KEY,
  dataset_id TEXT NOT NULL REFERENCES market_datasets(id),
  strategy_id TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  decision_at TEXT NOT NULL,
  strict INTEGER NOT NULL,
  params_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX research_runs_dataset ON research_runs(dataset_id, created_at);
