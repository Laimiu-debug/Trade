CREATE TABLE accounts (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('real', 'sim')),
  currency TEXT NOT NULL DEFAULT 'CNY',
  input_revision INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE trades (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  trade_date TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  price_units INTEGER NOT NULL CHECK (price_units > 0),
  fee_minor INTEGER NOT NULL DEFAULT 0 CHECK (fee_minor >= 0),
  note TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  voided_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, trade_date, sequence)
);
CREATE INDEX trades_account_date ON trades(account_id, trade_date, sequence);
CREATE TABLE cash_flows (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  flow_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('initial', 'deposit', 'withdraw')),
  amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
  note TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  voided_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX flows_account_date ON cash_flows(account_id, flow_date);
CREATE TABLE asset_snapshots (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  snap_date TEXT NOT NULL,
  total_assets_minor INTEGER NOT NULL CHECK (total_assets_minor >= 0),
  available_cash_minor INTEGER CHECK (available_cash_minor >= 0),
  position_value_minor INTEGER CHECK (position_value_minor >= 0),
  note TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, snap_date)
);
CREATE TABLE daily_reviews (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  review_date TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  market_observation TEXT NOT NULL DEFAULT '',
  decision_review TEXT NOT NULL DEFAULT '',
  mistakes TEXT NOT NULL DEFAULT '',
  tomorrow_plan TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, review_date)
);
CREATE TABLE rebuild_requests (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  earliest_date TEXT NOT NULL,
  target_revision INTEGER NOT NULL,
  change_kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed')),
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX rebuild_pending ON rebuild_requests(state, account_id, target_revision);
CREATE TABLE projection_versions (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  input_revision INTEGER NOT NULL,
  calculation_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('current', 'historical')),
  created_at TEXT NOT NULL,
  UNIQUE(account_id, input_revision)
);
CREATE INDEX projections_current ON projection_versions(account_id, state);
CREATE TABLE audit_events (
  id TEXT PRIMARY KEY,
  account_id TEXT REFERENCES accounts(id),
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE idempotency_keys (
  scope TEXT NOT NULL,
  key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  response_json TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(scope, key)
);
