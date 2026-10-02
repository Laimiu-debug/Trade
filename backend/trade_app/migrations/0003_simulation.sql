CREATE TABLE sim_wallets (
  account_id TEXT PRIMARY KEY REFERENCES accounts(id),
  initial_minor INTEGER NOT NULL CHECK (initial_minor > 0),
  cash_minor INTEGER NOT NULL CHECK (cash_minor >= 0),
  as_of_date TEXT NOT NULL,
  config_json TEXT NOT NULL,
  config_version INTEGER NOT NULL DEFAULT 1,
  revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE sim_orders (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  limit_price_units INTEGER NOT NULL CHECK (limit_price_units > 0),
  signal_date TEXT NOT NULL,
  submit_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'filled', 'cancelled')),
  reserve_minor INTEGER NOT NULL DEFAULT 0,
  config_json TEXT NOT NULL,
  config_version INTEGER NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX sim_orders_account_state ON sim_orders(account_id, status, submit_date);
CREATE TABLE sim_fills (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL UNIQUE REFERENCES sim_orders(id),
  account_id TEXT NOT NULL REFERENCES accounts(id),
  fill_date TEXT NOT NULL,
  price_units INTEGER NOT NULL,
  gross_minor INTEGER NOT NULL,
  commission_minor INTEGER NOT NULL,
  stamp_minor INTEGER NOT NULL,
  transfer_minor INTEGER NOT NULL,
  realized_pnl_minor INTEGER,
  price_source TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX sim_fills_account_date ON sim_fills(account_id, fill_date);
CREATE TABLE sim_lots (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  acquired_date TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  remaining_qty INTEGER NOT NULL CHECK (remaining_qty >= 0),
  cost_minor INTEGER NOT NULL CHECK (cost_minor >= 0),
  created_at TEXT NOT NULL
);
CREATE INDEX sim_lots_account_symbol ON sim_lots(account_id, symbol, acquired_date);
