-- Primary watchlist: survivors of the periodic structural screen.
CREATE TABLE IF NOT EXISTS primary_watchlist (
    symbol         TEXT PRIMARY KEY,
    screened_date  TEXT NOT NULL,
    spot           REAL,
    avg_volume     REAL,
    near_oi        INTEGER,
    spread_pct     REAL,
    vrp            REAL,
    sector         TEXT,
    metrics_json   TEXT
);

-- Generic key/value job metadata (e.g. last_screen_date).
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
