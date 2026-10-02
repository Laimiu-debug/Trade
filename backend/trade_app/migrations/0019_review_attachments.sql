CREATE TABLE review_attachments (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  review_date TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  original_name TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  deleted_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX review_attachments_account_day ON review_attachments(account_id, review_date, created_at);
