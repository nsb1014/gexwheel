-- Baseline schema for existing gexwheel databases.
-- Keep idempotent so databases previously initialized from schema.sql can record
-- this migration without changing their data.

CREATE TABLE IF NOT EXISTS tickers (
    symbol          TEXT PRIMARY KEY,
    added_date      TEXT NOT NULL,
    source          TEXT NOT NULL,
    sector          TEXT,
    conviction_tag  TEXT,
    excluded        INTEGER NOT NULL DEFAULT 0,
    exclusion_reason TEXT,
    cooldown_until  TEXT
);

CREATE TABLE IF NOT EXISTS mentions (
    symbol   TEXT NOT NULL,
    date     TEXT NOT NULL,
    source   TEXT NOT NULL DEFAULT 'apewisdom',
    mentions INTEGER NOT NULL,
    rank     INTEGER,
    upvotes  INTEGER,
    PRIMARY KEY (symbol, date, source)
);

CREATE TABLE IF NOT EXISTS gex_snapshots (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    spot        REAL NOT NULL,
    call_wall   REAL,
    put_wall    REAL,
    zero_gamma  REAL,
    net_gex     REAL,
    regime      TEXT,
    profile_json TEXT,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS vol_stats (
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    iv_atm  REAL,
    iv_rank REAL,
    rv20    REAL,
    vrp     REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol     TEXT PRIMARY KEY,
    date_added TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    last_score REAL,
    notes      TEXT
);

CREATE TABLE IF NOT EXISTS earnings (
    symbol             TEXT PRIMARY KEY,
    next_earnings_date TEXT,
    updated_at         TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    date         TEXT NOT NULL,
    type         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sent_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_mentions_symbol_date ON mentions(symbol, date);
CREATE INDEX IF NOT EXISTS idx_gex_symbol_date ON gex_snapshots(symbol, date);
