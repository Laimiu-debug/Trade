CREATE TABLE legacy_research_reports (
    id TEXT PRIMARY KEY,
    source_sha256 TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK(mode IN ('legacy_readonly', 'archive_only')),
    content_sha256 TEXT NOT NULL,
    original_bytes BLOB NOT NULL,
    metadata_json TEXT NOT NULL,
    preview_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT
);
CREATE INDEX ix_legacy_research_reports_history ON legacy_research_reports(deleted, created_at DESC);
