# Subsystem A — Screening inversion design (2026-06-15)

> **Scope:** Pure backend/logic change inside the existing Python package. No
> new runtime dependencies, no `models.py`/`schema.sql` edits (new tables ship
> as numbered migrations per `docs/database-migrations.md`). This is the first
> of two subsystems; Subsystem B (storage migration + Cloudflare frontend) is
> specced separately and depends on the data model this subsystem stabilizes.

## Problem

Today the pipeline casts the wide net with Reddit first, then runs the
expensive Stage-2 hard gate per-candidate **every morning**:

```
mentions_daily (daily)          morning (weekday)
  ApeWisdom WSB top ~200    →    candidates = active watchlist + every
  → mentions table               new velocity-triggered ticker
  → velocity (3x / 7d)      →    per candidate: yfinance chain → GEX → vol
  → tickers (wsb_velocity)       → FULL Stage-2 hard gate → promote/alert
```

Two problems:

1. The heavy work (chain fetch, price fetch, structural screening) runs daily
   over a large, mostly-unqualified candidate set.
2. Reddit mention-velocity is computed over the entire ApeWisdom universe even
   though most of those names can never pass the hard gate.

## Goal

Invert the order so qualification happens **first** and only on a periodic
cadence, shrinking the daily workload and the Reddit-tracked set:

```
screen (every ~21 days)            mentions_daily (daily)        morning (weekday)
  ApeWisdom wide pull         →     ApeWisdom pull, persist   →   process SECONDARY
  (universe) ∪ incumbents           + velocity ONLY for           (active) watchlist:
  → per symbol: 1 chain +           PRIMARY members           →   GEX walls + regime,
    1 price fetch             →     → velocity (3x / 7d)           proximity/persistence,
  → STRUCTURAL screen             → trigger → promote to       →  above_50dma, earnings,
  → PRIMARY watchlist               SECONDARY watchlist            wall-break/cooldown
    (new table)                                              →    persistent put wall
                                                                  + proximity → Discord
```

- **Primary watchlist** — survivors of the periodic structural screen. The
  universe of names Reddit is allowed to track.
- **Secondary (active) watchlist** — the existing `watchlist` table
  (`status='active'`). Primary names that fire the WSB velocity trigger get
  promoted here. This is the list the morning job and the Subsystem-B frontend
  operate on.

## Decisions (locked)

1. **Universe source = ApeWisdom** (option a). No new data source. The `screen`
   job pulls a wider ApeWisdom slice than the daily job and unions it with
   current primary members so incumbents are re-evaluated.
2. **"volume" = a new average-daily-share-volume gate** (`min_avg_volume`),
   computed from yfinance daily history. This is genuinely new — there was no
   share-volume filter before, only option open interest.
3. **Slow/structural checks move to the periodic `screen`; fast/GEX checks stay
   daily.** The full hard gate is *not* re-verified every morning.
4. **`iv_rank` cannot gate the screen** (it needs ~252d of per-symbol
   `vol_stats` history that only exists for tracked names — chicken-and-egg).
   At screen time, "volatility" = **`vrp`** (current ATM IV − 20d realized vol),
   both computable from a single point-in-time fetch. `iv_rank` stays a **daily
   alert gate** in the morning job, exactly as it works today. Net effect:
   `iv_rank` moves from an entry gate to an alert gate; nothing regresses.
5. **`screen` is a separate, self-throttling CLI command + timer.** It checks a
   persisted `last_screen_date` and no-ops unless `primary_screen_interval_days`
   (default 21) has elapsed; `--force` overrides. Timer may safely fire often
   (e.g. weekly) without re-screening early.

## Components

### 1. New `screen` job — `jobs/screen.py`

`run(cfg, *, force=False) -> None`

1. `asof = today in cfg['timezone']` (zoneinfo, never bare `date.today()`).
2. Connect DB. Read `last_screen_date` from a new `app_state` key/value table.
   If `not force` and `asof - last_screen_date < primary_screen_interval_days`,
   log "screen: not due (last=…, interval=…)" and return.
3. Build the universe: `fetch_apewisdom(filter, screen_pages, asof)` symbols
   (wider than the daily `apewisdom_pages`) ∪ current `primary_watchlist`
   members. Reuse `MentionFetchError` handling; if the pull fails entirely,
   log ERROR and **abort without mutating** the primary list (do not wipe the
   existing primary on a transient API failure).
4. Per symbol (wrap in try/except — one failure never kills the run):
   - `spot, quotes = chain_source.fetch(symbol, asof, max_dte)`
   - `closes, volumes = prices.daily_closes_and_volumes(symbol)` (see below)
   - run `screening.primary.run_primary_screen(...)` (pure function, below).
   - record the screen result row.
5. Replace the primary watchlist with this run's survivors (transactional):
   upsert survivors as primary members with their measured metrics; demote
   names that were primary but failed now. Demoting a primary name that is also
   on the secondary/active `watchlist` sets that `watchlist` row
   `status='removed'` with note `"dropped from primary screen"` (this system
   tracks no open positions, so demotion is safe).
6. Persist `last_screen_date = asof`. Commit. One INFO summary line. Close.

### 2. New structural screen — `screening/primary.py`

`run_primary_screen(symbol, cfg, *, spot, quotes, closes, volumes, asof, sector) -> PrimaryScreenReport`

Pure function (no network, no DB) — unit-testable with synthetic data, mirroring
`screening/filters.py`. Checks (ALL must pass):

| check            | rule                                                                 |
|------------------|----------------------------------------------------------------------|
| `price_range`    | `price_min <= spot <= price_max`                                     |
| `avg_volume`     | mean of last `avg_volume_days` (default 20) share volumes ≥ `min_avg_volume` |
| `optionable_oi`  | sum OI on 3 nearest strikes (nearest expiry > 7 DTE) ≥ `min_open_interest` |
| `optionable_spread` | ATM call `spread_pct` ≤ `max_spread_pct`; `no_quote` → FAIL       |
| `volatility_vrp` | `vrp` (= ATM IV − rv20) ≥ `min_vrp`                                  |
| `sector`         | `sector` contains no `excluded_sectors` entry (case-insensitive)     |
| `not_blocklisted`| symbol not in `excluded_symbols`                                     |

The OI/spread/ATM-IV logic is the same arithmetic already in `filters.py`; the
shared helpers should be factored into a small internal module so both the
periodic screen and the daily filters use one implementation (avoid drift).
`PrimaryScreenReport` mirrors `FilterReport` shape (`symbol`, `passed`,
`checks`, `values`) — defined as a new dataclass in `models.py` **appended**
(adding a new dataclass does not rename existing fields, so the frozen contract
holds).

### 3. New table — `migrations/0002_primary_watchlist.sql`

```sql
CREATE TABLE IF NOT EXISTS primary_watchlist (
    symbol         TEXT PRIMARY KEY,
    screened_date  TEXT NOT NULL,      -- last screen that admitted this name
    spot           REAL,
    avg_volume     REAL,
    near_oi        INTEGER,
    spread_pct     REAL,
    vrp            REAL,
    sector         TEXT,
    metrics_json   TEXT                -- full PrimaryScreenReport.values for display/debug
);

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

`app_state` stores `last_screen_date` (extensible key/value for future job
metadata). Add `tests/test_db_migrations.py` coverage: open an older DB shape,
run `connect()`, assert prior data survives and the two new tables exist.

### 4. `data/prices.py` addition

`daily_closes_and_volumes(symbol, lookback_days=120) -> tuple[list[float], list[float]]`
returns oldest-first closes and volumes from one `history()` call (avoids a
second network round-trip in the screen). `daily_closes()` stays as-is for
existing callers (frozen signature). Add a tiny `avg_volume(volumes, window)`
helper alongside `sma()`.

### 5. `data/mentions.py` / `screening/discovery.py` change

The daily path narrows to the primary set:

- `mentions_daily.run()` → `run_discovery()` still calls `fetch_apewisdom`, but
  **filters records to `symbol IN primary_watchlist` before persisting and
  before computing velocity.** This is the "reduce the number of tickers tracked
  on Reddit" win — non-primary names are dropped immediately.
- A primary name whose velocity triggers is promoted to the secondary/active
  `watchlist` via `db.watchlist_add()` (this replaces the old `tickers`
  `wsb_velocity` upsert as the promotion mechanism). The `tickers` table is
  still maintained for sector/cooldown/exclusion metadata.
- If `primary_watchlist` is empty (system never screened yet), the daily job
  logs a warning and skips — the operator must run `screen --force` once to
  seed. (Documented in README + INSTALL.)

### 6. `jobs/morning.py` change

Candidates become **the active (secondary) watchlist only**. Remove the
discovery-promotion branch and the per-candidate structural hard gate from the
morning path. The morning job keeps: GEX profile + `regime`, put-wall
proximity/persistence, `above_50dma`, earnings blackout, `not_cooled_down` /
wall-break bench, scoring, and persisting the resulting trades to the `alerts`
table. `above_50dma` + `earnings` remain here (they are cheap and
time-sensitive) rather than in the periodic screen. The alert *delivery*
channel is handled in Subsystem B (Discord push is removed there in favor of
the dashboard) — A makes no delivery changes.

The weekly-prune logic in `_update_watchlist_membership` is superseded: a name
leaves the secondary list when it drops from the primary screen, gets benched on
a wall break, or fails the still-daily structural checks (`above_50dma`,
earnings). Simplify accordingly; keep the per-symbol try/except resilience.

### 7. Config additions — `config/config.example.yaml`

```yaml
screen:                          # NEW: periodic primary-watchlist screen
  primary_screen_interval_days: 21
  screen_pages: 5                # wider ApeWisdom pull than daily apewisdom_pages
  avg_volume_days: 20            # window for the average-daily-volume gate
  min_avg_volume: 1000000        # NEW gate: avg daily shares
```

The screen owns only the two genuinely new keys above (`avg_volume_days`,
`min_avg_volume`) plus its scheduling keys. For every other structural
threshold it **reads the existing `filters.*` values** (`price_min`,
`price_max`, `min_open_interest`, `max_spread_pct`, `min_vrp`,
`excluded_sectors`, `excluded_symbols`) so there is exactly one source of
truth and the screen can never disagree with the daily gate. `iv_rank`
(`filters.min_iv_rank`) stays a daily-only gate and is intentionally not read
by the screen (decision 4).

### 8. CLI + deploy

- `src/gexwheel/__main__.py`: add `screen` subcommand (`--force` flag).
- `deploy/`: add `gexwheel-screen.{container,timer}` (timer fires weekly; job
  self-throttles to 21d). Update `install.sh` and `deploy/INSTALL.md`.

## Data flow summary

```
universe (ApeWisdom wide ∪ incumbents)
   └─ screen (≤ every 21d) ─ structural gate ─▶ primary_watchlist
primary_watchlist
   └─ mentions_daily ─ velocity (3x/7d) ─▶ watchlist (status='active')   ← SECONDARY
watchlist(active)
   └─ morning ─ GEX/proximity/regime/earnings ─▶ alerts (the day's trades)
```

> **Cross-subsystem note:** the *output channel* is intentionally out of scope
> for Subsystem A. A keeps the morning job **computing and persisting** the
> day's trades into the `alerts` table (via scoring), but does not touch how
> they are delivered. Subsystem B removes the Discord push entirely and makes
> the Cloudflare dashboard the sole surface that reads the `alerts` rows. A's
> morning changes must therefore not add any new Discord coupling.

## Error handling

- All network code keeps stdlib `logging`, per-call timeouts, exponential
  backoff, and per-symbol try/except (one symbol never kills a run) — existing
  rules in `.cursor/rules/gexwheel-data-network.mdc`.
- `screen` aborts without mutating the primary list if the universe pull fails
  entirely (no destructive update on transient API failure).
- All dates via `zoneinfo.ZoneInfo(cfg['timezone'])`.
- SQLite single-writer; jobs serialized by timers; no threading.

## Testing

- `tests/test_primary_screen.py` — known-answer unit tests for each check
  (pass/fail edge cases), mirroring `tests/test_filters.py` fixtures.
- `tests/test_db_migrations.py` — `0002` migration adds tables, preserves data.
- `tests/test_screen_job.py` — seed an in-memory DB + fake chain/price sources;
  assert: not-due no-op, force re-screen, survivor upsert, incumbent demotion,
  transient-failure abort leaves primary intact.
- `tests/test_discovery.py` / `tests/test_mentions*` — extend to assert
  non-primary symbols are dropped before persistence/velocity.
- Full suite green: `PYTHONPATH=src pytest` after every step.

## Out of scope (this subsystem)

- Storage migration and the Cloudflare frontend (Subsystem B).
- Any change to GEX math, scoring, or Discord formatting beyond candidate
  sourcing.
- Position/PnL tracking (the system remains alert-only).
```
