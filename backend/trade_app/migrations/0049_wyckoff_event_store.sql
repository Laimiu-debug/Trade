CREATE TABLE event_store_versions (
    id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    profile_revision INTEGER NOT NULL,
    window_days INTEGER NOT NULL,
    strict INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE event_store_records (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES event_store_versions(id),
    dataset_id TEXT NOT NULL REFERENCES market_datasets(id),
    symbol TEXT NOT NULL,
    decision_date TEXT NOT NULL,
    decision_at TEXT NOT NULL,
    source_date TEXT,
    status TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    risk_count INTEGER NOT NULL,
    primary_event TEXT NOT NULL,
    observed_bars INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_event_store_date_symbol ON event_store_records(decision_date, symbol);
CREATE INDEX idx_event_store_version_dataset ON event_store_records(version_id, dataset_id, decision_date);
CREATE TABLE event_store_jobs (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    state TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL,
    total_count INTEGER NOT NULL,
    completed_count INTEGER NOT NULL,
    cache_hits INTEGER NOT NULL,
    written_count INTEGER NOT NULL,
    result_bytes INTEGER NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    attempt_id TEXT,
    attempt_number INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_event_store_job_state ON event_store_jobs(state, updated_at);
