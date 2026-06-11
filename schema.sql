-- gexwheel SQLite schema. Applied idempotently on every connect (db.py).
-- All dates stored as ISO-8601 TEXT in America/New_York trading-day terms.

CREATE TABLE IF NOT EXISTS tickers (
    symbol          TEXT PRIMARY KEY,
    added_date      TEXT NOT NULL,
    source          TEXT NOT NULL,            -- 'wsb_velocity' | 'manual' | 'iv_screen'
    sector          TEXT,
    conviction_tag  TEXT,                     -- user-assigned: 'core' | 'wheel' | 'watch'
    excluded        INTEGER NOT NULL DEFAULT 0,
    exclusion_reason TEXT,                    -- e.g. 'binary_catalyst_biotech'
    cooldown_until  TEXT                      -- benched until this date (gapped through wall)
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
    net_gex     REAL,                          -- dollar gamma per 1% move, signed
    regime      TEXT,                          -- 'positive' | 'negative'
    profile_json TEXT,                         -- {"strike": gex, ...} for charting/history
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS vol_stats (
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    iv_atm  REAL,                              -- ATM IV, ~30 DTE interpolated
    iv_rank REAL,                              -- 0-100 percentile vs trailing 252d of iv_atm
    rv20    REAL,                              -- 20d realized vol, annualized
    vrp     REAL,                              -- iv_atm - rv20
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol     TEXT PRIMARY KEY,
    date_added TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active', -- 'active' | 'benched' | 'removed'
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
    type         TEXT NOT NULL,                -- 'put_wall_entry' | 'regime_flip' | 'new_watchlist'
    payload_json TEXT NOT NULL,
    sent_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_mentions_symbol_date ON mentions(symbol, date);
CREATE INDEX IF NOT EXISTS idx_gex_symbol_date ON gex_snapshots(symbol, date);
