CREATE TABLE ai_quick_prompt_lists (
  id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL,
  items_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
