CREATE TABLE legacy_supplement_batches (
 id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES legacy_imports(id),
 preview_sha256 TEXT NOT NULL, request_json TEXT NOT NULL, preview_json TEXT NOT NULL,
 result_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(import_id, preview_sha256)
);
CREATE TABLE legacy_supplement_items (
 import_id TEXT NOT NULL REFERENCES legacy_imports(id), source_key TEXT NOT NULL,
 batch_id TEXT NOT NULL REFERENCES legacy_supplement_batches(id),
 target_json TEXT NOT NULL, PRIMARY KEY(import_id, source_key)
);
