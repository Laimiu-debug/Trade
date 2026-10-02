ALTER TABLE sim_orders ADD COLUMN legacy_origin_json TEXT;
CREATE TABLE legacy_sim_promotions (
    import_id TEXT PRIMARY KEY REFERENCES legacy_imports(id),
    account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id),
    preview_sha256 TEXT NOT NULL,
    preview_json TEXT NOT NULL,
    mappings_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
