CREATE TABLE sim_review_tags (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  tag_type TEXT NOT NULL CHECK(tag_type IN ('emotion', 'reason')),
  name TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX sim_review_tags_account ON sim_review_tags(account_id, tag_type, active);
CREATE TABLE sim_fill_tag_assignments (
  fill_id TEXT PRIMARY KEY REFERENCES sim_fills(id),
  account_id TEXT NOT NULL REFERENCES accounts(id),
  emotion_tag_id TEXT REFERENCES sim_review_tags(id),
  reason_tag_ids_json TEXT NOT NULL DEFAULT '[]',
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE INDEX sim_fill_tag_assignments_account ON sim_fill_tag_assignments(account_id);
