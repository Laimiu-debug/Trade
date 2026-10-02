CREATE TABLE portfolio_runs (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL, strategy_id TEXT NOT NULL,
 input_json TEXT NOT NULL, input_sha256 TEXT NOT NULL, code_sha256 TEXT NOT NULL,
 state TEXT NOT NULL, attempt_id TEXT, checkpoint_json TEXT NOT NULL, checkpoint_sha256 TEXT NOT NULL,
 summary_json TEXT NOT NULL, result_sha256 TEXT, completed_days INTEGER NOT NULL DEFAULT 0,
 total_days INTEGER NOT NULL, symbol_count INTEGER NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,
 elapsed_ms INTEGER NOT NULL DEFAULT 0, total_bytes INTEGER NOT NULL DEFAULT 0,
 pause_requested INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
 deleted INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 last_served_at TEXT NOT NULL
);
CREATE INDEX ix_portfolio_queue ON portfolio_runs(deleted, state, last_served_at);
CREATE TABLE portfolio_chunks (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES portfolio_runs(id), ordinal INTEGER NOT NULL,
 start_cursor INTEGER NOT NULL, end_cursor INTEGER NOT NULL, prior_sha256 TEXT NOT NULL,
 result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(run_id, ordinal)
);
