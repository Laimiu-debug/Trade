CREATE TABLE target_configs (
  account_id TEXT NOT NULL REFERENCES accounts(id),
  version INTEGER NOT NULL,
  multiplier TEXT NOT NULL,
  node_count INTEGER NOT NULL CHECK (node_count BETWEEN 1 AND 100),
  created_at TEXT NOT NULL,
  PRIMARY KEY (account_id, version)
);
