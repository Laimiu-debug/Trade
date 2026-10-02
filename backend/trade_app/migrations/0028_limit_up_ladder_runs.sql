CREATE TABLE limit_up_ladder_runs (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_limit_up_ladder_runs_created ON limit_up_ladder_runs(created_at);
