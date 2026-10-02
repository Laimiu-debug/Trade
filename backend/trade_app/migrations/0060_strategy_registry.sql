CREATE TABLE strategy_registry_settings (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    settings_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE strategy_registry_revisions (
    revision INTEGER PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
