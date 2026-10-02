CREATE TABLE legacy_imports (
  id TEXT PRIMARY KEY,
  source_sha256 TEXT NOT NULL UNIQUE,
  source_kind TEXT NOT NULL,
  filename TEXT NOT NULL,
  source_bytes INTEGER NOT NULL,
  logical_sha256 TEXT NOT NULL,
  archive_json TEXT NOT NULL,
  preview_json TEXT NOT NULL,
  mappings_json TEXT NOT NULL,
  account_id TEXT REFERENCES accounts(id),
  created_at TEXT NOT NULL
);
