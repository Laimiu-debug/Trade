CREATE TABLE strategy_scan_jobs (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','running','cancelled','failed','succeeded')),
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL,
    checkpoint_bytes INTEGER NOT NULL DEFAULT 0,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    attempt_id TEXT,
    attempt_number INTEGER NOT NULL DEFAULT 0,
    scan_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_strategy_scan_jobs_queue ON strategy_scan_jobs(state, updated_at);
CREATE TABLE strategy_scan_chunks (
    job_id TEXT NOT NULL REFERENCES strategy_scan_jobs(id),
    start_index INTEGER NOT NULL,
    end_index INTEGER NOT NULL,
    attempt_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (job_id,start_index)
);
