ALTER TABLE portfolio_runs ADD COLUMN owner_kind TEXT;
ALTER TABLE portfolio_runs ADD COLUMN owner_id TEXT;
CREATE INDEX ix_portfolio_owner ON portfolio_runs(owner_kind, owner_id);
CREATE TABLE portfolio_experiments (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, source_run_id TEXT NOT NULL,
 input_json TEXT NOT NULL, input_sha256 TEXT NOT NULL, code_sha256 TEXT NOT NULL,
 state TEXT NOT NULL, attempt_id TEXT, result_json TEXT, result_sha256 TEXT,
 completed_points INTEGER NOT NULL DEFAULT 0, total_points INTEGER NOT NULL,
 active_point INTEGER NOT NULL DEFAULT 0, elapsed_ms INTEGER NOT NULL DEFAULT 0,
 total_bytes INTEGER NOT NULL DEFAULT 0, pause_requested INTEGER NOT NULL DEFAULT 0,
 cancel_requested INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_served_at TEXT NOT NULL
);
CREATE INDEX ix_portfolio_experiment_queue ON portfolio_experiments(deleted, state, last_served_at);
CREATE TABLE portfolio_experiment_points (
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES portfolio_experiments(id),
 ordinal INTEGER NOT NULL, axis_json TEXT NOT NULL, point_sha256 TEXT NOT NULL,
 child_run_id TEXT REFERENCES portfolio_runs(id), metrics_json TEXT, metrics_sha256 TEXT,
 created_at TEXT NOT NULL, UNIQUE(experiment_id, ordinal)
);
