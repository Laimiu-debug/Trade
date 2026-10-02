CREATE TABLE market_datasets (
  id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  provider TEXT NOT NULL,
  adjustment TEXT NOT NULL,
  first_date TEXT NOT NULL,
  last_date TEXT NOT NULL,
  bar_count INTEGER NOT NULL,
  availability_quality TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX market_datasets_symbol ON market_datasets(symbol, last_date);
