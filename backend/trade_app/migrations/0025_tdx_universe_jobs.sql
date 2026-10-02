CREATE TABLE tdx_universe_jobs (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    request_json TEXT NOT NULL,
    total_count INTEGER NOT NULL,
    processed_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    run_id TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_tdx_universe_jobs_state ON tdx_universe_jobs(state, created_at);
CREATE TABLE tdx_universe_items (
    job_id TEXT NOT NULL REFERENCES tdx_universe_jobs(id),
    ordinal INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    float_shares REAL,
    state TEXT NOT NULL,
    dataset_id TEXT,
    candidate_json TEXT,
    error_code TEXT,
    PRIMARY KEY (job_id, ordinal),
    UNIQUE (job_id, symbol)
);
CREATE INDEX ix_tdx_universe_items_state ON tdx_universe_items(job_id, state, ordinal);
