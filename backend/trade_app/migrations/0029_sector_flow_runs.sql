CREATE TABLE sector_flow_runs (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_sector_flow_runs_created ON sector_flow_runs(created_at);
