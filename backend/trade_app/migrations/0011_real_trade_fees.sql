CREATE TABLE real_fee_configs (
  account_id TEXT PRIMARY KEY REFERENCES accounts(id),
  config_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
ALTER TABLE trades ADD COLUMN calculated_fee_minor INTEGER NOT NULL DEFAULT 0;
ALTER TABLE trades ADD COLUMN fee_source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE trades ADD COLUMN fee_rule_version INTEGER;
ALTER TABLE trades ADD COLUMN fee_breakdown_json TEXT;
ALTER TABLE pending_trades ADD COLUMN calculated_fee_minor INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pending_trades ADD COLUMN fee_source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE pending_trades ADD COLUMN fee_rule_version INTEGER;
ALTER TABLE pending_trades ADD COLUMN fee_breakdown_json TEXT;
