CREATE TABLE strategy_presets (
 id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, revision INTEGER NOT NULL,
 snapshot_json TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0,
 deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX strategy_presets_strategy ON strategy_presets(strategy_id, deleted, favorite);
CREATE TABLE strategy_preset_revisions (
 id TEXT PRIMARY KEY, preset_id TEXT NOT NULL REFERENCES strategy_presets(id),
 revision INTEGER NOT NULL, action TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(preset_id, revision)
);
CREATE TABLE strategy_parameter_proposals (
 id TEXT PRIMARY KEY, preset_id TEXT NOT NULL REFERENCES strategy_presets(id),
 base_revision INTEGER NOT NULL, proposal_sha256 TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 status TEXT NOT NULL, applied_revision INTEGER, created_at TEXT NOT NULL, applied_at TEXT
);
