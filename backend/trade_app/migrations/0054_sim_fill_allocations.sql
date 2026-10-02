ALTER TABLE sim_fills ADD COLUMN allocations_json TEXT;
ALTER TABLE sim_lots ADD COLUMN buy_fill_id TEXT REFERENCES sim_fills(id);
CREATE INDEX sim_lots_buy_fill ON sim_lots(buy_fill_id);
