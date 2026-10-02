CREATE TABLE strategy_pool_runs (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL REFERENCES screener_runs(id),
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_strategy_pool_runs_created ON strategy_pool_runs(created_at DESC);
