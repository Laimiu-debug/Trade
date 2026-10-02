CREATE TABLE signal_workspace_reports (
    id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_signal_workspace_history ON signal_workspace_reports(created_at);
CREATE TABLE signal_workspace_sources (
    job_id TEXT PRIMARY KEY,
    source_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
