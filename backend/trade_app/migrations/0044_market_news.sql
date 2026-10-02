CREATE TABLE market_news_snapshots (
    id TEXT PRIMARY KEY,
    cache_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_market_news_cache ON market_news_snapshots(cache_key, created_at DESC);
