CREATE TABLE sim_equity_reports (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_sim_equity_reports_account ON sim_equity_reports(account_id, created_at);
CREATE TABLE sim_equity_draft_evidence (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    draft_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
