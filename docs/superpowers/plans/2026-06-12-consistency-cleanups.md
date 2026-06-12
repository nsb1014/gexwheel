# Consistency Cleanups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align `scoring.py`, `filters.py`, `data/mentions.py`, and `data/prices.py` with the repo's own stated rules (timezone discipline, retry/backoff on network calls, single-source config defaults) and fix small internal inconsistencies found in review.

**Architecture:** Four independent tasks, one per module. No public signatures change (frozen contract). Each task is self-contained and individually committable; they can be executed in any order.

**Tech Stack:** Python stdlib (`zoneinfo`, `time`, `math`), pandas (test fixtures only), pytest with `monkeypatch`/`unittest.mock.patch`, in-memory sqlite via `gexwheel.db.connect(":memory:")`.

---

### Task 1: `alerts/scoring.py` — dedup honors `sent_at`, top-level db import, named proximity constant

**Files:**
- Modify: `src/gexwheel/alerts/scoring.py`
- Test: `tests/test_scoring.py` (append)

**Background.** Three issues:

1. *Dedup vs retry conflict.* `alerts/discord.py`'s docstring spec says failed sends are logged with `sent_at = None` and "retried next run by the dedup rules". But `should_alert`'s dedup query blocks on **any** alerts row for `(symbol, date, type)`, including unsent ones — so a same-day manual re-run after fixing a broken webhook would never re-alert. The two module docstrings conflict; the discord one describes the operationally correct intent (an alert that never reached Discord was not delivered and should not dedup). Per repo rules, when specs conflict, pick the simpler/safer behavior and leave a `# NOTE:`.
2. `from .. import db as gdb` is imported inside `should_alert` (line 64) instead of at module top. There is no circular-import reason for this (`db.py` imports only `models`).
3. `score()` hardcodes `proximity_pct = 0.03` (line 85) because its frozen signature has no `cfg` access. The constant should at least be a named module-level tunable next to `WEIGHTS`, as the docstring's "tunable without touching logic" philosophy demands. (Threading cfg through is a deferred decision — see `docs/superpowers/plans/2026-06-12-deferred-decisions.md`.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scoring.py` (the file already has `_profile`, `_conn_with_recent_walls`, `_cfg`, `ASOF`, and imports `gdb`):

```python
def test_should_alert_retries_when_prior_alert_was_never_sent():
    conn = _conn_with_recent_walls(18.0, 18.0)
    gdb.log_alert(conn, "TEST", ASOF, "put_wall_entry", {}, sent_at=None)

    assert should_alert(_profile(spot=18.0), _cfg(), conn, ASOF) is True


def test_should_alert_dedups_when_prior_alert_was_sent():
    conn = _conn_with_recent_walls(18.0, 18.0)
    gdb.log_alert(conn, "TEST", ASOF, "put_wall_entry", {}, sent_at="2026-06-10T07:20:00")

    assert should_alert(_profile(spot=18.0), _cfg(), conn, ASOF) is False
```

- [ ] **Step 2: Run tests to verify the first fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_scoring.py -v`
Expected: `test_should_alert_retries_when_prior_alert_was_never_sent` FAILS (returns False today); the dedup test and the three pre-existing tests PASS.

- [ ] **Step 3: Implement all three scoring fixes**

In `src/gexwheel/alerts/scoring.py`:

(a) Move the db import to the top — add after `from ..models import GexProfile`:

```python
from .. import db as gdb
```

and delete the local `from .. import db as gdb` inside `should_alert` (line 64). Update the call site to `recent_walls = gdb.recent_put_walls(conn, profile.symbol, persistence_days)` (name already matches).

(b) Change the dedup query (lines 75-78) to:

```python
    # NOTE: only a *delivered* alert (sent_at set) dedups; rows logged with
    # sent_at=NULL are failed sends that should be retried on a re-run
    # (per the alerts/discord.py send_alerts spec).
    dup = conn.execute(
        "SELECT 1 FROM alerts WHERE symbol=? AND date=? AND type=? AND sent_at IS NOT NULL",
        (profile.symbol, asof.isoformat(), "put_wall_entry"),
    ).fetchone()
    return dup is None
```

Also amend the module docstring's dedup bullet (line 11) to read:

```
  * no duplicate: no DELIVERED row (sent_at set) in alerts table for
    (symbol, asof, 'put_wall_entry'); rows with sent_at NULL are failed
    sends and do not dedup, so a re-run can retry them.
```

(c) Add a named constant next to `WEIGHTS` and use it in `score()`:

```python
# score() has no cfg access (frozen signature); keep this aligned with
# alerts.put_wall_proximity_pct in config/config.example.yaml.
DEFAULT_PROXIMITY_PCT = 0.03
```

and inside `score()` replace line 85 (`proximity_pct = 0.03  # default; ...`) with:

```python
    proximity_pct = DEFAULT_PROXIMITY_PCT
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_scoring.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `PYTHONPATH=src python3 -m pytest -q` — all pass.

```bash
git add src/gexwheel/alerts/scoring.py tests/test_scoring.py
git commit -m "fix(scoring): only delivered alerts dedup; hoist db import; name proximity constant"
```

---

### Task 2: `screening/filters.py` — remove redundant local datetime imports

**Files:**
- Modify: `src/gexwheel/screening/filters.py:34` (imports), `:115`, `:146` (local imports)

- [ ] **Step 1: Consolidate the import**

Change line 34 from:

```python
from datetime import date, timedelta
```

to:

```python
from datetime import date, datetime, timedelta
```

Then delete the two function-local `from datetime import datetime` lines (inside the earnings-blackout block at line 115 and the not_cooled_down block at line 146). The `datetime.strptime(...)` calls below them are unchanged.

- [ ] **Step 2: Run the filter tests, then the full suite**

Run: `PYTHONPATH=src python3 -m pytest tests/test_filters.py -q` — all pass.
Run: `PYTHONPATH=src python3 -m pytest -q` — all pass.

- [ ] **Step 3: Commit**

```bash
git add src/gexwheel/screening/filters.py
git commit -m "chore(filters): hoist datetime import to module level"
```

---

### Task 3: `data/mentions.py` — consistent upvotes fallback, class defined before use

**Files:**
- Modify: `src/gexwheel/data/mentions.py:48-64` (ordering), `:101-104` (upvotes)
- Test: `tests/test_mentions.py` (append)

**Background.** (a) On a missing `upvotes` field the code yields `0`; on an unparseable one it yields `None` — two different fallbacks for the same "no usable data" situation. Align both to `0`, matching how `mentions` parse failures fall back to `0` four lines earlier. (b) `MentionFetchError` is defined *below* `_get_with_retry`, which raises it — legal at runtime but reads backwards; move the class above the helper.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mentions.py` (file already imports `patch`, `date`, `fetch_apewisdom`):

```python
def test_unparseable_upvotes_falls_back_to_zero_like_mentions():
    payload = {"results": [
        {"ticker": "TEST", "mentions": "5", "rank": 1, "upvotes": "n/a"},
    ]}
    with patch("gexwheel.data.mentions._get_with_retry", return_value=payload):
        records = fetch_apewisdom("wallstreetbets", pages=1, asof=date(2026, 6, 10))

    assert len(records) == 1
    assert records[0].mentions == 5
    assert records[0].upvotes == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_mentions.py -v`
Expected: the new test FAILS with `assert None == 0`; pre-existing tests PASS.

- [ ] **Step 3: Implement**

In `src/gexwheel/data/mentions.py`:

(a) Move the `class MentionFetchError(RuntimeError): pass` block (currently lines 63-64) so it sits immediately above `_get_with_retry` (currently line 48).

(b) In `fetch_apewisdom`, change the upvotes fallback (lines 101-104) to:

```python
            try:
                upvotes = int(item.get("upvotes", 0) or 0)
            except (TypeError, ValueError):
                upvotes = 0   # NOTE: same fallback as mentions; bad data == no data
```

- [ ] **Step 4: Run tests, then the full suite**

Run: `PYTHONPATH=src python3 -m pytest tests/test_mentions.py -q` — all pass.
Run: `PYTHONPATH=src python3 -m pytest -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add src/gexwheel/data/mentions.py tests/test_mentions.py
git commit -m "fix(mentions): consistent upvotes fallback; define MentionFetchError before use"
```

---

### Task 4: `data/prices.py` — retry/backoff on history fetch, drop deprecated `utcnow()`

**Files:**
- Modify: `src/gexwheel/data/prices.py`
- Test: `tests/test_prices.py` (create)

**Background.** (a) The repo's network rule requires exponential-backoff retry on every network call; `chains.py` complies but `daily_closes` is a single-shot `ticker.history()` call — one throttle kills that symbol for the day. (b) `next_earnings` uses `datetime.utcnow().date()` (lines 75, 86), which violates the repo timezone rule ("never bare date.today()" — UTC-today is the same failure class) and is deprecated since Python 3.12. The function's signature is frozen (no `cfg` access), so the market timezone must be a module constant; the schema header already pins dates to "America/New_York trading-day terms". (c) `next_earnings` and `sector` stay single-shot deliberately: they already swallow all failures and return `None`, which filters treat as "unknown → pass", so a retry adds latency for no correctness gain — leave them, but this is recorded in the deferred-decisions doc.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prices.py`:

```python
"""Price-history retry behavior and earnings-date selection (no live network)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from gexwheel.data.prices import daily_closes, next_earnings


def test_daily_closes_retries_transient_history_failure(monkeypatch):
    calls = {"n": 0}

    class FakeTicker:
        def history(self, period):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("throttled")
            return pd.DataFrame({"Close": [10.0] * 70})

    monkeypatch.setattr("gexwheel.data.prices.yf.Ticker", lambda symbol: FakeTicker())
    # patch the stdlib module, not gexwheel.data.prices.time: before the fix
    # the module doesn't import time, and the test must fail on behavior
    # (no retry), not on a monkeypatch AttributeError
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    closes = daily_closes("TEST")

    assert len(closes) == 70
    assert calls["n"] == 2


def test_next_earnings_picks_first_future_date(monkeypatch):
    class FakeTicker:
        calendar = {"Earnings Date": [date(2020, 1, 1), date(2099, 1, 2), date(2099, 1, 1)]}

    monkeypatch.setattr("gexwheel.data.prices.yf.Ticker", lambda symbol: FakeTicker())

    assert next_earnings("TEST") == date(2099, 1, 1)
```

- [ ] **Step 2: Run tests to verify the retry test fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_prices.py -v`
Expected: `test_daily_closes_retries_transient_history_failure` FAILS with `PriceFetchError` (no retry today, the single failure propagates). `test_next_earnings_picks_first_future_date` should already PASS (it pins current behavior before the utcnow change).

- [ ] **Step 3: Implement**

In `src/gexwheel/data/prices.py`:

(a) Add to the imports:

```python
import time
from zoneinfo import ZoneInfo
```

(b) Add module constants/helper below `class PriceFetchError`:

```python
# NOTE: next_earnings()/daily_closes() signatures are frozen (no cfg access),
# so the market timezone is pinned here; schema.sql stores all dates in
# America/New_York trading-day terms.
_MARKET_TZ = ZoneInfo("America/New_York")


def _history_with_retry(ticker, period: str, retries: int = 3):
    """ticker.history() with exponential backoff (1s, 2s) on transient failures."""
    for attempt in range(retries):
        try:
            return ticker.history(period=period)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
```

(c) In `daily_closes`, replace the fetch block (lines 39-43) with:

```python
    try:
        ticker = yf.Ticker(symbol)
        hist = _history_with_retry(ticker, f"{lookback_days}d")
    except Exception as exc:
        raise PriceFetchError(f"price history failed for {symbol}: {exc}") from exc
```

(d) In `next_earnings`, replace both occurrences of `today = datetime.utcnow().date()` (lines 75 and 86) with:

```python
            today = datetime.now(_MARKET_TZ).date()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_prices.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `PYTHONPATH=src python3 -m pytest -q` — all pass.

```bash
git add src/gexwheel/data/prices.py tests/test_prices.py
git commit -m "fix(prices): retry history fetch with backoff; market-tz today instead of deprecated utcnow"
```
