"""Run with:  python -m unittest discover -s tests -v"""
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline as p  # noqa: E402

START, END = date(2024, 1, 1), date(2024, 1, 2)
BERLIN = p.Location("Berlin", 52.52, 13.41)


def dirty_payload() -> dict:
    """A realistic Open-Meteo response with every kind of mess the cleaner handles."""
    times = [f"2024-01-0{d}T{h:02d}:00" for d in (1, 2) for h in range(24)]
    n = len(times)
    hourly = {
        "time": times,
        "temperature_2m": [round(-2 + i * 0.3, 1) for i in range(n)],
        "relative_humidity_2m": [80] * n,
        "precipitation": [0.1] * n,
        "wind_speed_10m": [12.0] * n,
        "wind_direction_10m": [200] * n,
        "weather_code": [3] * n,
    }
    hourly["temperature_2m"][3] = 999.0          # impossible        -> NULL
    hourly["precipitation"][4] = -1.0            # negative          -> NULL
    hourly["relative_humidity_2m"][5] = None     # missing           -> stays NULL
    hourly["wind_direction_10m"][6] = 360        # 360 deg           -> 0 / "N"
    hourly["weather_code"][7] = 42               # not a WMO code    -> NULL
    for var in p.HOURLY_VARS:                    # no data at all    -> row dropped
        hourly[var][8] = None
    for ts, temp in [("2024-01-01T10:00", 5.5),   # duplicate         -> last wins
                     ("2023-12-31T23:00", 1.0),   # outside window    -> dropped
                     ("not-a-date", 1.0)]:        # garbage timestamp -> dropped
        hourly["time"].append(ts)
        hourly["temperature_2m"].append(temp)
        for var in p.HOURLY_VARS[1:]:
            hourly[var].append(hourly[var][0])
    return {"latitude": 52.52, "longitude": 13.419998, "elevation": 38.0,
            "utc_offset_seconds": 0, "timezone": "GMT", "hourly": hourly}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "weather.db"

    def tearDown(self):
        self.tmp.cleanup()

    def run_pipeline(self, fetch=lambda *_: dirty_payload()):
        return p.run(self.db, [BERLIN], START, END, fetch)

    def query(self, sql):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql)]

    def dump(self) -> str:
        with closing(sqlite3.connect(self.db)) as conn:
            return "\n".join(conn.iterdump())

    def test_running_twice_gives_identical_database(self):
        self.run_pipeline()
        first = self.dump()
        self.run_pipeline()
        self.assertEqual(first, self.dump())

    def test_migrations_are_applied_once(self):
        self.run_pipeline()
        self.run_pipeline()
        self.assertEqual(self.query("SELECT version FROM schema_migrations ORDER BY version"),
                         [{"version": "001_create_weather_schema.sql"},
                          {"version": "002_create_quality_tables.sql"}])

    def test_cleaning_rules(self):
        stats = self.run_pipeline().cleaning["Berlin"]
        rows = {r["observed_at"]: r for r in self.query("SELECT * FROM hourly_weather")}

        self.assertEqual(len(rows), 47)
        self.assertEqual(stats["clean_rows"], 47)
        self.assertIsNone(rows["2024-01-01T03:00:00Z"]["temperature_c"])
        self.assertIsNone(rows["2024-01-01T04:00:00Z"]["precipitation_mm"])
        self.assertIsNone(rows["2024-01-01T05:00:00Z"]["relative_humidity_pct"])
        self.assertEqual(rows["2024-01-01T06:00:00Z"]["wind_direction_deg"], 0)
        self.assertEqual(rows["2024-01-01T06:00:00Z"]["wind_direction_cardinal"], "N")
        self.assertIsNone(rows["2024-01-01T07:00:00Z"]["weather_code"])
        self.assertNotIn("2024-01-01T08:00:00Z", rows)
        self.assertEqual(rows["2024-01-01T10:00:00Z"]["temperature_c"], 5.5)
        self.assertEqual(rows["2024-01-02T00:00:00Z"]["wind_direction_cardinal"], "SSW")
        self.assertEqual(stats["dropped_duplicate_timestamp"], 1)
        self.assertEqual(stats["dropped_outside_window"], 1)
        self.assertEqual(stats["dropped_bad_timestamp"], 1)

    def test_daily_aggregates(self):
        self.run_pipeline()
        days = self.query("SELECT date, hours_observed, is_complete FROM daily_weather ORDER BY date")
        self.assertEqual(days, [
            {"date": "2024-01-01", "hours_observed": 23, "is_complete": 0},
            {"date": "2024-01-02", "hours_observed": 24, "is_complete": 1},
        ])

    def test_bad_payload_writes_nothing(self):
        broken = dirty_payload()
        broken["hourly"]["precipitation"].pop()            # ragged arrays
        with self.assertRaises(ValueError):
            self.run_pipeline(lambda *_: broken)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM hourly_weather"), [{"n": 0}])
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM locations"), [{"n": 0}])


if __name__ == "__main__":
    unittest.main()
