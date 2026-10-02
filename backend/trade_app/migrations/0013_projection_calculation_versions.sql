CREATE TABLE projection_versions_next (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  input_revision INTEGER NOT NULL,
  calculation_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('current', 'historical')),
  created_at TEXT NOT NULL,
  UNIQUE(account_id, input_revision, calculation_version)
);
INSERT INTO projection_versions_next
  SELECT id, account_id, input_revision, calculation_version, payload_json, state, created_at
  FROM projection_versions;
DROP TABLE projection_versions;
ALTER TABLE projection_versions_next RENAME TO projection_versions;
CREATE INDEX projections_current ON projection_versions(account_id, state);
