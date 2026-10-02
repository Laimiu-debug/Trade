CREATE TABLE local_trading_calendars (
    market TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    days_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
