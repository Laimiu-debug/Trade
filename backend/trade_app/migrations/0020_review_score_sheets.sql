CREATE TABLE review_score_sheets (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  review_date TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN ('daily', 'trade', 't_group')),
  subject_id TEXT NOT NULL,
  trade_ids_json TEXT NOT NULL DEFAULT '[]',
  scores_json TEXT NOT NULL DEFAULT '{}',
  comment TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, review_date, scope, subject_id)
);
CREATE INDEX review_score_sheets_account_day ON review_score_sheets(account_id, review_date);
