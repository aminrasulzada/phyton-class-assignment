-- 002_create_quality_tables.sql
-- Data-quality bookkeeping.
--
--   quality_rules      catalog of rules (synced from quality_checks.py on every run),
--                      including the SQL that finds violating rows
--   quality_data_stat  one row per record that currently violates a rule, with the
--                      reason it failed and a JSON snapshot of the offending record
--
-- quality_data_stat always mirrors the *current* state of the data: violations that
-- get fixed are removed on the next run, and re-running without changes is a no-op.

CREATE TABLE IF NOT EXISTS quality_rules (
    rule_id      TEXT PRIMARY KEY,                       -- e.g. DQ001
    name         TEXT NOT NULL,
    dimension    TEXT NOT NULL CHECK (dimension IN
                     ('completeness', 'validity', 'consistency', 'plausibility', 'accuracy')),
    severity     TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
    target_table TEXT NOT NULL,
    description  TEXT NOT NULL,
    check_sql    TEXT NOT NULL                           -- returns the violating rows
);

CREATE TABLE IF NOT EXISTS quality_data_stat (
    rule_id           TEXT    NOT NULL REFERENCES quality_rules (rule_id) ON DELETE CASCADE,
    location_id       INTEGER NOT NULL REFERENCES locations (id)        ON DELETE CASCADE,
    record_key        TEXT    NOT NULL,   -- observed_at, date, or 'location:<id>'
    column_name       TEXT,               -- column(s) that failed; NULL = whole record
    failed_value      TEXT,               -- offending value rendered as text
    reason            TEXT    NOT NULL,   -- why this specific row failed
    row_data          TEXT    NOT NULL CHECK (json_valid(row_data)),  -- snapshot of the record
    first_detected_at TEXT    NOT NULL,   -- set once; kept while the violation persists
    PRIMARY KEY (rule_id, location_id, record_key)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_quality_data_stat_location
    ON quality_data_stat (location_id, record_key);

CREATE VIEW IF NOT EXISTS v_quality_violations AS
SELECT r.rule_id,
       r.name      AS rule_name,
       r.dimension,
       r.severity,
       l.name      AS location,
       q.record_key,
       q.column_name,
       q.failed_value,
       q.reason,
       q.row_data,
       q.first_detected_at
FROM quality_data_stat q
JOIN quality_rules r   ON r.rule_id = q.rule_id
LEFT JOIN locations l  ON l.id = q.location_id;
