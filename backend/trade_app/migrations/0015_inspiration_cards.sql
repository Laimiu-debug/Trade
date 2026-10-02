CREATE TABLE inspiration_cards (
  id TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE INDEX inspiration_cards_active ON inspiration_cards(deleted_at, created_at);
CREATE TABLE daily_inspiration_selections (
  review_date TEXT PRIMARY KEY,
  card_id TEXT NOT NULL REFERENCES inspiration_cards(id),
  created_at TEXT NOT NULL
);
