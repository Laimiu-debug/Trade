CREATE TABLE research_watch_pool (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE research_watch_pool_audit (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_watch_pool_audit_revision ON research_watch_pool_audit(revision);
