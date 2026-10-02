CREATE TABLE application_settings (
    group_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    revision INTEGER NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
