CREATE TABLE walk_forward_jobs (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, source_run_id TEXT NOT NULL, strategy_id TEXT NOT NULL,
 input_json TEXT NOT NULL, input_sha256 TEXT NOT NULL, plan_json TEXT NOT NULL, code_sha256 TEXT NOT NULL,
 state TEXT NOT NULL, attempt_id TEXT, pause_requested INTEGER NOT NULL DEFAULT 0,
 cancel_requested INTEGER NOT NULL DEFAULT 0, summary_json TEXT, result_sha256 TEXT, error TEXT,
 total_bytes INTEGER NOT NULL DEFAULT 0, elapsed_ms INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_served_at TEXT NOT NULL
);
CREATE INDEX ix_walk_forward_queue ON walk_forward_jobs(deleted, state, last_served_at);
CREATE TABLE walk_forward_folds (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES walk_forward_jobs(id), fold_index INTEGER NOT NULL,
 range_json TEXT NOT NULL, state TEXT NOT NULL, selection_json TEXT, selection_sha256 TEXT,
 updated_at TEXT NOT NULL, UNIQUE(job_id, fold_index)
);
CREATE TABLE walk_forward_tasks (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES walk_forward_jobs(id), fold_index INTEGER NOT NULL,
 role TEXT NOT NULL, candidate_sha256 TEXT, ordinal INTEGER NOT NULL, payload_json TEXT, payload_sha256 TEXT,
 state TEXT NOT NULL, attempt_id TEXT, attempt_number INTEGER NOT NULL DEFAULT 0,
 result_json TEXT, result_sha256 TEXT, metrics_json TEXT, error TEXT, updated_at TEXT NOT NULL,
 UNIQUE(job_id, fold_index, role, ordinal)
);
CREATE INDEX ix_walk_forward_task_queue ON walk_forward_tasks(job_id, fold_index, role, state);
