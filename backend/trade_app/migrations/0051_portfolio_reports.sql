CREATE TABLE portfolio_reports (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, source_run_id TEXT NOT NULL, origin TEXT NOT NULL,
 content_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL, metadata_json TEXT NOT NULL,
 deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX ix_portfolio_report_history ON portfolio_reports(deleted, created_at);
CREATE INDEX ix_portfolio_report_digest ON portfolio_reports(content_sha256, deleted);
