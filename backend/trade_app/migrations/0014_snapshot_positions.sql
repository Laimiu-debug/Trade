CREATE TABLE asset_snapshot_positions (
  id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES asset_snapshots(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  market_value_minor INTEGER NOT NULL CHECK (market_value_minor >= 0),
  UNIQUE(snapshot_id, sequence),
  UNIQUE(snapshot_id, symbol)
);
CREATE INDEX snapshot_positions_snapshot ON asset_snapshot_positions(snapshot_id, sequence);
