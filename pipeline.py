#!/usr/bin/env python3
"""
Open-Meteo -> SQLite weather pipeline (standard library only).

  Extract    GET https://archive-api.open-meteo.com/v1/archive
             Historical Weather API, hourly ERA5 reanalysis. Past data does not
             change, so the same request always returns the same answer.
  Transform  validate the response shape, parse timestamps to UTC, drop junk
             rows, de-duplicate, null out physically impossible values, round,
             derive date / hour / compass direction.
  Load       upsert into SQLite, one transaction per location, then rebuild the
             daily aggregates for the loaded window.
  Quality    run the rules in quality_checks.py over the whole database, store
             every violating row in quality_data_stat, and write a Markdown report.

Why it is idempotent (running it twice gives the same database):
  * migrations are recorded in schema_migrations and applied exactly once
  * every table has a natural key (lat/lon, location+timestamp, location+date)
    and every write is INSERT ... ON CONFLICT DO UPDATE, never a blind INSERT
  * transforms are pure and deterministic: no now(), no random ids, sorted output
  * all writes for a location commit atomically, so a crash never leaves
    half-loaded data that a re-run would have to work around
  * quality_data_stat is synced to the current violations (fixed ones removed,
    unchanged ones untouched) and the report contains no wall-clock time

Usage:
  python pipeline.py
  python pipeline.py --start-date 2024-01-01 --end-date 2024-01-31 \\
      --location "Berlin:52.52:13.41" --location "Paris:48.8566:2.3522"
  python pipeline.py --input-json saved_response.json   # offline, one location
  python pipeline.py --checks-only                      # re-run quality checks only
  python pipeline.py --fail-on-error                    # exit 2 if an error rule fails (CI)
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import quality_checks

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# API variable -> (column name, valid physical range, decimals to keep)
HOURLY_FIELDS: dict[str, tuple[str, tuple[float, float], int]] = {
    "temperature_2m":       ("temperature_c",         (-90.0, 60.0),  1),
    "relative_humidity_2m": ("relative_humidity_pct", (0.0, 100.0),   0),
    "precipitation":        ("precipitation_mm",      (0.0, 500.0),   2),
    "wind_speed_10m":       ("wind_speed_kmh",        (0.0, 500.0),   1),
    "wind_direction_10m":   ("wind_direction_deg",    (0.0, 360.0),   0),
}
HOURLY_VARS = [*HOURLY_FIELDS, "weather_code"]
CARDINALS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

log = logging.getLogger("pipeline")


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float
    longitude: float

    @classmethod
    def parse(cls, spec: str) -> "Location":
        """Parse 'NAME:LAT:LON'. Coordinates are rounded so the natural key is stable."""
        try:
            name, lat, lon = spec.rsplit(":", 2)
            loc = cls(name.strip(), round(float(lat), 4), round(float(lon), 4))
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected NAME:LAT:LON, got {spec!r}") from None
        if not loc.name or not -90 <= loc.latitude <= 90 or not -180 <= loc.longitude <= 180:
            raise argparse.ArgumentTypeError(f"invalid location {spec!r}")
        return loc


# --------------------------------------------------------------------------- #
# Extract
# --------------------------------------------------------------------------- #
def fetch_hourly(loc: Location, start: date, end: date, *,
                 retries: int = 3, timeout: float = 30.0) -> dict:
    """Call the Open-Meteo archive endpoint, retrying transient failures."""
    params = {
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_VARS),
        "timezone": "GMT",                 # store everything in UTC
        "temperature_unit": "celsius",     # pin units explicitly so defaults
        "wind_speed_unit": "kmh",          # changing upstream can't silently
        "precipitation_unit": "mm",        # change what we store
    }
    url = f"{ARCHIVE_URL}?{urllib.parse.urlencode(params)}"
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code < 500 and exc.code != 429:      # bad request: retrying won't help
                raise RuntimeError(f"Open-Meteo HTTP {exc.code} for {loc.name}: {detail}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < retries:
            delay = 2 ** attempt
            log.warning("fetch %s failed (%s); retry %d/%d in %ds",
                        loc.name, last_error, attempt, retries - 1, delay)
            time.sleep(delay)

    raise RuntimeError(f"Open-Meteo unreachable for {loc.name} after {retries} attempts: {last_error}")


# --------------------------------------------------------------------------- #
# Transform (pure: same input -> same output, no I/O)
# --------------------------------------------------------------------------- #
def _num(value) -> float | None:
    """Coerce to a finite float; anything else (None, '', NaN, inf, bool) -> None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _validate_shape(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("payload is not a JSON object")
    if payload.get("error"):
        raise ValueError(f"API returned an error: {payload.get('reason')}")
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise ValueError("payload has no hourly.time array")
    missing = [v for v in HOURLY_VARS if not isinstance(hourly.get(v), list)]
    if missing:
        raise ValueError(f"payload is missing hourly variables: {missing}")
    ragged = [v for v in HOURLY_VARS if len(hourly[v]) != len(hourly["time"])]
    if ragged:
        raise ValueError(f"hourly arrays differ in length from 'time': {ragged}")
    if payload.get("utc_offset_seconds", 0) != 0:
        raise ValueError("expected UTC timestamps (request is sent with timezone=GMT)")
    return hourly


def transform(payload: dict, start: date, end: date,
              valid_codes: set[int]) -> tuple[list[dict], Counter]:
    """Turn the raw column-oriented API payload into clean, sorted, unique hourly rows."""
    hourly = _validate_shape(payload)
    stats: Counter = Counter(raw_rows=len(hourly["time"]))
    rows: dict[str, dict] = {}

    for i, raw_ts in enumerate(hourly["time"]):
        # 1. timestamp -> aware UTC datetime, inside the requested window
        try:
            ts = datetime.fromisoformat(raw_ts)
        except (TypeError, ValueError):
            stats["dropped_bad_timestamp"] += 1
            continue
        ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
        if not start <= ts.date() <= end:
            stats["dropped_outside_window"] += 1
            continue

        # 2. numeric measures: coerce, range-check, round
        row: dict = {}
        for var, (col, (lo, hi), ndigits) in HOURLY_FIELDS.items():
            value = _num(hourly[var][i])
            if value is not None and not lo <= value <= hi:
                stats[f"nulled_out_of_range_{col}"] += 1
                value = None
            if value is not None:
                value = int(round(value)) if ndigits == 0 else round(value, ndigits) + 0.0  # +0.0 kills -0.0
            row[col] = value
        if row["wind_direction_deg"] is not None:
            row["wind_direction_deg"] %= 360                          # 360 deg == 0 deg (north)

        # 3. weather code must be a known WMO code
        code = _num(hourly["weather_code"][i])
        if code is not None and (code != int(code) or int(code) not in valid_codes):
            stats["nulled_unknown_weather_code"] += 1
            code = None
        row["weather_code"] = None if code is None else int(code)

        # 4. a row with no measurements at all carries no information
        if all(v is None for v in row.values()):
            stats["dropped_empty_row"] += 1
            continue

        # 5. derived fields
        deg = row["wind_direction_deg"]
        row["wind_direction_cardinal"] = None if deg is None else CARDINALS[int((deg + 11.25) // 22.5) % 16]
        key = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        row.update(observed_at=key, observed_date=ts.date().isoformat(), observed_hour=ts.hour)

        # 6. de-duplicate on timestamp (deterministically: last occurrence wins)
        if key in rows:
            stats["dropped_duplicate_timestamp"] += 1
        rows[key] = row

    stats["clean_rows"] = len(rows)
    return [rows[k] for k in sorted(rows)], stats


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
UPSERT_LOCATION_SQL = """
INSERT INTO locations (name, latitude, longitude, grid_latitude, grid_longitude, elevation_m)
VALUES (:name, :latitude, :longitude, :grid_latitude, :grid_longitude, :elevation_m)
ON CONFLICT (latitude, longitude) DO UPDATE SET
    name           = excluded.name,
    grid_latitude  = excluded.grid_latitude,
    grid_longitude = excluded.grid_longitude,
    elevation_m    = excluded.elevation_m
"""

UPSERT_HOURLY_SQL = """
INSERT INTO hourly_weather (
    location_id, observed_at, observed_date, observed_hour,
    temperature_c, relative_humidity_pct, precipitation_mm,
    wind_speed_kmh, wind_direction_deg, wind_direction_cardinal, weather_code
) VALUES (
    :location_id, :observed_at, :observed_date, :observed_hour,
    :temperature_c, :relative_humidity_pct, :precipitation_mm,
    :wind_speed_kmh, :wind_direction_deg, :wind_direction_cardinal, :weather_code
)
ON CONFLICT (location_id, observed_at) DO UPDATE SET
    observed_date           = excluded.observed_date,
    observed_hour           = excluded.observed_hour,
    temperature_c           = excluded.temperature_c,
    relative_humidity_pct   = excluded.relative_humidity_pct,
    precipitation_mm        = excluded.precipitation_mm,
    wind_speed_kmh          = excluded.wind_speed_kmh,
    wind_direction_deg      = excluded.wind_direction_deg,
    wind_direction_cardinal = excluded.wind_direction_cardinal,
    weather_code            = excluded.weather_code
"""

# Recomputed from hourly_weather (the source of truth) rather than incremented,
# so re-running can never double-count.
REFRESH_DAILY_SQL = """
INSERT INTO daily_weather (
    location_id, date, temp_min_c, temp_max_c, temp_mean_c,
    precipitation_total_mm, wind_speed_max_kmh, hours_observed, is_complete
)
SELECT location_id,
       observed_date,
       MIN(temperature_c),
       MAX(temperature_c),
       ROUND(AVG(temperature_c), 1),
       ROUND(SUM(precipitation_mm), 2),
       MAX(wind_speed_kmh),
       COUNT(*),
       COUNT(*) = 24
FROM hourly_weather
WHERE location_id = :location_id
  AND observed_date BETWEEN :start AND :end
GROUP BY location_id, observed_date
ON CONFLICT (location_id, date) DO UPDATE SET
    temp_min_c             = excluded.temp_min_c,
    temp_max_c             = excluded.temp_max_c,
    temp_mean_c            = excluded.temp_mean_c,
    precipitation_total_mm = excluded.precipitation_total_mm,
    wind_speed_max_kmh     = excluded.wind_speed_max_kmh,
    hours_observed         = excluded.hours_observed,
    is_complete            = excluded.is_complete
"""

MIGRATION_NAME = re.compile(r"^\d{3,}_[a-z0-9_]+\.sql$")


def apply_migrations(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply each migrations/*.sql file once, in filename order, each in its own transaction."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                 "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    conn.commit()
    done = {v for (v,) in conn.execute("SELECT version FROM schema_migrations")}
    applied: list[str] = []

    for path in sorted(directory.glob("*.sql")):
        if path.name in done:
            continue
        if not MIGRATION_NAME.match(path.name):
            raise ValueError(f"bad migration file name: {path.name} (expected NNN_snake_case.sql)")
        script = (
            "BEGIN;\n"
            + path.read_text(encoding="utf-8")
            + f"\n;INSERT INTO schema_migrations (version, applied_at) "
              f"VALUES ('{path.name}', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));\n"
            + "COMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise
        log.info("applied migration %s", path.name)
        applied.append(path.name)
    return applied


def load(conn: sqlite3.Connection, loc: Location, payload: dict,
         rows: list[dict], start: date, end: date) -> int:
    """Write one location's data. Caller owns the transaction."""
    conn.execute(UPSERT_LOCATION_SQL, {
        "name": loc.name,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "grid_latitude": _num(payload.get("latitude")),
        "grid_longitude": _num(payload.get("longitude")),
        "elevation_m": _num(payload.get("elevation")),
    })
    (location_id,) = conn.execute(
        "SELECT id FROM locations WHERE latitude = ? AND longitude = ?",
        (loc.latitude, loc.longitude),
    ).fetchone()
    conn.executemany(UPSERT_HOURLY_SQL, [{"location_id": location_id, **r} for r in rows])
    conn.execute(REFRESH_DAILY_SQL, {"location_id": location_id,
                                     "start": start.isoformat(), "end": end.isoformat()})
    return location_id


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
Fetcher = Callable[[Location, date, date], dict]


@dataclass
class RunResult:
    cleaning: dict[str, Counter]                       # transform stats per location
    quality: list[quality_checks.RuleResult]           # one result per quality rule

    @property
    def quality_status(self) -> str:
        return quality_checks.overall_status(self.quality)


def run(db_path: str | Path, locations: list[Location], start: date, end: date,
        fetch: Fetcher = fetch_hourly, *, report_path: str | Path | None = None,
        checks_only: bool = False) -> RunResult:
    if start > end:
        raise ValueError(f"start date {start} is after end date {end}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        apply_migrations(conn)
        valid_codes = {c for (c,) in conn.execute("SELECT code FROM weather_codes")}
        cleaning: dict[str, Counter] = {}

        for loc in [] if checks_only else locations:
            payload = fetch(loc, start, end)                 # network happens outside any transaction
            rows, stats = transform(payload, start, end, valid_codes)
            with conn:                                       # atomic: commit all or roll back all
                load(conn, loc, payload, rows, start, end)
            log.info("%s %s..%s: %s", loc.name, start, end, dict(sorted(stats.items())))
            cleaning[loc.name] = stats

        quality = quality_checks.run_quality_checks(conn)
        result = RunResult(cleaning, quality)
        failing = [f"{r.rule.rule_id}={r.violations}" for r in quality if r.violations]
        log.info("data quality %s (%d rules; violations: %s)",
                 result.quality_status, len(quality), ", ".join(failing) or "none")

        if report_path is not None:
            report = quality_checks.render_report(
                quality,
                db_path=str(db_path),
                window=None if checks_only else (start, end),
                locations=[] if checks_only else [loc.name for loc in locations],
                cleaning=cleaning,
            )
            Path(report_path).write_text(report, encoding="utf-8")
            log.info("wrote data quality report to %s", report_path)
        return result
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    # ERA5 data lags real time by ~5 days, so the default window ends a week ago.
    # For byte-identical runs on different days, pass explicit dates.
    default_end = date.today() - timedelta(days=7)

    parser = argparse.ArgumentParser(description="Load Open-Meteo hourly history into SQLite.")
    parser.add_argument("--db", default="weather.db", help="SQLite file (default: weather.db)")
    parser.add_argument("--location", action="append", type=Location.parse, metavar="NAME:LAT:LON",
                        help="repeatable; default Berlin:52.52:13.41")
    parser.add_argument("--start-date", type=date.fromisoformat, default=default_end - timedelta(days=6))
    parser.add_argument("--end-date", type=date.fromisoformat, default=default_end)
    parser.add_argument("--input-json", type=Path,
                        help="use a saved API response instead of calling the API (one location only)")
    parser.add_argument("--report", type=Path, default=Path("data_quality_report.md"),
                        help="where to write the data quality report (default: data_quality_report.md)")
    parser.add_argument("--checks-only", action="store_true",
                        help="skip extract/load and only run the quality checks on the existing database")
    parser.add_argument("--fail-on-error", action="store_true",
                        help="exit with status 2 if any error-severity quality rule has violations")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    locations = args.location or [Location("Berlin", 52.52, 13.41)]
    fetch: Fetcher = fetch_hourly
    if args.input_json:
        if len(locations) != 1:
            parser.error("--input-json works with exactly one --location")
        saved = json.loads(args.input_json.read_text(encoding="utf-8"))
        fetch = lambda *_: saved  # noqa: E731

    try:
        result = run(args.db, locations, args.start_date, args.end_date, fetch,
                     report_path=args.report, checks_only=args.checks_only)
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        log.error("pipeline failed: %s", exc)
        return 1
    if args.fail_on_error and result.quality_status == "FAIL":
        log.error("error-severity data quality rules failed; see %s", args.report)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
