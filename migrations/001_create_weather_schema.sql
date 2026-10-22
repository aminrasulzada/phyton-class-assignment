-- 001_create_weather_schema.sql
-- Schema for the Open-Meteo Historical Weather API (/v1/archive) hourly response.
--
-- Response entity -> tables
--   latitude / longitude / elevation  -> locations
--   hourly.weather_code (WMO codes)   -> weather_codes (lookup)
--   hourly.* arrays (one row per hour) -> hourly_weather
--   derived per-day rollup            -> daily_weather
--
-- Applied once by pipeline.py and recorded in schema_migrations.
-- Do not put BEGIN/COMMIT in here: the runner wraps the file in a transaction.

CREATE TABLE IF NOT EXISTS locations (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,
    latitude        REAL    NOT NULL CHECK (latitude  BETWEEN -90  AND 90),   -- as requested
    longitude       REAL    NOT NULL CHECK (longitude BETWEEN -180 AND 180),  -- as requested
    grid_latitude   REAL,   -- centre of the model grid cell Open-Meteo actually used
    grid_longitude  REAL,
    elevation_m     REAL,
    UNIQUE (latitude, longitude)                                               -- natural key
);

CREATE TABLE IF NOT EXISTS weather_codes (
    code        INTEGER PRIMARY KEY,
    description TEXT    NOT NULL
);

INSERT INTO weather_codes (code, description) VALUES
    (0,  'Clear sky'),
    (1,  'Mainly clear'),
    (2,  'Partly cloudy'),
    (3,  'Overcast'),
    (45, 'Fog'),
    (48, 'Depositing rime fog'),
    (51, 'Light drizzle'),
    (53, 'Moderate drizzle'),
    (55, 'Dense drizzle'),
    (56, 'Light freezing drizzle'),
    (57, 'Dense freezing drizzle'),
    (61, 'Slight rain'),
    (63, 'Moderate rain'),
    (65, 'Heavy rain'),
    (66, 'Light freezing rain'),
    (67, 'Heavy freezing rain'),
    (71, 'Slight snow fall'),
    (73, 'Moderate snow fall'),
    (75, 'Heavy snow fall'),
    (77, 'Snow grains'),
    (80, 'Slight rain showers'),
    (81, 'Moderate rain showers'),
    (82, 'Violent rain showers'),
    (85, 'Slight snow showers'),
    (86, 'Heavy snow showers'),
    (95, 'Thunderstorm'),
    (96, 'Thunderstorm with slight hail'),
    (99, 'Thunderstorm with heavy hail')
ON CONFLICT (code) DO UPDATE SET description = excluded.description;

CREATE TABLE IF NOT EXISTS hourly_weather (
    location_id             INTEGER NOT NULL REFERENCES locations (id) ON DELETE CASCADE,
    observed_at             TEXT    NOT NULL,  -- ISO-8601 UTC, e.g. 2024-05-01T13:00:00Z
    observed_date           TEXT    NOT NULL,  -- YYYY-MM-DD (UTC), derived
    observed_hour           INTEGER NOT NULL CHECK (observed_hour BETWEEN 0 AND 23),
    temperature_c           REAL    CHECK (temperature_c BETWEEN -90 AND 60),
    relative_humidity_pct   INTEGER CHECK (relative_humidity_pct BETWEEN 0 AND 100),
    precipitation_mm        REAL    CHECK (precipitation_mm >= 0),
    wind_speed_kmh          REAL    CHECK (wind_speed_kmh >= 0),
    wind_direction_deg      INTEGER CHECK (wind_direction_deg BETWEEN 0 AND 359),
    wind_direction_cardinal TEXT    CHECK (wind_direction_cardinal IN
        ('N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW')),
    weather_code            INTEGER REFERENCES weather_codes (code),
    PRIMARY KEY (location_id, observed_at)                                     -- natural key
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_hourly_weather_location_date
    ON hourly_weather (location_id, observed_date);

CREATE TABLE IF NOT EXISTS daily_weather (
    location_id            INTEGER NOT NULL REFERENCES locations (id) ON DELETE CASCADE,
    date                   TEXT    NOT NULL,
    temp_min_c             REAL,
    temp_max_c             REAL,
    temp_mean_c            REAL,
    precipitation_total_mm REAL,
    wind_speed_max_kmh     REAL,
    hours_observed         INTEGER NOT NULL CHECK (hours_observed BETWEEN 0 AND 24),
    is_complete            INTEGER NOT NULL CHECK (is_complete IN (0, 1)),
    PRIMARY KEY (location_id, date)
) WITHOUT ROWID;

CREATE VIEW IF NOT EXISTS v_hourly_weather AS
SELECT l.name AS location,
       h.*,
       wc.description AS weather_description
FROM hourly_weather h
JOIN locations l          ON l.id = h.location_id
LEFT JOIN weather_codes wc ON wc.code = h.weather_code;
