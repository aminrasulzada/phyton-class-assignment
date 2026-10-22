# Open-Meteo weather pipeline

Loads hourly weather history from the Open-Meteo archive API into SQLite, cleans it,
and checks its quality. Standard library only (Python 3.10+).

```bash
python pipeline.py --start-date 2024-01-01 --end-date 2024-01-31 --location "Berlin:52.52:13.41"
python pipeline.py --checks-only                 # quality checks on the existing database
python pipeline.py --fail-on-error               # exit code 2 if an error rule fails (for CI)
python -m unittest discover -s tests -v
```

## Steps

1. **Extract**: `GET archive-api.open-meteo.com/v1/archive` (hourly, UTC, fixed units).
2. **Transform**: validate the payload, drop bad and duplicate rows, NULL impossible values, derive fields.
3. **Load**: upsert `locations`, `hourly_weather`, rebuild `daily_weather` (migration `001`).
4. **Quality**: run the rules in `quality_checks.py`, sync `quality_data_stat` (migration `002`),
   write `data_quality_report.md`.

Running the pipeline twice with the same arguments leaves the database and the report unchanged.

## Data quality

The 17 rules (completeness, validity, consistency, plausibility, accuracy) are listed with
their SQL in [`docs/data_quality_rules.md`](docs/data_quality_rules.md). Each rule is a query
that returns the violating rows; they are stored in `quality_data_stat` with the reason and
a JSON snapshot of the record. Fixed violations disappear on the next run.

```sql
SELECT rule_id, severity, location, record_key, reason
FROM v_quality_violations
ORDER BY severity, rule_id, record_key;
```

To add a rule, append a `Rule` to `RULES` in `quality_checks.py` whose SQL returns
`location_id, record_key, column_name, failed_value, reason, row_data`, then regenerate the
catalog with `python quality_checks.py > docs/data_quality_rules.md`.
