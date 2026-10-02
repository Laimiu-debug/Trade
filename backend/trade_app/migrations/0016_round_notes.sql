CREATE TABLE round_notes (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  round_id TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  trade_ids_json TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, round_id)
);
CREATE INDEX round_notes_account ON round_notes(account_id, updated_at);
