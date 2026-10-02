CREATE TABLE signal_baskets (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE signal_basket_audit (
    id TEXT PRIMARY KEY,
    basket_id TEXT NOT NULL REFERENCES signal_baskets(id),
    revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE signal_basket_evaluations (
    id TEXT PRIMARY KEY,
    basket_id TEXT NOT NULL REFERENCES signal_baskets(id),
    basket_revision INTEGER NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_signal_basket_evaluation ON signal_basket_evaluations(basket_id, created_at);
CREATE INDEX ix_signal_basket_audit ON signal_basket_audit(basket_id, revision);
