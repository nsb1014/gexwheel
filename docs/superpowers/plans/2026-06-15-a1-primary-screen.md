# Plan A1 — Primary watchlist screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a periodic, self-throttling `screen` job that builds a pre-qualified **primary watchlist** from the ApeWisdom universe using a structural gate (price band, average daily volume, optionable OI+spread, VRP volatility, sector, blocklist), persisted in a new `primary_watchlist` table.

**Architecture:** A new pure-function module `screening/primary.py` does the gating (no network/DB), reusing shared chain-metric helpers factored out of `screening/filters.py` so the two gates never drift. A new `jobs/screen.py` orchestrates fetch → screen → persist, self-throttling on `app_state.last_screen_date`. New tables ship as migration `0002`. `models.py` field names and `schema.sql` stay frozen; the new dataclass is appended.

**Tech Stack:** Python stdlib, `sqlite3` in-memory via `gexwheel.db.connect(":memory:")`, `yfinance` (already a dep), pytest.

**Spec:** `docs/superpowers/specs/2026-06-15-screening-inversion-design.md`

---

## Background

Today Reddit casts the wide net first and the hard gate runs per-candidate every morning (`jobs/morning.py`). This plan inverts that: a periodic structural screen produces a small primary watchlist. Plan A2 then narrows Reddit tracking to that list and simplifies the morning job. Plans B1/B2 handle Discord removal and cloud hosting.

The structural-screen checks reuse existing `filters.py` arithmetic. To avoid two copies, Task 3 extracts the OI/spread/expiry helpers into `screening/chain_metrics.py` and rewires `filters.py` to call them (behavior-preserving — `tests/test_filters.py` must stay green).

`iv_rank` is intentionally NOT part of the screen (it needs ~252d of per-symbol history that can't exist for a fresh universe). The screen's volatility gate is `vrp = atm_iv − realized_vol`, both computable from a single point-in-time fetch (`analytics/vol.py`).

---

## File structure

- Create: `migrations/0002_primary_watchlist.sql` — `primary_watchlist` + `app_state` tables.
- Create: `src/gexwheel/screening/chain_metrics.py` — shared `eligible_expiries`, `near_oi_sum`, `atm_call_spread`.
- Create: `src/gexwheel/screening/primary.py` — `run_primary_screen()` pure function.
- Create: `src/gexwheel/jobs/screen.py` — the `screen` job.
- Modify: `src/gexwheel/models.py` — append `PrimaryScreenReport` dataclass (no existing field renames).
- Modify: `src/gexwheel/data/prices.py` — add `daily_closes_and_volumes()` + `avg_volume()`.
- Modify: `src/gexwheel/db.py` — add `app_state` + `primary_watchlist` helpers.
- Modify: `src/gexwheel/screening/filters.py` — use `chain_metrics` helpers.
- Modify: `src/gexwheel/config.py` — add `"screen"` to `REQUIRED_KEYS`.
- Modify: `config/config.example.yaml` — add the `screen:` block.
- Modify: `src/gexwheel/__main__.py` — add the `screen` subcommand (`--force`).
- Tests: `tests/test_db_migrations.py` (extend), `tests/test_prices_volume.py`, `tests/test_chain_metrics.py`, `tests/test_primary_screen.py`, `tests/test_db_primary.py`, `tests/test_screen_job.py`.

---

### Task 1: Migration 0002 — `primary_watchlist` + `app_state`

**Files:**
- Create: `migrations/0002_primary_watchlist.sql`
- Test: `tests/test_db_migrations.py` (add one test)

- [ ] **Step 1: Write the migration**

Create `migrations/0002_primary_watchlist.sql` (no `BEGIN`/`COMMIT` — the runner wraps it; keep idempotent):

```sql
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
```

- [ ] **Step 2: Write the failing test**

Add to `tests/test_db_migrations.py`:

```python
def test_migration_0002_adds_primary_tables_and_preserves_data(tmp_path):
    db_path = tmp_path / "existing.db"
    seed = sqlite3.connect(db_path)
    seed.execute(
        """CREATE TABLE watchlist (
               symbol TEXT PRIMARY KEY, date_added TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'active', last_score REAL, notes TEXT)"""
    )
    seed.execute("INSERT INTO watchlist(symbol, date_added) VALUES ('KEEP', '2026-01-01')")
    seed.commit()
    seed.close()

    conn = gdb.connect(str(db_path))

    assert conn.execute(
        "SELECT symbol FROM watchlist WHERE symbol='KEEP'"
    ).fetchone()["symbol"] == "KEEP"
    tables = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"primary_watchlist", "app_state"} <= tables
    versions = [
        r["version"]
        for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    ]
    assert "0002_primary_watchlist" in versions
```

- [ ] **Step 3: Run test to verify it fails, then passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_db_migrations.py -v`
Expected: the new test fails before the migration file exists / passes once it does. (If you wrote the file in Step 1, it should pass now; if it fails, the migration filename must be exactly `0002_primary_watchlist.sql`.)

- [ ] **Step 4: Commit**

```bash
git add migrations/0002_primary_watchlist.sql tests/test_db_migrations.py
git commit -m "feat(db): migration 0002 adds primary_watchlist and app_state tables"
```

---

### Task 2: `prices.daily_closes_and_volumes()` + `avg_volume()`

**Files:**
- Modify: `src/gexwheel/data/prices.py`
- Test: `tests/test_prices_volume.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_prices_volume.py`:

```python
"""avg_volume helper (pure) and the closes+volumes shape."""
from __future__ import annotations

import pytest

from gexwheel.data.prices import avg_volume


def test_avg_volume_last_window_only():
    vols = [100.0] * 10 + [200.0] * 20
    assert avg_volume(vols, 20) == 200.0


def test_avg_volume_raises_when_short():
    with pytest.raises(ValueError):
        avg_volume([1.0, 2.0], 20)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_prices_volume.py -v`
Expected: FAIL with `ImportError: cannot import name 'avg_volume'`.

- [ ] **Step 3: Implement the helpers**

In `src/gexwheel/data/prices.py`, add `avg_volume()` next to `sma()`:

```python
def avg_volume(volumes: list[float], window: int) -> float:
    """Mean of the last `window` daily share volumes (ValueError if short)."""
    if len(volumes) < window:
        raise ValueError(f"need >= {window} volumes for avg_volume, got {len(volumes)}")
    tail = volumes[-window:]
    return sum(tail) / len(tail)
```

And add `daily_closes_and_volumes()` after `daily_closes()` (single network call, oldest-first, drop NaN-by-position alignment):

```python
def daily_closes_and_volumes(
    symbol: str, lookback_days: int = 120
) -> tuple[list[float], list[float]]:
    """Oldest-first (closes, volumes) from one history() call.

    Rows with a NaN close are dropped (and their volume with them) so the two
    lists stay index-aligned. Raises PriceFetchError if < 60 usable closes.
    """
    try:
        ticker = yf.Ticker(symbol)
        hist = _history_with_retry(ticker, f"{lookback_days}d")
    except Exception as exc:
        raise PriceFetchError(f"price history failed for {symbol}: {exc}") from exc

    if hist is None or hist.empty:
        raise PriceFetchError(f"no price history for {symbol}")

    closes: list[float] = []
    volumes: list[float] = []
    for close, vol in zip(hist["Close"], hist["Volume"]):
        if close != close:  # NaN check without importing math
            continue
        closes.append(float(close))
        volumes.append(0.0 if (vol != vol) else float(vol))

    if len(closes) < 60:
        raise PriceFetchError(f"{symbol}: only {len(closes)} closes, need >= 60")
    return closes, volumes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_prices_volume.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gexwheel/data/prices.py tests/test_prices_volume.py
git commit -m "feat(prices): add daily_closes_and_volumes and avg_volume helpers"
```

---

### Task 3: Extract shared chain-metric helpers; rewire `filters.py`

**Files:**
- Create: `src/gexwheel/screening/chain_metrics.py`
- Modify: `src/gexwheel/screening/filters.py` (the `open_interest` + `spread` blocks)
- Test: `tests/test_chain_metrics.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_chain_metrics.py`:

```python
"""Shared chain-metric helpers used by both filters and the primary screen."""
from __future__ import annotations

from datetime import date, timedelta

from gexwheel.models import OptionQuote
from gexwheel.screening.chain_metrics import (
    atm_call_spread,
    eligible_expiries,
    near_oi_sum,
)

ASOF = date(2026, 6, 10)
EXP = ASOF + timedelta(days=30)
SOON = ASOF + timedelta(days=3)  # <= 7 DTE -> ineligible


def _q(strike, kind, oi, bid=0.95, ask=1.00, exp=EXP):
    return OptionQuote("T", strike, exp, kind, oi, 0.5, bid, ask)


def test_eligible_expiries_excludes_le_7_dte():
    quotes = [_q(100, "C", 10, exp=SOON), _q(100, "C", 10, exp=EXP)]
    assert eligible_expiries(quotes, ASOF) == [EXP]


def test_near_oi_sum_uses_three_nearest_strikes_nearest_expiry():
    quotes = [
        _q(90, "C", 100), _q(95, "C", 100), _q(100, "C", 100),
        _q(105, "C", 100), _q(200, "C", 9999),  # far strike excluded
        _q(100, "P", 100), _q(95, "P", 100), _q(90, "P", 100),
    ]
    # 3 strikes nearest spot=100 are {100, 95, 105 or 90}; sums calls+puts on them
    assert near_oi_sum(quotes, 100.0, ASOF) == 100 * 6  # 3 strikes x (some C + some P)


def test_atm_call_spread_no_quote_when_bid_ask_zero():
    quotes = [_q(100, "C", 100, bid=0.0, ask=0.0)]
    sp, status = atm_call_spread(quotes, 100.0, ASOF)
    assert status == "no_quote" and sp is None


def test_atm_call_spread_ok():
    quotes = [_q(100, "C", 100, bid=0.90, ask=1.10)]
    sp, status = atm_call_spread(quotes, 100.0, ASOF)
    assert status == "ok"
    assert abs(sp - 0.2) < 1e-9  # (1.10-0.90)/1.0
```

> Note: `near_oi_sum`'s exact total depends on which 3 strikes tie as "nearest"; the test seeds equal OI (100) on all near strikes so the assertion is robust regardless of tie-breaking. Confirm the count matches the 3-nearest-strike rule when you implement.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_chain_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: ... chain_metrics`.

- [ ] **Step 3: Implement `chain_metrics.py`**

Create `src/gexwheel/screening/chain_metrics.py` (lifts the exact logic from `filters.py` lines 67-98):

```python
"""Shared option-chain metric helpers.

Single implementation used by both screening.filters (daily gate) and
screening.primary (periodic screen) so the two can never disagree.
"""
from __future__ import annotations

from datetime import date

from ..models import OptionQuote

_MIN_DTE = 7
_N_STRIKES = 3


def eligible_expiries(quotes: list[OptionQuote], asof: date,
                      min_dte: int = _MIN_DTE) -> list[date]:
    """Expiries with DTE > min_dte, sorted nearest-first."""
    return sorted(
        {q.expiry for q in quotes if (q.expiry - asof).days > min_dte},
        key=lambda e: (e - asof).days,
    )


def near_oi_sum(quotes: list[OptionQuote], spot: float, asof: date,
                n_strikes: int = _N_STRIKES) -> int:
    """Sum OI (calls+puts) over the `n_strikes` strikes nearest spot on the
    nearest eligible expiry. 0 if no eligible expiry."""
    exps = eligible_expiries(quotes, asof)
    if not exps:
        return 0
    near = [q for q in quotes if q.expiry == exps[0]]
    strikes_sorted = sorted({q.strike for q in near}, key=lambda s: abs(s - spot))
    top = set(strikes_sorted[:n_strikes])
    return sum(q.open_interest for q in near if q.strike in top)


def atm_call_spread(quotes: list[OptionQuote], spot: float, asof: date
                    ) -> tuple[float | None, str]:
    """Return (spread_pct, status). status is 'no_quote' when there is no usable
    ATM call (none on the nearest eligible expiry, or bid==ask==0), else 'ok'."""
    exps = eligible_expiries(quotes, asof)
    if not exps:
        return None, "no_quote"
    near_calls = [q for q in quotes if q.expiry == exps[0] and q.kind == "C"]
    if not near_calls:
        return None, "no_quote"
    atm = min(near_calls, key=lambda q: abs(q.strike - spot))
    if atm.bid == 0 and atm.ask == 0:
        return None, "no_quote"
    return atm.spread_pct, "ok"
```

- [ ] **Step 4: Rewire `filters.py` to use the helpers (behavior-preserving)**

In `src/gexwheel/screening/filters.py`, add the import near the top:

```python
from .chain_metrics import atm_call_spread, eligible_expiries, near_oi_sum
```

Replace the `open_interest` block (currently lines 67-81) with:

```python
    # --- open_interest: sum OI on 3 nearest strikes, nearest expiry > 7 DTE ---
    total_oi = near_oi_sum(quotes, spot, asof)
    checks["open_interest"] = total_oi >= f["min_open_interest"]
    values["near_oi"] = total_oi
```

Replace the `spread` block (currently lines 83-98) with:

```python
    # --- spread: ATM call on nearest expiry > 7 DTE ---
    sp, status = atm_call_spread(quotes, spot, asof)
    if status == "no_quote":
        checks["spread"] = False
        values["spread"] = "no_quote"
    else:
        checks["spread"] = sp <= f["max_spread_pct"]
        values["spread"] = round(sp, 4)
```

The now-unused local `eligible_expiries`/`atm_call` computations are removed by these replacements. (`eligible_expiries` is imported but only used transitively; if a linter flags it as unused, drop it from the import.)

- [ ] **Step 5: Run the chain-metrics tests AND the existing filter tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_chain_metrics.py tests/test_filters.py -v`
Expected: all PASS (filters behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/gexwheel/screening/chain_metrics.py src/gexwheel/screening/filters.py tests/test_chain_metrics.py
git commit -m "refactor(screening): extract shared chain-metric helpers; filters reuse them"
```

---

### Task 4: `PrimaryScreenReport` + `screening/primary.py`

**Files:**
- Modify: `src/gexwheel/models.py` (append dataclass)
- Create: `src/gexwheel/screening/primary.py`
- Test: `tests/test_primary_screen.py` (create)

- [ ] **Step 1: Append the dataclass**

At the END of `src/gexwheel/models.py` (do not touch existing dataclasses):

```python
@dataclass
class PrimaryScreenReport:
    """Periodic structural-screen result. `passed` only True if every check is True."""
    symbol: str
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    values: dict[str, float | str | None] = field(default_factory=dict)
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_primary_screen.py`:

```python
"""Primary structural screen (pure function)."""
from __future__ import annotations

from datetime import date, timedelta

from gexwheel.models import OptionQuote
from gexwheel.screening.primary import run_primary_screen

ASOF = date(2026, 6, 10)
EXP = ASOF + timedelta(days=30)
SPOT = 20.0

CFG = {
    "filters": {
        "price_min": 10.0, "price_max": 45.0,
        "min_open_interest": 500, "max_spread_pct": 0.10,
        "min_vrp": 0.0,
        "excluded_sectors": ["Biotechnology"],
        "excluded_symbols": [],
    },
    "screen": {"avg_volume_days": 20, "min_avg_volume": 1_000_000},
}


def _quotes(oi=2000, bid=0.95, ask=1.00):
    # high IV (0.80) so vrp = iv - rv is comfortably positive
    return [
        OptionQuote("TEST", SPOT, EXP, "C", oi, 0.80, bid, ask),
        OptionQuote("TEST", SPOT, EXP, "P", oi, 0.80, bid, ask),
    ]


def _closes():
    # ~flat series -> low realized vol so vrp stays positive
    return [SPOT for _ in range(61)]


def _volumes(v=2_000_000):
    return [float(v) for _ in range(61)]


def test_clean_setup_passes_all():
    rep = run_primary_screen(
        "TEST", CFG, spot=SPOT, quotes=_quotes(), closes=_closes(),
        volumes=_volumes(), asof=ASOF, sector="Industrials",
    )
    assert rep.passed, rep.checks
    assert set(rep.checks) == {
        "price_range", "avg_volume", "optionable_oi",
        "optionable_spread", "volatility_vrp", "sector", "not_blocklisted",
    }


def test_low_volume_fails_only_volume():
    rep = run_primary_screen(
        "TEST", CFG, spot=SPOT, quotes=_quotes(), closes=_closes(),
        volumes=_volumes(100_000), asof=ASOF, sector="Industrials",
    )
    assert not rep.passed
    assert rep.checks["avg_volume"] is False
    assert rep.checks["price_range"] is True


def test_biotech_sector_substring_fails():
    rep = run_primary_screen(
        "TEST", CFG, spot=SPOT, quotes=_quotes(), closes=_closes(),
        volumes=_volumes(), asof=ASOF, sector="Biotechnology - Gene Editing",
    )
    assert not rep.passed
    assert rep.checks["sector"] is False


def test_no_quote_spread_fails():
    rep = run_primary_screen(
        "TEST", CFG, spot=SPOT, quotes=_quotes(bid=0.0, ask=0.0),
        closes=_closes(), volumes=_volumes(), asof=ASOF, sector="Industrials",
    )
    assert not rep.passed
    assert rep.checks["optionable_spread"] is False
    assert rep.values["spread"] == "no_quote"


def test_blocklisted_symbol_fails():
    cfg = {**CFG, "filters": {**CFG["filters"], "excluded_symbols": ["TEST"]}}
    rep = run_primary_screen(
        "TEST", cfg, spot=SPOT, quotes=_quotes(), closes=_closes(),
        volumes=_volumes(), asof=ASOF, sector="Industrials",
    )
    assert not rep.passed
    assert rep.checks["not_blocklisted"] is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_primary_screen.py -v`
Expected: FAIL with `ModuleNotFoundError: ... primary`.

- [ ] **Step 4: Implement `primary.py`**

Create `src/gexwheel/screening/primary.py`:

```python
"""Periodic structural screen -> primary watchlist.

Pure function (no network, no DB) so it is unit-testable with synthetic data,
mirroring screening.filters. Reuses shared chain-metric helpers and the vol
module. iv_rank is intentionally NOT screened here (it needs per-symbol history
that cannot exist for a fresh universe); volatility is gated on vrp instead.
See docs/superpowers/specs/2026-06-15-screening-inversion-design.md.
"""
from __future__ import annotations

from datetime import date

from ..analytics.vol import atm_iv, realized_vol
from ..data.prices import avg_volume
from ..models import OptionQuote, PrimaryScreenReport
from .chain_metrics import atm_call_spread, near_oi_sum


def run_primary_screen(symbol: str, cfg: dict, *, spot: float,
                       quotes: list[OptionQuote], closes: list[float],
                       volumes: list[float], asof: date,
                       sector: str | None) -> PrimaryScreenReport:
    """All checks must pass; no short-circuit so callers see the full picture."""
    f = cfg["filters"]
    s = cfg.get("screen", {})
    checks: dict[str, bool] = {}
    values: dict[str, object] = {}

    # price_range
    checks["price_range"] = f["price_min"] <= spot <= f["price_max"]
    values["spot"] = spot

    # avg_volume (NEW gate)
    window = s.get("avg_volume_days", 20)
    min_vol = s.get("min_avg_volume", 0)
    try:
        av = avg_volume(volumes, window)
        checks["avg_volume"] = av >= min_vol
        values["avg_volume"] = round(av, 1)
    except ValueError:
        checks["avg_volume"] = False
        values["avg_volume"] = None

    # optionable: OI on 3 nearest strikes
    oi = near_oi_sum(quotes, spot, asof)
    checks["optionable_oi"] = oi >= f["min_open_interest"]
    values["near_oi"] = oi

    # optionable: ATM call spread
    sp, status = atm_call_spread(quotes, spot, asof)
    if status == "no_quote":
        checks["optionable_spread"] = False
        values["spread"] = "no_quote"
    else:
        checks["optionable_spread"] = sp <= f["max_spread_pct"]
        values["spread"] = round(sp, 4)

    # volatility: vrp = atm_iv - realized_vol
    iv = atm_iv(quotes, spot, asof)
    try:
        rv = realized_vol(closes)
    except ValueError:
        rv = None
    vrp = (iv - rv) if (iv is not None and rv is not None) else None
    checks["volatility_vrp"] = vrp is not None and vrp >= f["min_vrp"]
    values["vrp"] = round(vrp, 4) if vrp is not None else None

    # sector exclusion (case-insensitive substring)
    excluded_sectors = [x.lower() for x in f.get("excluded_sectors", [])]
    if sector:
        sector_fail = any(excl in sector.lower() for excl in excluded_sectors)
    else:
        sector_fail = False
    checks["sector"] = not sector_fail
    values["sector"] = sector or "unknown"

    # blocklist
    checks["not_blocklisted"] = symbol not in f.get("excluded_symbols", [])

    passed = all(checks.values())
    return PrimaryScreenReport(symbol=symbol, passed=passed, checks=checks, values=values)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_primary_screen.py -v`
Expected: 5 PASS. (If `volatility_vrp` fails in the clean test, confirm `_quotes` IV 0.80 minus the realized vol of a flat series 0.0 ≥ min_vrp 0.0.)

- [ ] **Step 6: Commit**

```bash
git add src/gexwheel/models.py src/gexwheel/screening/primary.py tests/test_primary_screen.py
git commit -m "feat(screening): primary structural screen (vrp volatility, new avg-volume gate)"
```

---

### Task 5: DB helpers for `app_state` + `primary_watchlist`

**Files:**
- Modify: `src/gexwheel/db.py`
- Test: `tests/test_db_primary.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_primary.py`:

```python
"""primary_watchlist + app_state DB helpers."""
from __future__ import annotations

from datetime import date

from gexwheel import db as gdb

ASOF = date(2026, 6, 10)


def test_app_state_roundtrip():
    conn = gdb.connect(":memory:")
    assert gdb.get_app_state(conn, "last_screen_date") is None
    gdb.set_app_state(conn, "last_screen_date", ASOF.isoformat())
    assert gdb.get_app_state(conn, "last_screen_date") == ASOF.isoformat()
    # upsert overwrites
    gdb.set_app_state(conn, "last_screen_date", "2026-07-01")
    assert gdb.get_app_state(conn, "last_screen_date") == "2026-07-01"


def test_upsert_and_list_primary():
    conn = gdb.connect(":memory:")
    gdb.upsert_primary(conn, "AAA", ASOF, metrics={"spot": 20.0, "avg_volume": 2e6,
                       "near_oi": 5000, "spread": 0.02, "vrp": 0.3, "sector": "Tech"})
    gdb.upsert_primary(conn, "BBB", ASOF, metrics={"spot": 15.0})
    assert set(gdb.primary_symbols(conn)) == {"AAA", "BBB"}
    row = conn.execute("SELECT spot, avg_volume, sector FROM primary_watchlist WHERE symbol='AAA'").fetchone()
    assert row["spot"] == 20.0 and row["avg_volume"] == 2e6 and row["sector"] == "Tech"


def test_delete_primary():
    conn = gdb.connect(":memory:")
    gdb.upsert_primary(conn, "AAA", ASOF, metrics={"spot": 20.0})
    gdb.delete_primary(conn, "AAA")
    assert gdb.primary_symbols(conn) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_db_primary.py -v`
Expected: FAIL with `AttributeError: module 'gexwheel.db' has no attribute 'get_app_state'`.

- [ ] **Step 3: Implement the helpers**

Append to `src/gexwheel/db.py` (after the existing helpers; `import json` already present at top):

```python
# ---------- app_state ----------

def get_app_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_app_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO app_state(key, value) VALUES (?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value),
    )


# ---------- primary watchlist ----------

def primary_symbols(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT symbol FROM primary_watchlist ORDER BY symbol").fetchall()
    return [r["symbol"] for r in rows]


def upsert_primary(conn: sqlite3.Connection, symbol: str, screened: date, *,
                   metrics: dict) -> None:
    """Insert/refresh a primary-watchlist row. `metrics` keys are optional;
    unknown keys are ignored, the full dict is stored as metrics_json."""
    conn.execute(
        """INSERT INTO primary_watchlist(symbol, screened_date, spot, avg_volume,
               near_oi, spread_pct, vrp, sector, metrics_json)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(symbol) DO UPDATE SET
               screened_date=excluded.screened_date, spot=excluded.spot,
               avg_volume=excluded.avg_volume, near_oi=excluded.near_oi,
               spread_pct=excluded.spread_pct, vrp=excluded.vrp,
               sector=excluded.sector, metrics_json=excluded.metrics_json""",
        (symbol, screened.isoformat(), metrics.get("spot"), metrics.get("avg_volume"),
         metrics.get("near_oi"), metrics.get("spread"), metrics.get("vrp"),
         metrics.get("sector"), json.dumps(metrics)),
    )


def delete_primary(conn: sqlite3.Connection, symbol: str) -> None:
    conn.execute("DELETE FROM primary_watchlist WHERE symbol=?", (symbol,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_db_primary.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gexwheel/db.py tests/test_db_primary.py
git commit -m "feat(db): app_state and primary_watchlist helpers"
```

---

### Task 6: Config `screen` block + `screen` CLI + `jobs/screen.py`

**Files:**
- Modify: `config/config.example.yaml`, `src/gexwheel/config.py`, `src/gexwheel/__main__.py`
- Create: `src/gexwheel/jobs/screen.py`
- Test: `tests/test_screen_job.py` (create)

- [ ] **Step 1: Add config + REQUIRED_KEYS**

In `config/config.example.yaml`, add after the `discovery:` block:

```yaml
screen:                          # periodic primary-watchlist screen
  primary_screen_interval_days: 21
  screen_pages: 5                # wider ApeWisdom pull than daily apewisdom_pages
  avg_volume_days: 20            # window for the average-daily-volume gate
  min_avg_volume: 1000000        # NEW gate: avg daily shares
```

In `src/gexwheel/config.py`, add `"screen"` to `REQUIRED_KEYS`:

```python
REQUIRED_KEYS = ["db_path", "discord", "data", "reddit", "discovery", "screen", "filters", "alerts"]
```

> NOTE: `"discord"` is still listed here — Plan B1 removes it. Do not remove it in this plan or `tests/` for Discord will break out of order.

- [ ] **Step 2: Write the failing test**

Create `tests/test_screen_job.py`:

```python
"""screen job: throttling, survivor persistence, incumbent demotion, safe-abort."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from gexwheel import db as gdb
from gexwheel.data.mentions import MentionFetchError
from gexwheel.jobs import screen as screen_job
from gexwheel.models import MentionRecord, OptionQuote

ASOF = date(2026, 6, 10)
EXP = ASOF + timedelta(days=30)


def _cfg(tmp_path):
    return {
        "db_path": str(tmp_path / "g.db"),
        "timezone": "America/New_York",
        "data": {"chain_source": "yfinance", "max_dte": 60,
                 "request_sleep_s": 0, "request_retries": 1},
        "reddit": {"source": "apewisdom", "apewisdom_filter": "wallstreetbets",
                   "apewisdom_pages": 2},
        "screen": {"primary_screen_interval_days": 21, "screen_pages": 5,
                   "avg_volume_days": 20, "min_avg_volume": 1_000_000},
        "filters": {"price_min": 10.0, "price_max": 45.0, "min_open_interest": 500,
                    "max_spread_pct": 0.10, "min_vrp": 0.0,
                    "excluded_sectors": ["Biotechnology"], "excluded_symbols": []},
    }


def _good_chain(symbol="AAA", spot=20.0):
    quotes = [
        OptionQuote(symbol, spot, EXP, "C", 2000, 0.80, 0.95, 1.00),
        OptionQuote(symbol, spot, EXP, "P", 2000, 0.80, 0.95, 1.00),
    ]
    return spot, quotes


class _FakeChain:
    def fetch(self, symbol, asof, max_dte):
        return _good_chain(symbol)


def _patches(universe_symbols, prices_ok=True):
    """Patch the external IO the screen job depends on."""
    records = [MentionRecord(s, ASOF, 100, source="apewisdom") for s in universe_symbols]
    closes = [20.0] * 61
    volumes = [2_000_000.0] * 61
    return (
        patch("gexwheel.jobs.screen.fetch_apewisdom", return_value=records),
        patch("gexwheel.jobs.screen.make_chain_source", return_value=_FakeChain()),
        patch("gexwheel.jobs.screen.daily_closes_and_volumes",
              return_value=(closes, volumes)),
        patch("gexwheel.jobs.screen.sector", return_value="Industrials"),
    )


def test_screen_persists_survivors(tmp_path):
    cfg = _cfg(tmp_path)
    p1, p2, p3, p4 = _patches(["AAA", "BBB"])
    with p1, p2, p3, p4:
        screen_job.run(cfg, force=True)
    conn = gdb.connect(cfg["db_path"])
    assert set(gdb.primary_symbols(conn)) == {"AAA", "BBB"}
    assert gdb.get_app_state(conn, "last_screen_date") == ASOF.isoformat()


def test_screen_not_due_is_noop(tmp_path):
    cfg = _cfg(tmp_path)
    conn = gdb.connect(cfg["db_path"])
    gdb.set_app_state(conn, "last_screen_date", ASOF.isoformat())
    conn.commit()
    conn.close()
    p1, p2, p3, p4 = _patches(["AAA"])
    with p1 as m_fetch, p2, p3, p4:
        # asof passed explicitly so "now" is deterministic; 5 days later < 21
        screen_job.run(cfg, force=False, asof=ASOF + timedelta(days=5))
        m_fetch.assert_not_called()


def test_screen_demotes_incumbent_dropped_from_primary(tmp_path):
    cfg = _cfg(tmp_path)
    conn = gdb.connect(cfg["db_path"])
    # OLD is a current primary member AND active on the secondary watchlist
    gdb.upsert_primary(conn, "OLD", ASOF - timedelta(days=21), metrics={"spot": 20.0})
    gdb.watchlist_add(conn, "OLD", ASOF - timedelta(days=21))
    conn.commit()
    conn.close()
    # New universe no longer contains OLD
    p1, p2, p3, p4 = _patches(["AAA"])
    with p1, p2, p3, p4:
        screen_job.run(cfg, force=True)
    conn = gdb.connect(cfg["db_path"])
    assert "OLD" not in gdb.primary_symbols(conn)
    status = conn.execute("SELECT status FROM watchlist WHERE symbol='OLD'").fetchone()["status"]
    assert status == "removed"


def test_screen_aborts_without_wiping_on_fetch_failure(tmp_path):
    cfg = _cfg(tmp_path)
    conn = gdb.connect(cfg["db_path"])
    gdb.upsert_primary(conn, "KEEP", ASOF - timedelta(days=21), metrics={"spot": 20.0})
    conn.commit()
    conn.close()
    with patch("gexwheel.jobs.screen.fetch_apewisdom",
               side_effect=MentionFetchError("down")):
        screen_job.run(cfg, force=True)
    conn = gdb.connect(cfg["db_path"])
    assert gdb.primary_symbols(conn) == ["KEEP"]  # untouched
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_screen_job.py -v`
Expected: FAIL with `ModuleNotFoundError: ... jobs.screen`.

- [ ] **Step 4: Implement `jobs/screen.py`**

Create `src/gexwheel/jobs/screen.py`:

```python
"""Periodic primary-watchlist screen.

run(cfg, *, force=False, asof=None) -> None
  1. asof = asof or today in cfg['timezone'].
  2. Self-throttle: if not force and (asof - last_screen_date) <
     primary_screen_interval_days, log and return.
  3. Universe = ApeWisdom (screen_pages) symbols UNION current primary members.
     If the universe pull fails entirely, log ERROR and ABORT without mutating
     the primary list (no destructive update on a transient API failure).
  4. Per symbol (try/except — one failure never kills the run): one chain fetch
     + one price fetch, run run_primary_screen, collect survivors.
  5. Replace primary watchlist with survivors: upsert survivors; delete prior
     members no longer surviving and demote them from the active watchlist.
  6. Persist last_screen_date=asof. Commit. One INFO summary line.

Logging: stdlib logging, INFO to stdout.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import db as gdb
from ..data.chains import make_chain_source
from ..data.mentions import MentionFetchError, fetch_apewisdom
from ..data.prices import daily_closes_and_volumes, sector
from ..screening.primary import run_primary_screen

log = logging.getLogger(__name__)


def run(cfg: dict, *, force: bool = False, asof=None) -> None:
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    if asof is None:
        asof = datetime.now(tz).date()
    s_cfg = cfg["screen"]
    interval = s_cfg.get("primary_screen_interval_days", 21)

    conn = gdb.connect(cfg["db_path"])
    try:
        last = gdb.get_app_state(conn, "last_screen_date")
        if not force and last:
            try:
                last_date = datetime.strptime(last, "%Y-%m-%d").date()
                if (asof - last_date).days < interval:
                    log.info("screen: not due (last=%s, interval=%dd) — skipping", last, interval)
                    return
            except ValueError:
                pass  # unparseable -> screen anyway

        # --- universe ---
        try:
            records = fetch_apewisdom(
                cfg["reddit"].get("apewisdom_filter", "wallstreetbets"),
                s_cfg.get("screen_pages", 5),
                asof,
            )
        except MentionFetchError as exc:
            log.error("screen: universe pull failed (%s) — aborting without changes", exc)
            return

        universe = {r.symbol for r in records} | set(gdb.primary_symbols(conn))
        if not universe:
            log.warning("screen: empty universe for %s — aborting", asof)
            return

        chain_src = make_chain_source(cfg)
        data_cfg = cfg["data"]
        survivors: set[str] = set()

        for symbol in sorted(universe):
            try:
                spot, quotes = chain_src.fetch(symbol, asof, data_cfg["max_dte"])
                closes, volumes = daily_closes_and_volumes(symbol)
                try:
                    sec = sector(symbol)
                except Exception:
                    sec = None
                report = run_primary_screen(
                    symbol, cfg, spot=spot, quotes=quotes, closes=closes,
                    volumes=volumes, asof=asof, sector=sec,
                )
                if report.passed:
                    survivors.add(symbol)
                    gdb.upsert_primary(conn, symbol, asof, metrics=report.values)
            except Exception as exc:
                log.error("screen: error for %s: %s", symbol, exc, exc_info=True)

        # --- demote prior members that did not survive ---
        prior = set(gdb.primary_symbols(conn))
        dropped = prior - survivors
        for symbol in dropped:
            gdb.delete_primary(conn, symbol)
            conn.execute(
                "UPDATE watchlist SET status='removed', notes=? WHERE symbol=? AND status!='removed'",
                ("dropped from primary screen", symbol),
            )

        gdb.set_app_state(conn, "last_screen_date", asof.isoformat())
        conn.commit()
        log.info("screen: %d survivors, %d dropped (universe=%d) for %s",
                 len(survivors), len(dropped), len(universe), asof)
    finally:
        conn.close()
```

> NOTE: `gdb.primary_symbols(conn)` is read again for `prior` AFTER survivors were upserted, so `prior` includes the just-upserted survivors plus any stale rows; `dropped = prior - survivors` correctly yields only the stale rows. This relies on survivors being upserted before the `prior` read — keep that order.

- [ ] **Step 5: Add the `screen` CLI subcommand**

In `src/gexwheel/__main__.py`, add the parser (near the other `sub.add_parser` calls):

```python
    screen_p = sub.add_parser("screen")
    screen_p.add_argument("--force", action="store_true",
                          help="ignore the interval throttle and re-screen now")
```

And the dispatch branch (near the `mentions`/`morning` branches):

```python
    elif args.cmd == "screen":
        from .jobs import screen as screen_job
        screen_job.run(cfg, force=args.force)
```

Update the module docstring usage list at the top to include:

```
  python -m gexwheel screen [--force]  # periodic primary-watchlist screen
```

- [ ] **Step 6: Run the screen-job tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_screen_job.py -v`
Expected: 4 PASS.

- [ ] **Step 7: Run the FULL suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: all green (existing + new tests).

- [ ] **Step 8: Commit**

```bash
git add config/config.example.yaml src/gexwheel/config.py src/gexwheel/__main__.py src/gexwheel/jobs/screen.py tests/test_screen_job.py
git commit -m "feat(jobs): self-throttling primary-watchlist screen job + screen CLI"
```

---

### Task 7: Update agent docs for the new screen stage

**Files:**
- Modify: `AGENTS.md` (status table + architecture diagram), `README.md` (CLI list + pipeline)

- [ ] **Step 1: Update AGENTS.md**

In the IMPLEMENTED row of the status table, add `screening/primary.py`, `screening/chain_metrics.py`, `jobs/screen.py`. In the Architecture quick-reference, add the screen stage feeding the primary watchlist (a one-line note is enough; the spec is authoritative). Add `primary_watchlist`, `app_state` to the "SQLite spine" list.

- [ ] **Step 2: Update README.md**

Add `python -m gexwheel screen [--force]` to the CLI entrypoints list with a one-line description ("periodic primary-watchlist screen"). Do not rewrite the whole pipeline diagram here — Plan A2 finalizes the flow narrative.

- [ ] **Step 3: Run the full suite once more & commit**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: all green.

```bash
git add AGENTS.md README.md
git commit -m "docs: document the primary-watchlist screen stage"
```

---

## Self-review notes (for the executor)

- After every task: `PYTHONPATH=src pytest` must stay green (ground rule).
- Frozen contracts respected: `models.py` only gains a new dataclass; `schema.sql` untouched (new tables via migration `0002`); `run_filters`/`db.*` existing signatures unchanged.
- `"discord"` stays in `REQUIRED_KEYS` here on purpose; Plan B1 removes it. Running B1 before A1 is unsupported — execute A1 → A2 → B1 → B2.
- No new dependencies in this plan.
