CREATE TABLE sim_order_drafts (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  source_run_id TEXT NOT NULL REFERENCES research_runs(id),
  symbol TEXT NOT NULL,
  signal_date TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  limit_price_units INTEGER NOT NULL CHECK (limit_price_units > 0),
  status TEXT NOT NULL CHECK (status IN ('draft', 'submitted', 'cancelled')),
  order_id TEXT REFERENCES sim_orders(id),
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, source_run_id)
);
CREATE INDEX sim_order_drafts_account_status ON sim_order_drafts(account_id, status, created_at);
