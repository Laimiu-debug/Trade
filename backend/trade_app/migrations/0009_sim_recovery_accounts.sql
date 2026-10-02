ALTER TABLE sim_wallets ADD COLUMN frozen INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sim_wallets ADD COLUMN reset_group_id TEXT;
CREATE INDEX sim_wallets_reset_group ON sim_wallets(reset_group_id, frozen);
