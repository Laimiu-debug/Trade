CREATE TABLE portfolio_analysis_runs (
    id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL, source_result_sha256 TEXT NOT NULL,
    name TEXT NOT NULL, version TEXT NOT NULL, code_sha256 TEXT NOT NULL,
    input_sha256 TEXT NOT NULL, input_json TEXT NOT NULL, options_json TEXT NOT NULL,
    state TEXT NOT NULL, attempt_id TEXT, result_sha256 TEXT, result_json TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}', cancel_requested INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX portfolio_analysis_state_idx ON portfolio_analysis_runs(state, deleted, created_at);
CREATE INDEX portfolio_analysis_source_idx ON portfolio_analysis_runs(source_run_id, created_at);
