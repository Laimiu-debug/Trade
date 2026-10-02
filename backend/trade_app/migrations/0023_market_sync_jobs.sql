CREATE TABLE market_sync_jobs (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    state TEXT NOT NULL,
    next_index INTEGER NOT NULL DEFAULT 0,
    results_json TEXT NOT NULL DEFAULT '[]',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_market_sync_jobs_state ON market_sync_jobs(state, created_at);
