CREATE TABLE stock_annotations (
    symbol TEXT PRIMARY KEY,
    start_date TEXT NOT NULL,
    stage TEXT NOT NULL,
    trend_class TEXT NOT NULL,
    decision TEXT NOT NULL,
    notes TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
