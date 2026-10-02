CREATE TABLE intraday_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    point_count INTEGER NOT NULL CHECK(point_count BETWEEN 1 AND 500),
    payload_json TEXT NOT NULL
);
CREATE INDEX ix_intraday_snapshots_symbol_date ON intraday_snapshots(symbol, date, as_of_at DESC);
