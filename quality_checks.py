"""
Data-quality rules for the Open-Meteo weather database.

Every rule is a single SQL query that returns the rows that VIOLATE it, always
with the same six columns so they can be stored uniformly:

    location_id   location the failing record belongs to
    record_key    which record failed (observed_at, a date, or 'location:<id>')
    column_name   column(s) that failed, NULL when the whole record failed
    failed_value  the offending value, as text
    reason        human-readable explanation for this specific row
    row_data      JSON snapshot of the failing record

run_quality_checks() executes all rules and syncs quality_data_stat so that it
holds exactly the violations that exist right now: new ones are inserted, fixed
ones are deleted, unchanged ones are left untouched (first_detected_at survives).
Running it twice on the same data therefore changes nothing.

Run `python quality_checks.py > docs/data_quality_rules.md` to regenerate the
rule catalog after editing rules.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

RESULT_COLUMNS = ("location_id", "record_key", "column_name", "failed_value", "reason", "row_data")
SEVERITIES = ("error", "warning")
DIMENSIONS = ("completeness", "validity", "consistency", "plausibility", "accuracy")
TABLES = ("hourly_weather", "daily_weather", "locations")

# Thresholds
MAX_TEMP_JUMP_C = 10          # hour-to-hour temperature change considered implausible
FLATLINE_HOURS = 6            # identical temperature this many hours in a row = stuck values
MAX_GRID_OFFSET_DEG = 0.25    # model grid cell further than this from the requested point
WET_WITH_DRY_CODE_MM = 0.5    # precipitation this large under a "no precipitation" weather code


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    dimension: str
    severity: str
    target_table: str
    description: str
    sql: str

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"{self.rule_id}: severity must be one of {SEVERITIES}")
        if self.dimension not in DIMENSIONS:
            raise ValueError(f"{self.rule_id}: dimension must be one of {DIMENSIONS}")
        if self.target_table not in TABLES:
            raise ValueError(f"{self.rule_id}: target_table must be one of {TABLES}")


# JSON snapshots of the record a violation points at (alias h / d / l).
HOURLY_JSON = """json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code)"""

DAILY_JSON = """json_object(
           'date', d.date, 'temp_min_c', d.temp_min_c, 'temp_max_c', d.temp_max_c,
           'temp_mean_c', d.temp_mean_c, 'precipitation_total_mm', d.precipitation_total_mm,
           'wind_speed_max_kmh', d.wind_speed_max_kmh, 'hours_observed', d.hours_observed,
           'is_complete', d.is_complete)"""

LOCATION_JSON = """json_object(
           'id', l.id, 'name', l.name, 'latitude', l.latitude, 'longitude', l.longitude,
           'grid_latitude', l.grid_latitude, 'grid_longitude', l.grid_longitude,
           'elevation_m', l.elevation_m)"""


def _not_null(rule_id: str, column: str, severity: str, why: str) -> Rule:
    return Rule(
        rule_id=rule_id,
        name=f"{column} is not null",
        dimension="completeness",
        severity=severity,
        target_table="hourly_weather",
        description=f"Every hourly observation must have {column}. {why}",
        sql=f"""
SELECT h.location_id,
       h.observed_at  AS record_key,
       '{column}'     AS column_name,
       NULL           AS failed_value,
       '{column} is NULL (missing in the API response or rejected as invalid during cleaning)'
                      AS reason,
       {HOURLY_JSON} AS row_data
FROM hourly_weather h
WHERE h.{column} IS NULL""",
    )


RULES: list[Rule] = [
    # ---------------------------------------------------------------- completeness
    _not_null("DQ001", "temperature_c", "error", "Temperature is the core measure of this dataset."),
    _not_null("DQ002", "precipitation_mm", "error", "Needed for daily precipitation totals."),
    _not_null("DQ003", "wind_speed_kmh", "error", "Needed for daily maximum wind."),
    _not_null("DQ004", "relative_humidity_pct", "warning", "Useful but not used in aggregates."),
    _not_null("DQ005", "wind_direction_deg", "warning", "Useful but not used in aggregates."),
    _not_null("DQ006", "weather_code", "warning", "NULL also results from unknown WMO codes."),

    # ---------------------------------------------------------------- validity / consistency
    Rule(
        "DQ007", "timestamp is well-formed and consistent", "validity", "error", "hourly_weather",
        "observed_at must be an on-the-hour ISO-8601 UTC timestamp, and observed_date / "
        "observed_hour must equal its date and hour parts.",
        f"""
SELECT h.location_id,
       h.observed_at AS record_key,
       'observed_at' AS column_name,
       h.observed_at || ' / ' || h.observed_date || ' / ' || h.observed_hour AS failed_value,
       CASE
         WHEN h.observed_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:00:00Z'
              OR julianday(h.observed_at) IS NULL
           THEN 'observed_at is not an on-the-hour ISO-8601 UTC timestamp'
         WHEN h.observed_date <> substr(h.observed_at, 1, 10)
           THEN 'observed_date does not match the date part of observed_at'
         ELSE 'observed_hour does not match the hour part of observed_at'
       END AS reason,
       {HOURLY_JSON} AS row_data
FROM hourly_weather h
WHERE h.observed_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:00:00Z'
   OR julianday(h.observed_at) IS NULL
   OR h.observed_date <> substr(h.observed_at, 1, 10)
   OR h.observed_hour <> CAST(substr(h.observed_at, 12, 2) AS INTEGER)""",
    ),
    Rule(
        "DQ008", "wind cardinal matches wind degrees", "consistency", "error", "hourly_weather",
        "wind_direction_cardinal must be the 16-point compass sector of wind_direction_deg "
        "(both NULL together).",
        f"""
WITH compass (idx, name) AS (
    VALUES (0, 'N'), (1, 'NNE'), (2, 'NE'), (3, 'ENE'), (4, 'E'), (5, 'ESE'), (6, 'SE'), (7, 'SSE'),
           (8, 'S'), (9, 'SSW'), (10, 'SW'), (11, 'WSW'), (12, 'W'), (13, 'WNW'), (14, 'NW'), (15, 'NNW')
)
SELECT h.location_id,
       h.observed_at AS record_key,
       'wind_direction_cardinal' AS column_name,
       h.wind_direction_cardinal AS failed_value,
       'cardinal ' || COALESCE(h.wind_direction_cardinal, 'NULL') || ' does not match '
         || COALESCE(h.wind_direction_deg || ' deg', 'NULL degrees')
         || ' (expected ' || COALESCE(c.name, 'NULL') || ')' AS reason,
       {HOURLY_JSON} AS row_data
FROM hourly_weather h
LEFT JOIN compass c ON c.idx = ((h.wind_direction_deg * 100 + 1125) / 2250) % 16
WHERE h.wind_direction_cardinal IS NOT c.name""",
    ),

    # ---------------------------------------------------------------- completeness over time
    Rule(
        "DQ009", "no gaps in the hourly series", "completeness", "warning", "hourly_weather",
        "Consecutive observations of a location must be exactly one hour apart. "
        "One violation per gap, keyed by the last hour before the gap.",
        """
WITH ordered AS (
    SELECT location_id,
           observed_at,
           LEAD(observed_at) OVER (PARTITION BY location_id ORDER BY observed_at) AS next_at
    FROM hourly_weather
), gaps AS (
    SELECT location_id, observed_at, next_at,
           CAST(ROUND((julianday(next_at) - julianday(observed_at)) * 24) AS INTEGER) - 1 AS missing_hours
    FROM ordered
    WHERE next_at IS NOT NULL
)
SELECT location_id,
       observed_at AS record_key,
       'observed_at' AS column_name,
       next_at AS failed_value,
       missing_hours || ' missing hour(s) between ' || observed_at || ' and ' || next_at AS reason,
       json_object('gap_after', observed_at, 'next_observation', next_at,
                   'missing_hours', missing_hours) AS row_data
FROM gaps
WHERE missing_hours > 0""",
    ),
    Rule(
        "DQ010", "every day has 24 hourly observations", "completeness", "warning", "daily_weather",
        "A daily aggregate built from fewer than 24 hours under-reports totals and extremes.",
        f"""
SELECT d.location_id,
       d.date AS record_key,
       'hours_observed' AS column_name,
       CAST(d.hours_observed AS TEXT) AS failed_value,
       'only ' || d.hours_observed || ' of 24 hourly observations for ' || d.date AS reason,
       {DAILY_JSON} AS row_data
FROM daily_weather d
WHERE d.hours_observed < 24""",
    ),

    # ---------------------------------------------------------------- plausibility
    Rule(
        "DQ011", "no implausible temperature jumps", "plausibility", "warning", "hourly_weather",
        f"Temperature must not change by more than {MAX_TEMP_JUMP_C} degC between two "
        "consecutive hours.",
        f"""
WITH h AS (
    SELECT hw.*,
           LAG(hw.temperature_c) OVER w AS prev_temp,
           LAG(hw.observed_at)   OVER w AS prev_at
    FROM hourly_weather hw
    WINDOW w AS (PARTITION BY hw.location_id ORDER BY hw.observed_at)
)
SELECT h.location_id,
       h.observed_at AS record_key,
       'temperature_c' AS column_name,
       CAST(h.temperature_c AS TEXT) AS failed_value,
       'temperature changed by ' || printf('%.1f', h.temperature_c - h.prev_temp)
         || ' degC in one hour (was ' || h.prev_temp || ' at ' || h.prev_at || ')' AS reason,
       {HOURLY_JSON} AS row_data
FROM h
WHERE h.temperature_c IS NOT NULL
  AND h.prev_temp IS NOT NULL
  AND ABS(h.temperature_c - h.prev_temp) > {MAX_TEMP_JUMP_C}
  AND (julianday(h.observed_at) - julianday(h.prev_at)) * 24 < 1.5""",
    ),
    Rule(
        "DQ012", "no flat-lined temperature", "plausibility", "warning", "hourly_weather",
        f"The same temperature for {FLATLINE_HOURS} or more consecutive hours suggests stuck "
        "or forward-filled values. Flags each hour that completes such a run.",
        f"""
WITH h AS (
    SELECT hw.*,
           LAG(hw.temperature_c, 1) OVER w AS t1,
           LAG(hw.temperature_c, 2) OVER w AS t2,
           LAG(hw.temperature_c, 3) OVER w AS t3,
           LAG(hw.temperature_c, 4) OVER w AS t4,
           LAG(hw.temperature_c, 5) OVER w AS t5,
           LAG(hw.observed_at, 5)   OVER w AS run_start
    FROM hourly_weather hw
    WINDOW w AS (PARTITION BY hw.location_id ORDER BY hw.observed_at)
)
SELECT h.location_id,
       h.observed_at AS record_key,
       'temperature_c' AS column_name,
       CAST(h.temperature_c AS TEXT) AS failed_value,
       'temperature stuck at ' || h.temperature_c || ' degC for {FLATLINE_HOURS} consecutive hours since '
         || h.run_start AS reason,
       {HOURLY_JSON} AS row_data
FROM h
WHERE h.temperature_c = h.t1 AND h.t1 = h.t2 AND h.t2 = h.t3 AND h.t3 = h.t4 AND h.t4 = h.t5
  AND (julianday(h.observed_at) - julianday(h.run_start)) * 24 < {FLATLINE_HOURS} - 0.5""",
    ),
    Rule(
        "DQ013", "precipitation agrees with weather code", "consistency", "warning", "hourly_weather",
        f"{WET_WITH_DRY_CODE_MM} mm or more of precipitation contradicts a clear/cloudy weather "
        "code (0-3).",
        f"""
SELECT h.location_id,
       h.observed_at AS record_key,
       'precipitation_mm, weather_code' AS column_name,
       h.precipitation_mm || ' mm / code ' || h.weather_code AS failed_value,
       h.precipitation_mm || ' mm of precipitation but weather code ' || h.weather_code
         || ' (' || wc.description || ') means no precipitation' AS reason,
       {HOURLY_JSON} AS row_data
FROM hourly_weather h
JOIN weather_codes wc ON wc.code = h.weather_code
WHERE h.precipitation_mm >= {WET_WITH_DRY_CODE_MM}
  AND h.weather_code IN (0, 1, 2, 3)""",
    ),

    # ---------------------------------------------------------------- daily table
    Rule(
        "DQ014", "daily aggregates reconcile with hourly data", "consistency", "error", "daily_weather",
        "Each daily_weather row must equal the aggregates recomputed from hourly_weather, and "
        "every day with hourly data must have a daily row.",
        f"""
WITH agg AS (
    SELECT location_id,
           observed_date                    AS date,
           MIN(temperature_c)               AS temp_min_c,
           MAX(temperature_c)               AS temp_max_c,
           ROUND(AVG(temperature_c), 1)     AS temp_mean_c,
           ROUND(SUM(precipitation_mm), 2)  AS precipitation_total_mm,
           MAX(wind_speed_kmh)              AS wind_speed_max_kmh,
           COUNT(*)                         AS hours_observed
    FROM hourly_weather
    GROUP BY location_id, observed_date
), keys AS (
    SELECT location_id, date FROM agg
    UNION
    SELECT location_id, date FROM daily_weather
), cmp AS (
    SELECT k.location_id,
           k.date,
           a.hours_observed AS hourly_n,
           d.hours_observed AS daily_n,
           substr(
               CASE WHEN d.temp_min_c IS NOT a.temp_min_c THEN ', temp_min_c' ELSE '' END ||
               CASE WHEN d.temp_max_c IS NOT a.temp_max_c THEN ', temp_max_c' ELSE '' END ||
               CASE WHEN d.temp_mean_c IS NOT a.temp_mean_c THEN ', temp_mean_c' ELSE '' END ||
               CASE WHEN d.precipitation_total_mm IS NOT a.precipitation_total_mm
                    THEN ', precipitation_total_mm' ELSE '' END ||
               CASE WHEN d.wind_speed_max_kmh IS NOT a.wind_speed_max_kmh
                    THEN ', wind_speed_max_kmh' ELSE '' END ||
               CASE WHEN d.hours_observed IS NOT a.hours_observed THEN ', hours_observed' ELSE '' END,
               3) AS mismatched,
           json_object(
               'stored', {DAILY_JSON},
               'recomputed_from_hourly', json_object(
                   'temp_min_c', a.temp_min_c, 'temp_max_c', a.temp_max_c,
                   'temp_mean_c', a.temp_mean_c, 'precipitation_total_mm', a.precipitation_total_mm,
                   'wind_speed_max_kmh', a.wind_speed_max_kmh, 'hours_observed', a.hours_observed)
           ) AS row_data
    FROM keys k
    LEFT JOIN agg a           ON a.location_id = k.location_id AND a.date = k.date
    LEFT JOIN daily_weather d ON d.location_id = k.location_id AND d.date = k.date
)
SELECT location_id,
       date AS record_key,
       CASE WHEN daily_n IS NULL OR hourly_n IS NULL THEN NULL ELSE mismatched END AS column_name,
       NULL AS failed_value,
       CASE
         WHEN daily_n IS NULL  THEN 'hourly data exists for ' || date || ' but daily_weather has no row'
         WHEN hourly_n IS NULL THEN 'daily_weather row for ' || date || ' has no hourly data behind it'
         ELSE 'stored daily values differ from hourly data in: ' || mismatched
       END AS reason,
       row_data
FROM cmp
WHERE daily_n IS NULL OR hourly_n IS NULL OR mismatched <> ''""",
    ),
    Rule(
        "DQ015", "daily min <= mean <= max", "validity", "error", "daily_weather",
        "A day's minimum temperature cannot exceed its mean, and the mean cannot exceed the maximum.",
        f"""
SELECT d.location_id,
       d.date AS record_key,
       'temp_min_c, temp_mean_c, temp_max_c' AS column_name,
       d.temp_min_c || ' / ' || d.temp_mean_c || ' / ' || d.temp_max_c AS failed_value,
       'expected temp_min_c <= temp_mean_c <= temp_max_c' AS reason,
       {DAILY_JSON} AS row_data
FROM daily_weather d
WHERE d.temp_min_c > d.temp_mean_c
   OR d.temp_mean_c > d.temp_max_c""",
    ),

    # ---------------------------------------------------------------- locations
    Rule(
        "DQ016", "model grid cell is close to the requested point", "accuracy", "warning", "locations",
        f"The grid cell Open-Meteo used must be known and within {MAX_GRID_OFFSET_DEG} deg of the "
        "requested coordinates, otherwise the data may describe a different place.",
        f"""
SELECT l.id AS location_id,
       'location:' || l.id AS record_key,
       'grid_latitude, grid_longitude' AS column_name,
       COALESCE(printf('%.4f, %.4f', l.grid_latitude, l.grid_longitude), 'NULL') AS failed_value,
       CASE
         WHEN l.grid_latitude IS NULL OR l.grid_longitude IS NULL
           THEN 'grid cell coordinates are missing'
         ELSE 'grid cell is ' || printf('%.3f', MAX(ABS(l.grid_latitude - l.latitude),
                                                  ABS(l.grid_longitude - l.longitude)))
              || ' deg away from the requested point'
       END AS reason,
       {LOCATION_JSON} AS row_data
FROM locations l
WHERE l.grid_latitude IS NULL
   OR l.grid_longitude IS NULL
   OR MAX(ABS(l.grid_latitude - l.latitude), ABS(l.grid_longitude - l.longitude)) > {MAX_GRID_OFFSET_DEG}""",
    ),
    Rule(
        "DQ017", "every location has hourly data", "completeness", "warning", "locations",
        "A location without any hourly observations means a load failed or returned nothing.",
        f"""
SELECT l.id AS location_id,
       'location:' || l.id AS record_key,
       NULL AS column_name,
       NULL AS failed_value,
       'location ' || l.name || ' has no hourly observations' AS reason,
       {LOCATION_JSON} AS row_data
FROM locations l
WHERE NOT EXISTS (SELECT 1 FROM hourly_weather h WHERE h.location_id = l.id)""",
    ),
]


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
UPSERT_RULE_SQL = """
INSERT INTO quality_rules (rule_id, name, dimension, severity, target_table, description, check_sql)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (rule_id) DO UPDATE SET
    name         = excluded.name,
    dimension    = excluded.dimension,
    severity     = excluded.severity,
    target_table = excluded.target_table,
    description  = excluded.description,
    check_sql    = excluded.check_sql
"""

UPSERT_VIOLATION_SQL = """
INSERT INTO quality_data_stat (
    rule_id, location_id, record_key, column_name, failed_value, reason, row_data, first_detected_at
) VALUES (
    :rule_id, :location_id, :record_key, :column_name, :failed_value, :reason, :row_data,
    strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
)
ON CONFLICT (rule_id, location_id, record_key) DO UPDATE SET
    column_name  = excluded.column_name,
    failed_value = excluded.failed_value,
    reason       = excluded.reason,
    row_data     = excluded.row_data
    -- first_detected_at intentionally not updated
"""


@dataclass
class RuleResult:
    rule: Rule
    rows_checked: int
    violations: int
    samples: list[dict] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.violations == 0:
            return "PASS"
        return "FAIL" if self.rule.severity == "error" else "WARN"


def run_quality_checks(conn: sqlite3.Connection, rules: list[Rule] = RULES,
                       sample_size: int = 20) -> list[RuleResult]:
    """Run every rule and make quality_data_stat match the current violations (atomically)."""
    ids = [r.rule_id for r in rules]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate rule ids")

    current: dict[tuple, dict] = {}
    with conn:
        conn.executemany(UPSERT_RULE_SQL, [
            (r.rule_id, r.name, r.dimension, r.severity, r.target_table, r.description, r.sql.strip())
            for r in rules
        ])
        # rules removed from code disappear, and ON DELETE CASCADE drops their violations
        conn.execute(f"DELETE FROM quality_rules WHERE rule_id NOT IN ({', '.join('?' * len(ids))})", ids)

        for rule in rules:
            try:
                cursor = conn.execute(rule.sql)
            except sqlite3.Error as exc:
                raise RuntimeError(f"quality rule {rule.rule_id} could not run: {exc}") from exc
            columns = tuple(d[0] for d in cursor.description)
            if columns != RESULT_COLUMNS:
                raise ValueError(f"quality rule {rule.rule_id} returns {columns}, expected {RESULT_COLUMNS}")
            for location_id, record_key, column_name, failed_value, reason, row_data in cursor:
                if location_id is None or record_key is None:
                    raise ValueError(f"quality rule {rule.rule_id} returned a row without location_id/record_key")
                current[(rule.rule_id, location_id, record_key)] = {
                    "rule_id": rule.rule_id,
                    "location_id": location_id,
                    "record_key": record_key,
                    "column_name": column_name,
                    "failed_value": None if failed_value is None else str(failed_value),
                    "reason": reason,
                    "row_data": row_data,
                }

        existing = set(conn.execute("SELECT rule_id, location_id, record_key FROM quality_data_stat"))
        conn.executemany(
            "DELETE FROM quality_data_stat WHERE rule_id = ? AND location_id = ? AND record_key = ?",
            sorted(existing - current.keys()),
        )
        conn.executemany(UPSERT_VIOLATION_SQL, [current[k] for k in sorted(current)])

    return summarize(conn, rules, sample_size)


def summarize(conn: sqlite3.Connection, rules: list[Rule], sample_size: int = 20) -> list[RuleResult]:
    results = []
    for rule in rules:
        (rows_checked,) = conn.execute(f"SELECT COUNT(*) FROM {rule.target_table}").fetchone()
        (violations,) = conn.execute(
            "SELECT COUNT(*) FROM quality_data_stat WHERE rule_id = ?", (rule.rule_id,)).fetchone()
        samples = [
            dict(zip(("location", "record_key", "column_name", "failed_value", "reason"), row))
            for row in conn.execute(
                """SELECT COALESCE(l.name, '#' || q.location_id), q.record_key, q.column_name,
                          q.failed_value, q.reason
                   FROM quality_data_stat q
                   LEFT JOIN locations l ON l.id = q.location_id
                   WHERE q.rule_id = ?
                   ORDER BY q.location_id, q.record_key
                   LIMIT ?""",
                (rule.rule_id, sample_size),
            )
        ]
        results.append(RuleResult(rule, rows_checked, violations, samples))
    return results


def overall_status(results: list[RuleResult]) -> str:
    statuses = {r.status for r in results}
    return "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"


# --------------------------------------------------------------------------- #
# Report (deterministic: same data -> byte-identical report, no wall-clock time)
# --------------------------------------------------------------------------- #
def _cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_report(results: list[RuleResult], *, db_path: str,
                  window: tuple[date, date] | None, locations: list[str],
                  cleaning: dict[str, Counter] | None = None) -> str:
    failing_errors = sum(r.status == "FAIL" for r in results)
    failing_warnings = sum(r.status == "WARN" for r in results)
    status = overall_status(results)
    lines = [
        "# Data quality report",
        "",
        "| | |",
        "|---|---|",
        f"| Database | `{_cell(db_path)}` |",
        f"| Window loaded this run | {f'{window[0]} to {window[1]}' if window else 'none (checks only)'} |",
        f"| Locations loaded this run | {_cell(', '.join(locations)) or 'none'} |",
        f"| Rules evaluated | {len(results)} |",
        f"| Overall status | **{status}**: {failing_errors} error rule(s) and "
        f"{failing_warnings} warning rule(s) have violations |",
        "",
        "Checks run over the whole database. Every violating row is stored in "
        "`quality_data_stat` (or the friendlier view `v_quality_violations`).",
        "",
        "## Results by rule",
        "",
        "| Rule | Name | Dimension | Severity | Table | Rows checked | Violations | Pass rate | Status |",
        "|---|---|---|---|---|---:|---:|---:|---|",
    ]
    for r in results:
        rate = "n/a" if r.rows_checked == 0 else f"{max(0.0, 1 - r.violations / r.rows_checked):.1%}"
        lines.append(
            f"| {r.rule.rule_id} | {_cell(r.rule.name)} | {r.rule.dimension} | {r.rule.severity} | "
            f"{r.rule.target_table} | {r.rows_checked} | {r.violations} | {rate} | {r.status} |"
        )

    if cleaning:
        lines += ["", "## Cleaning summary (this run)", "",
                  "What the transform step dropped or set to NULL before loading.", "",
                  "| Location | Raw rows | Clean rows | Details |", "|---|---:|---:|---|"]
        for name in sorted(cleaning):
            stats = cleaning[name]
            details = ", ".join(f"{k}: {v}" for k, v in sorted(stats.items())
                                if k not in ("raw_rows", "clean_rows")) or "nothing removed"
            lines.append(f"| {_cell(name)} | {stats.get('raw_rows', 0)} | "
                         f"{stats.get('clean_rows', 0)} | {_cell(details)} |")

    failing = [r for r in results if r.violations]
    lines += ["", "## Violations", ""]
    if not failing:
        lines.append("No violations.")
    for r in failing:
        lines += [
            f"### {r.rule.rule_id}: {_cell(r.rule.name)} ({r.rule.severity})",
            "",
            r.rule.description,
            "",
            "| Location | Record | Column | Value | Reason |",
            "|---|---|---|---|---|",
        ]
        for s in r.samples:
            lines.append(f"| {_cell(s['location'])} | {_cell(s['record_key'])} | {_cell(s['column_name'])} | "
                         f"{_cell(s['failed_value'])} | {_cell(s['reason'])} |")
        if r.violations > len(r.samples):
            lines.append("")
            lines.append(f"Showing {len(r.samples)} of {r.violations}. All rows: "
                         f"`SELECT * FROM v_quality_violations WHERE rule_id = '{r.rule.rule_id}';`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def rules_catalog_markdown(rules: list[Rule] = RULES) -> str:
    """Human-readable catalog of the rules, including the SQL behind each one."""
    lines = [
        "# Data quality rules",
        "",
        "Generated from `quality_checks.py` (`python quality_checks.py > docs/data_quality_rules.md`).",
        "",
        "Each rule is a SQL query returning the rows that violate it, with the columns "
        "`location_id, record_key, column_name, failed_value, reason, row_data`. "
        "Errors mean the data is wrong or unusable; warnings mean it is suspicious or incomplete.",
        "",
        "| Rule | Name | Dimension | Severity | Table | What it checks |",
        "|---|---|---|---|---|---|",
    ]
    lines += [f"| {r.rule_id} | {_cell(r.name)} | {r.dimension} | {r.severity} | {r.target_table} | "
              f"{_cell(r.description)} |" for r in rules]
    lines += ["", "## SQL", ""]
    for r in rules:
        lines += [f"### {r.rule_id}: {r.name}", "", "```sql", r.sql.strip(), "```", ""]
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    print(rules_catalog_markdown(), end="")
