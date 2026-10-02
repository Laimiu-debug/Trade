CREATE TABLE strategy_scan_runs (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_strategy_scan_created_at ON strategy_scan_runs(created_at);
