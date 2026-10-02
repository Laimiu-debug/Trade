CREATE TABLE research_event_profiles (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE research_event_profile_selection (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    profile_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE research_event_profile_audit (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    action TEXT NOT NULL,
    revision INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_event_profile_audit ON research_event_profile_audit(profile_id, created_at);
