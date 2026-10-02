CREATE TABLE period_reviews (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  kind TEXT NOT NULL CHECK (kind IN ('weekly', 'monthly')),
  period_key TEXT NOT NULL,
  sections_json TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, kind, period_key)
);
CREATE INDEX period_reviews_account ON period_reviews(account_id, kind, period_key);
