CREATE TABLE abnormal_runs (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_abnormal_runs_created ON abnormal_runs(created_at);
