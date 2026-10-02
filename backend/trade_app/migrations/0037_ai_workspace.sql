CREATE TABLE ai_config_versions (
    revision INTEGER PRIMARY KEY, config_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE ai_sessions (
    id TEXT PRIMARY KEY, account_id TEXT, title TEXT NOT NULL, revision INTEGER NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE ai_calls (
    id TEXT PRIMARY KEY, session_id TEXT REFERENCES ai_sessions(id), account_id TEXT,
    kind TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL,
    config_revision INTEGER NOT NULL, config_json TEXT NOT NULL,
    request_json TEXT NOT NULL, context_json TEXT NOT NULL, input_sha256 TEXT NOT NULL,
    output TEXT NOT NULL, usage_json TEXT NOT NULL, error_code TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE UNIQUE INDEX ix_ai_one_active_session ON ai_calls(session_id) WHERE status IN ('queued', 'running');
CREATE INDEX ix_ai_call_history ON ai_calls(account_id, created_at);
CREATE TABLE ai_prompt_templates (
    id TEXT PRIMARY KEY, revision INTEGER NOT NULL, name TEXT NOT NULL,
    content TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE ai_prompt_revisions (
    id TEXT PRIMARY KEY, template_id TEXT NOT NULL REFERENCES ai_prompt_templates(id),
    revision INTEGER NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(template_id, revision)
);
