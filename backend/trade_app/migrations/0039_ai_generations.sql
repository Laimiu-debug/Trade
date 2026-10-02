CREATE TABLE ai_generations (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES ai_calls(id), account_id TEXT,
    kind TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL,
    source_json TEXT NOT NULL, output_json TEXT, errors_json TEXT NOT NULL,
    acceptance_json TEXT, input_sha256 TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX ix_ai_generation_scope ON ai_generations(account_id, created_at);
CREATE TABLE ai_generation_audit (
    id TEXT PRIMARY KEY, generation_id TEXT NOT NULL REFERENCES ai_generations(id),
    revision INTEGER NOT NULL, action TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(generation_id, revision)
);
