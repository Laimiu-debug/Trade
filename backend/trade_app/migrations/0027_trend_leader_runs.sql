CREATE TABLE trend_leader_runs (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_trend_leader_runs_created ON trend_leader_runs(created_at);
