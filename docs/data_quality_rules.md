# Data quality rules

Generated from `quality_checks.py` (`python quality_checks.py > docs/data_quality_rules.md`).

Each rule is a SQL query returning the rows that violate it, with the columns `location_id, record_key, column_name, failed_value, reason, row_data`. Errors mean the data is wrong or unusable; warnings mean it is suspicious or incomplete.

| Rule | Name | Dimension | Severity | Table | What it checks |
|---|---|---|---|---|---|
| DQ001 | temperature_c is not null | completeness | error | hourly_weather | Every hourly observation must have temperature_c. Temperature is the core measure of this dataset. |
| DQ002 | precipitation_mm is not null | completeness | error | hourly_weather | Every hourly observation must have precipitation_mm. Needed for daily precipitation totals. |
| DQ003 | wind_speed_kmh is not null | completeness | error | hourly_weather | Every hourly observation must have wind_speed_kmh. Needed for daily maximum wind. |
| DQ004 | relative_humidity_pct is not null | completeness | warning | hourly_weather | Every hourly observation must have relative_humidity_pct. Useful but not used in aggregates. |
| DQ005 | wind_direction_deg is not null | completeness | warning | hourly_weather | Every hourly observation must have wind_direction_deg. Useful but not used in aggregates. |
| DQ006 | weather_code is not null | completeness | warning | hourly_weather | Every hourly observation must have weather_code. NULL also results from unknown WMO codes. |
| DQ007 | timestamp is well-formed and consistent | validity | error | hourly_weather | observed_at must be an on-the-hour ISO-8601 UTC timestamp, and observed_date / observed_hour must equal its date and hour parts. |
| DQ008 | wind cardinal matches wind degrees | consistency | error | hourly_weather | wind_direction_cardinal must be the 16-point compass sector of wind_direction_deg (both NULL together). |
| DQ009 | no gaps in the hourly series | completeness | warning | hourly_weather | Consecutive observations of a location must be exactly one hour apart. One violation per gap, keyed by the last hour before the gap. |
| DQ010 | every day has 24 hourly observations | completeness | warning | daily_weather | A daily aggregate built from fewer than 24 hours under-reports totals and extremes. |
| DQ011 | no implausible temperature jumps | plausibility | warning | hourly_weather | Temperature must not change by more than 10 degC between two consecutive hours. |
| DQ012 | no flat-lined temperature | plausibility | warning | hourly_weather | The same temperature for 6 or more consecutive hours suggests stuck or forward-filled values. Flags each hour that completes such a run. |
| DQ013 | precipitation agrees with weather code | consistency | warning | hourly_weather | 0.5 mm or more of precipitation contradicts a clear/cloudy weather code (0-3). |
| DQ014 | daily aggregates reconcile with hourly data | consistency | error | daily_weather | Each daily_weather row must equal the aggregates recomputed from hourly_weather, and every day with hourly data must have a daily row. |
| DQ015 | daily min <= mean <= max | validity | error | daily_weather | A day's minimum temperature cannot exceed its mean, and the mean cannot exceed the maximum. |
| DQ016 | model grid cell is close to the requested point | accuracy | warning | locations | The grid cell Open-Meteo used must be known and within 0.25 deg of the requested coordinates, otherwise the data may describe a different place. |
| DQ017 | every location has hourly data | completeness | warning | locations | A location without any hourly observations means a load failed or returned nothing. |

## SQL

### DQ001: temperature_c is not null

```sql
SELECT h.location_id,
       h.observed_at  AS record_key,
       'temperature_c'     AS column_name,
       NULL           AS failed_value,
       'temperature_c is NULL (missing in the API response or rejected as invalid during cleaning)'
                      AS reason,
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM hourly_weather h
WHERE h.temperature_c IS NULL
```

### DQ002: precipitation_mm is not null

```sql
SELECT h.location_id,
       h.observed_at  AS record_key,
       'precipitation_mm'     AS column_name,
       NULL           AS failed_value,
       'precipitation_mm is NULL (missing in the API response or rejected as invalid during cleaning)'
                      AS reason,
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM hourly_weather h
WHERE h.precipitation_mm IS NULL
```

### DQ003: wind_speed_kmh is not null

```sql
SELECT h.location_id,
       h.observed_at  AS record_key,
       'wind_speed_kmh'     AS column_name,
       NULL           AS failed_value,
       'wind_speed_kmh is NULL (missing in the API response or rejected as invalid during cleaning)'
                      AS reason,
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM hourly_weather h
WHERE h.wind_speed_kmh IS NULL
```

### DQ004: relative_humidity_pct is not null

```sql
SELECT h.location_id,
       h.observed_at  AS record_key,
       'relative_humidity_pct'     AS column_name,
       NULL           AS failed_value,
       'relative_humidity_pct is NULL (missing in the API response or rejected as invalid during cleaning)'
                      AS reason,
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM hourly_weather h
WHERE h.relative_humidity_pct IS NULL
```

### DQ005: wind_direction_deg is not null

```sql
SELECT h.location_id,
       h.observed_at  AS record_key,
       'wind_direction_deg'     AS column_name,
       NULL           AS failed_value,
       'wind_direction_deg is NULL (missing in the API response or rejected as invalid during cleaning)'
                      AS reason,
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM hourly_weather h
WHERE h.wind_direction_deg IS NULL
```

### DQ006: weather_code is not null

```sql
SELECT h.location_id,
       h.observed_at  AS record_key,
       'weather_code'     AS column_name,
       NULL           AS failed_value,
       'weather_code is NULL (missing in the API response or rejected as invalid during cleaning)'
                      AS reason,
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM hourly_weather h
WHERE h.weather_code IS NULL
```

### DQ007: timestamp is well-formed and consistent

```sql
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
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM hourly_weather h
WHERE h.observed_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:00:00Z'
   OR julianday(h.observed_at) IS NULL
   OR h.observed_date <> substr(h.observed_at, 1, 10)
   OR h.observed_hour <> CAST(substr(h.observed_at, 12, 2) AS INTEGER)
```

### DQ008: wind cardinal matches wind degrees

```sql
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
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM hourly_weather h
LEFT JOIN compass c ON c.idx = ((h.wind_direction_deg * 100 + 1125) / 2250) % 16
WHERE h.wind_direction_cardinal IS NOT c.name
```

### DQ009: no gaps in the hourly series

```sql
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
WHERE missing_hours > 0
```

### DQ010: every day has 24 hourly observations

```sql
SELECT d.location_id,
       d.date AS record_key,
       'hours_observed' AS column_name,
       CAST(d.hours_observed AS TEXT) AS failed_value,
       'only ' || d.hours_observed || ' of 24 hourly observations for ' || d.date AS reason,
       json_object(
           'date', d.date, 'temp_min_c', d.temp_min_c, 'temp_max_c', d.temp_max_c,
           'temp_mean_c', d.temp_mean_c, 'precipitation_total_mm', d.precipitation_total_mm,
           'wind_speed_max_kmh', d.wind_speed_max_kmh, 'hours_observed', d.hours_observed,
           'is_complete', d.is_complete) AS row_data
FROM daily_weather d
WHERE d.hours_observed < 24
```

### DQ011: no implausible temperature jumps

```sql
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
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM h
WHERE h.temperature_c IS NOT NULL
  AND h.prev_temp IS NOT NULL
  AND ABS(h.temperature_c - h.prev_temp) > 10
  AND (julianday(h.observed_at) - julianday(h.prev_at)) * 24 < 1.5
```

### DQ012: no flat-lined temperature

```sql
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
       'temperature stuck at ' || h.temperature_c || ' degC for 6 consecutive hours since '
         || h.run_start AS reason,
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM h
WHERE h.temperature_c = h.t1 AND h.t1 = h.t2 AND h.t2 = h.t3 AND h.t3 = h.t4 AND h.t4 = h.t5
  AND (julianday(h.observed_at) - julianday(h.run_start)) * 24 < 6 - 0.5
```

### DQ013: precipitation agrees with weather code

```sql
SELECT h.location_id,
       h.observed_at AS record_key,
       'precipitation_mm, weather_code' AS column_name,
       h.precipitation_mm || ' mm / code ' || h.weather_code AS failed_value,
       h.precipitation_mm || ' mm of precipitation but weather code ' || h.weather_code
         || ' (' || wc.description || ') means no precipitation' AS reason,
       json_object(
           'observed_at', h.observed_at, 'observed_date', h.observed_date,
           'observed_hour', h.observed_hour, 'temperature_c', h.temperature_c,
           'relative_humidity_pct', h.relative_humidity_pct, 'precipitation_mm', h.precipitation_mm,
           'wind_speed_kmh', h.wind_speed_kmh, 'wind_direction_deg', h.wind_direction_deg,
           'wind_direction_cardinal', h.wind_direction_cardinal, 'weather_code', h.weather_code) AS row_data
FROM hourly_weather h
JOIN weather_codes wc ON wc.code = h.weather_code
WHERE h.precipitation_mm >= 0.5
  AND h.weather_code IN (0, 1, 2, 3)
```

### DQ014: daily aggregates reconcile with hourly data

```sql
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
               'stored', json_object(
           'date', d.date, 'temp_min_c', d.temp_min_c, 'temp_max_c', d.temp_max_c,
           'temp_mean_c', d.temp_mean_c, 'precipitation_total_mm', d.precipitation_total_mm,
           'wind_speed_max_kmh', d.wind_speed_max_kmh, 'hours_observed', d.hours_observed,
           'is_complete', d.is_complete),
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
WHERE daily_n IS NULL OR hourly_n IS NULL OR mismatched <> ''
```

### DQ015: daily min <= mean <= max

```sql
SELECT d.location_id,
       d.date AS record_key,
       'temp_min_c, temp_mean_c, temp_max_c' AS column_name,
       d.temp_min_c || ' / ' || d.temp_mean_c || ' / ' || d.temp_max_c AS failed_value,
       'expected temp_min_c <= temp_mean_c <= temp_max_c' AS reason,
       json_object(
           'date', d.date, 'temp_min_c', d.temp_min_c, 'temp_max_c', d.temp_max_c,
           'temp_mean_c', d.temp_mean_c, 'precipitation_total_mm', d.precipitation_total_mm,
           'wind_speed_max_kmh', d.wind_speed_max_kmh, 'hours_observed', d.hours_observed,
           'is_complete', d.is_complete) AS row_data
FROM daily_weather d
WHERE d.temp_min_c > d.temp_mean_c
   OR d.temp_mean_c > d.temp_max_c
```

### DQ016: model grid cell is close to the requested point

```sql
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
       json_object(
           'id', l.id, 'name', l.name, 'latitude', l.latitude, 'longitude', l.longitude,
           'grid_latitude', l.grid_latitude, 'grid_longitude', l.grid_longitude,
           'elevation_m', l.elevation_m) AS row_data
FROM locations l
WHERE l.grid_latitude IS NULL
   OR l.grid_longitude IS NULL
   OR MAX(ABS(l.grid_latitude - l.latitude), ABS(l.grid_longitude - l.longitude)) > 0.25
```

### DQ017: every location has hourly data

```sql
SELECT l.id AS location_id,
       'location:' || l.id AS record_key,
       NULL AS column_name,
       NULL AS failed_value,
       'location ' || l.name || ' has no hourly observations' AS reason,
       json_object(
           'id', l.id, 'name', l.name, 'latitude', l.latitude, 'longitude', l.longitude,
           'grid_latitude', l.grid_latitude, 'grid_longitude', l.grid_longitude,
           'elevation_m', l.elevation_m) AS row_data
FROM locations l
WHERE NOT EXISTS (SELECT 1 FROM hourly_weather h WHERE h.location_id = l.id)
```
