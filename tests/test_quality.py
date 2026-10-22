"""Run with:  python -m unittest discover -s tests -v"""
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as p  # noqa: E402
import quality_checks as q  # noqa: E402
from test_pipeline import BERLIN, END, START, dirty_payload  # noqa: E402

# What the dirty fixture must produce (every other rule must pass).
EXPECTED_ON_FIXTURE = {
    "DQ001": ["2024-01-01T03:00:00Z"],   # temperature 999 -> NULL by cleaning
    "DQ002": ["2024-01-01T04:00:00Z"],   # precipitation -1 -> NULL by cleaning
    "DQ004": ["2024-01-01T05:00:00Z"],   # humidity missing in source
    "DQ006": ["2024-01-01T07:00:00Z"],   # unknown weather code 42 -> NULL
    "DQ009": ["2024-01-01T07:00:00Z"],   # empty 08:00 row dropped -> gap
    "DQ010": ["2024-01-01"],             # so that day has 23 hours
}


class QualityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "weather.db"
        self.report = self.dir / "report.md"

    def tearDown(self):
        self.tmp.cleanup()

    def run_pipeline(self, **kwargs):
        return p.run(self.db, [BERLIN], START, END, lambda *_: dirty_payload(),
                     report_path=self.report, **kwargs)

    def execute(self, sql, params=()):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(sql, params)

    def violations(self) -> dict[str, list[str]]:
        with closing(sqlite3.connect(self.db)) as conn:
            out: dict[str, list[str]] = {}
            for rule_id, key in conn.execute(
                    "SELECT rule_id, record_key FROM quality_data_stat ORDER BY rule_id, record_key"):
                out.setdefault(rule_id, []).append(key)
            return out

    def dump(self) -> str:
        with closing(sqlite3.connect(self.db)) as conn:
            return "\n".join(conn.iterdump())

    # ------------------------------------------------------------------ rules
    def test_fixture_produces_exactly_the_expected_violations(self):
        result = self.run_pipeline()
        self.assertEqual(self.violations(), EXPECTED_ON_FIXTURE)
        self.assertEqual(result.quality_status, "FAIL")

    def test_violation_rows_store_reason_and_snapshot(self):
        self.run_pipeline()
        with closing(sqlite3.connect(self.db)) as conn:
            reason, row_data, severity = conn.execute(
                "SELECT reason, row_data, severity FROM v_quality_violations WHERE rule_id = 'DQ001'"
            ).fetchone()
        self.assertIn("temperature_c is NULL", reason)
        self.assertEqual(severity, "error")
        snapshot = json.loads(row_data)
        self.assertEqual(snapshot["observed_at"], "2024-01-01T03:00:00Z")
        self.assertIsNone(snapshot["temperature_c"])

    def test_consistency_and_plausibility_rules_detect_corruption(self):
        self.run_pipeline()
        self.execute("UPDATE hourly_weather SET wind_direction_cardinal = 'E' "
                     "WHERE observed_at = '2024-01-02T00:00:00Z'")
        self.execute("UPDATE hourly_weather SET temperature_c = 30 "
                     "WHERE observed_at = '2024-01-02T12:00:00Z'")
        self.execute("UPDATE hourly_weather SET temperature_c = 4.0 "
                     "WHERE observed_at BETWEEN '2024-01-02T16:00:00Z' AND '2024-01-02T21:00:00Z'")
        self.execute("UPDATE hourly_weather SET precipitation_mm = 3.0, weather_code = 0 "
                     "WHERE observed_at = '2024-01-02T03:00:00Z'")
        self.execute("UPDATE hourly_weather SET observed_hour = 5 "
                     "WHERE observed_at = '2024-01-02T04:00:00Z'")
        self.execute("UPDATE locations SET grid_latitude = 53.5")
        p.run(self.db, [], START, END, checks_only=True)

        found = self.violations()
        self.assertEqual(found["DQ007"], ["2024-01-02T04:00:00Z"])
        self.assertEqual(found["DQ008"], ["2024-01-02T00:00:00Z"])
        self.assertEqual(found["DQ011"], ["2024-01-02T12:00:00Z", "2024-01-02T13:00:00Z"])
        self.assertEqual(found["DQ012"], ["2024-01-02T21:00:00Z"])
        self.assertEqual(found["DQ013"], ["2024-01-02T03:00:00Z"])
        self.assertEqual(found["DQ014"], ["2024-01-02"])        # daily no longer matches hourly
        self.assertEqual(found["DQ016"], ["location:1"])

    def test_daily_rules(self):
        self.run_pipeline()
        self.execute("UPDATE daily_weather SET temp_min_c = 50 WHERE date = '2024-01-02'")
        self.execute("INSERT INTO daily_weather VALUES (1, '2024-01-03', 1, 2, 1.5, 0, 5, 24, 1)")
        p.run(self.db, [], START, END, checks_only=True)
        found = self.violations()
        self.assertEqual(found["DQ014"], ["2024-01-02", "2024-01-03"])
        self.assertEqual(found["DQ015"], ["2024-01-02"])

    def test_every_rule_runs_on_an_empty_database(self):
        result = p.run(self.db, [], START, END, checks_only=True)
        self.assertEqual(len(result.quality), len(q.RULES))
        self.assertEqual(result.quality_status, "PASS")
        self.assertEqual(self.violations(), {})

    # ------------------------------------------------------------------ idempotency
    def test_rerun_leaves_database_and_report_unchanged(self):
        self.run_pipeline()
        db_first, report_first = self.dump(), self.report.read_bytes()
        self.run_pipeline()
        self.assertEqual(db_first, self.dump())
        self.assertEqual(report_first, self.report.read_bytes())

    def test_fixed_violations_are_removed_and_others_keep_first_detected_at(self):
        self.run_pipeline()
        self.execute("UPDATE quality_data_stat SET first_detected_at = '2000-01-01T00:00:00Z'")
        self.execute("UPDATE hourly_weather SET temperature_c = 0.0 "
                     "WHERE observed_at = '2024-01-01T03:00:00Z'")
        self.execute(p.REFRESH_DAILY_SQL, {"location_id": 1, "start": str(START), "end": str(END)})
        p.run(self.db, [], START, END, checks_only=True)

        self.assertNotIn("DQ001", self.violations())
        with closing(sqlite3.connect(self.db)) as conn:
            stamps = {s for (s,) in conn.execute("SELECT first_detected_at FROM quality_data_stat")}
        self.assertEqual(stamps, {"2000-01-01T00:00:00Z"})

    def test_removed_rule_drops_its_violations(self):
        self.run_pipeline()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            q.run_quality_checks(conn, rules=[r for r in q.RULES if r.rule_id != "DQ001"])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM quality_data_stat WHERE rule_id = 'DQ001'").fetchone(), (0,))
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM quality_rules WHERE rule_id = 'DQ001'").fetchone(), (0,))

    def test_broken_rule_rolls_back_and_reports_rule_id(self):
        self.run_pipeline()
        before = self.dump()
        bad = q.Rule("DQ999", "broken", "validity", "error", "hourly_weather", "x", "SELECT nope FROM nowhere")
        with closing(sqlite3.connect(self.db)) as conn:
            with self.assertRaisesRegex(RuntimeError, "DQ999"):
                q.run_quality_checks(conn, rules=[*q.RULES, bad])
        self.assertEqual(before, self.dump())

    # ------------------------------------------------------------------ report + CLI
    def test_report_contents(self):
        self.run_pipeline()
        text = self.report.read_text()
        self.assertIn("**FAIL**: 2 error rule(s) and 4 warning rule(s) have violations", text)
        self.assertIn("| DQ001 | temperature_c is not null | completeness | error | hourly_weather | 47 | 1 |", text)
        self.assertIn("### DQ009: no gaps in the hourly series (warning)", text)
        self.assertIn("## Cleaning summary (this run)", text)

    def test_cli_fail_on_error_exit_code(self):
        saved = self.dir / "response.json"
        saved.write_text(json.dumps(dirty_payload()))
        args = ["--db", str(self.db), "--input-json", str(saved), "--report", str(self.report),
                "--start-date", str(START), "--end-date", str(END)]
        self.assertEqual(p.main(args), 0)
        self.assertEqual(p.main([*args, "--fail-on-error"]), 2)
        self.execute("DELETE FROM hourly_weather WHERE temperature_c IS NULL OR precipitation_mm IS NULL")
        self.execute(p.REFRESH_DAILY_SQL, {"location_id": 1, "start": str(START), "end": str(END)})
        # only warnings left (gaps, incomplete day) -> not a failure
        self.assertEqual(p.main(["--db", str(self.db), "--report", str(self.report),
                                 "--checks-only", "--fail-on-error"]), 0)


if __name__ == "__main__":
    unittest.main()
