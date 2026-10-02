CREATE TABLE pending_trades (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  trade_date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  price_units INTEGER NOT NULL CHECK (price_units > 0),
  fee_minor INTEGER NOT NULL CHECK (fee_minor >= 0),
  note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',
  source_text TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'discarded')),
  confirmed_trade_id TEXT REFERENCES trades(id),
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX pending_trades_account_status ON pending_trades(account_id, status, created_at);
