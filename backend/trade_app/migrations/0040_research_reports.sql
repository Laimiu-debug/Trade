CREATE TABLE research_reports (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, source_run_id TEXT NOT NULL,
 origin TEXT NOT NULL, content_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
 deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX research_reports_content ON research_reports(content_sha256, deleted);
CREATE INDEX research_reports_created ON research_reports(created_at);
