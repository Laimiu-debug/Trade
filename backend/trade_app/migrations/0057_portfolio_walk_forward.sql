CREATE TABLE portfolio_walk_forwards (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, source_run_id TEXT NOT NULL,
 input_json TEXT NOT NULL, input_sha256 TEXT NOT NULL, code_sha256 TEXT NOT NULL,
 state TEXT NOT NULL, attempt_id TEXT, result_json TEXT, result_sha256 TEXT,
 completed_tasks INTEGER NOT NULL DEFAULT 0, total_tasks INTEGER NOT NULL,
 active_fold INTEGER NOT NULL DEFAULT 0, elapsed_ms INTEGER NOT NULL DEFAULT 0,
 total_bytes INTEGER NOT NULL DEFAULT 0, pause_requested INTEGER NOT NULL DEFAULT 0,
 cancel_requested INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_served_at TEXT NOT NULL
);
CREATE INDEX ix_portfolio_wf_queue ON portfolio_walk_forwards(deleted, state, last_served_at);
CREATE TABLE portfolio_walk_forward_folds (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES portfolio_walk_forwards(id),
 ordinal INTEGER NOT NULL, selection_json TEXT, selection_sha256 TEXT,
 UNIQUE(run_id, ordinal)
);
CREATE TABLE portfolio_walk_forward_tasks (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES portfolio_walk_forwards(id),
 fold_index INTEGER NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
 axis_json TEXT, candidate_sha256 TEXT, child_run_id TEXT REFERENCES portfolio_runs(id),
 outcome_json TEXT, outcome_sha256 TEXT, UNIQUE(run_id, ordinal)
);
