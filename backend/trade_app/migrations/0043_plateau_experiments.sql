CREATE TABLE plateau_experiments (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, source_run_id TEXT NOT NULL, strategy_id TEXT NOT NULL,
 code_sha256 TEXT NOT NULL, input_sha256 TEXT NOT NULL, input_json TEXT NOT NULL, plan_json TEXT NOT NULL,
 state TEXT NOT NULL, pause_requested INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
 summary_json TEXT, result_sha256 TEXT, error TEXT, total_bytes INTEGER NOT NULL DEFAULT 0,
 elapsed_ms INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_served_at TEXT NOT NULL
);
CREATE INDEX ix_plateau_queue ON plateau_experiments(deleted, state, last_served_at);
CREATE TABLE plateau_points (
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES plateau_experiments(id), ordinal INTEGER NOT NULL,
 point_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL, state TEXT NOT NULL,
 attempt_id TEXT, attempt_number INTEGER NOT NULL DEFAULT 0, result_json TEXT, result_sha256 TEXT,
 metrics_json TEXT, error TEXT, updated_at TEXT NOT NULL,
 UNIQUE(experiment_id, ordinal), UNIQUE(experiment_id, point_sha256)
);
CREATE INDEX ix_plateau_point_queue ON plateau_points(experiment_id, state, ordinal);
