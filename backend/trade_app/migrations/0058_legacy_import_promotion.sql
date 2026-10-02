ALTER TABLE legacy_imports ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE legacy_imports ADD COLUMN promotion_json TEXT;
