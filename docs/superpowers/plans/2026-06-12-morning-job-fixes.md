# Morning Job Fixes Implementation Plan

> **STATUS: COMPLETE** — merged to `main` via [PR #4](https://github.com/nsb1014/gexwheel/pull/4). Do not re-implement; kept for traceability.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the mention-velocity query in `jobs/morning.py` so it never mixes ApeWisdom and PRAW baselines, remove the duplicated sort/truncate logic and dead code in the alert-send path, and drop unused imports.

**Architecture:** Extract the inline velocity SQL into a testable `_velocity_ratio()` helper with an explicit `source` filter (mirroring how `_update_watchlist_membership` and `_failed_checks` are already extracted and unit-tested). Tighten `run()` so the top-N truncation happens once and the dead fallback payload is removed. No public signatures change (`run(cfg)` is frozen; new helpers are private).

**Tech Stack:** Python stdlib, sqlite3 in-memory DBs via `gexwheel.db.connect(":memory:")`, pytest.

---

## Background (why these are bugs)

**Source mixing.** The `mentions` table is keyed `(symbol, date, source)` precisely so ApeWisdom and PRAW counts never mix (see the `data/mentions.py` docstring: *"tag source='praw' so velocity baselines never mix sources"*). But the alert-velocity query in `_process_symbol` has no `source` filter:

```190:196:src/gexwheel/jobs/morning.py
        vel_row = conn.execute(
            """SELECT mentions, (SELECT AVG(mentions) FROM (
                   SELECT mentions FROM mentions WHERE symbol=? AND date < ?
                   ORDER BY date DESC LIMIT 7)) AS baseline
               FROM mentions WHERE symbol=? AND date=?""",
            (symbol, asof.isoformat(), symbol, asof.isoformat()),
        ).fetchone()
```

With `reddit.source: both` configured, `fetchone()` picks an arbitrary row for today and the baseline averages across both sources, skewing the velocity component of the alert score.

**Duplicated truncation.** `run()` computes `top_cards = sorted(cards, ...)[:max_cards]` and then calls `disc.send_alerts(cards, cfg)`, which re-sorts and re-truncates internally. The two stay in sync only by coincidence, and they read the config differently: `run()` uses `cfg["discord"]["max_alerts_per_run"]` (hard `KeyError`) while `send_alerts` uses `.get("max_alerts_per_run", 8)`.

**Dead code.** (a) `alert_payloads.get(key, _alert_payload(card, put_wall_strength_val=None))` — a payload is always stored when a card is appended, so the fallback can never fire, yet it eagerly builds a throwaway dict per card. (b) `ChainFetchError` and `PriceFetchError` are imported at lines 46-47 and never referenced. (c) `_refresh_earnings` does a local `from datetime import datetime` (line 290) although the module already imports `datetime` at the top (line 36).

---

### Task 1: Extract `_velocity_ratio()` with a source filter

**Files:**
- Modify: `src/gexwheel/jobs/morning.py` (replace lines 189-202 inside `_process_symbol`; add helper near `_failed_checks`)
- Test: `tests/test_morning_velocity.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_morning_velocity.py`:

```python
"""Alert-velocity ratio must never mix mention sources."""
from __future__ import annotations

from datetime import date, timedelta

from gexwheel import db as gdb
from gexwheel.jobs.morning import _velocity_ratio

ASOF = date(2026, 6, 10)


def _seed(conn, source: str, day: date, mentions: int) -> None:
    conn.execute(
        "INSERT INTO mentions(symbol, date, source, mentions) VALUES (?,?,?,?)",
        ("TEST", day.isoformat(), source, mentions),
    )


def test_velocity_ratio_uses_only_requested_source():
    conn = gdb.connect(":memory:")
    # apewisdom: flat 10/day baseline, 30 today -> ratio 3.0
    for i in range(1, 8):
        _seed(conn, "apewisdom", ASOF - timedelta(days=i), 10)
    _seed(conn, "apewisdom", ASOF, 30)
    # praw noise that would poison the baseline and today's count if mixed
    for i in range(1, 8):
        _seed(conn, "praw", ASOF - timedelta(days=i), 1000)
    _seed(conn, "praw", ASOF, 1)
    conn.commit()

    assert _velocity_ratio(conn, "TEST", ASOF, "apewisdom") == 3.0


def test_velocity_ratio_none_without_history():
    conn = gdb.connect(":memory:")
    _seed(conn, "apewisdom", ASOF, 30)
    conn.commit()

    assert _velocity_ratio(conn, "TEST", ASOF, "apewisdom") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_morning_velocity.py -v`
Expected: FAIL with `ImportError: cannot import name '_velocity_ratio'`.

- [ ] **Step 3: Implement `_velocity_ratio` and call it from `_process_symbol`**

Add to `src/gexwheel/jobs/morning.py`, next to `_failed_checks`:

```python
def _velocity_ratio(conn, symbol: str, asof, source: str) -> float | None:
    """today's mentions / trailing-7-row average, single source only.

    The mentions PK is (symbol, date, source); mixing sources would skew
    the baseline (see data/mentions.py docstring).
    """
    row = conn.execute(
        """SELECT mentions, (SELECT AVG(mentions) FROM (
               SELECT mentions FROM mentions
               WHERE symbol=? AND source=? AND date < ?
               ORDER BY date DESC LIMIT 7)) AS baseline
           FROM mentions WHERE symbol=? AND source=? AND date=?""",
        (symbol, source, asof.isoformat(), symbol, source, asof.isoformat()),
    ).fetchone()
    if not row or not row["baseline"]:
        return None
    try:
        return row["mentions"] / row["baseline"]
    except (TypeError, ZeroDivisionError):
        return None
```

In `_process_symbol`, replace the block from `vel_row = conn.execute(` through the `except (TypeError, ZeroDivisionError): pass` (currently lines 190-202) with:

```python
        # NOTE: velocity context follows the configured discovery source;
        # 'both' falls back to apewisdom (the primary).
        mention_source = "praw" if cfg["reddit"].get("source") == "praw" else "apewisdom"
        vel_ratio = _velocity_ratio(conn, symbol, asof, mention_source)
```

The subsequent line `card_score = score(profile, ivr, vrp, vel_ratio)` is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_morning_velocity.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/gexwheel/jobs/morning.py tests/test_morning_velocity.py
git commit -m "fix(morning): velocity baseline must not mix mention sources"
```

---

### Task 2: Single truncation point + consistent config access in `run()`

**Files:**
- Modify: `src/gexwheel/jobs/morning.py:94-112` (the `if cards:` block in `run()`)

- [ ] **Step 1: Rework the send/log block**

Replace the `if cards:` block in `run()` (lines 95-112) with:

```python
    if cards:
        # send_alerts sorts and truncates to max_alerts_per_run internally;
        # truncate here too so only attempted cards are logged (re-sorting an
        # already-truncated list inside send_alerts is a no-op).
        max_cards = cfg["discord"].get("max_alerts_per_run", 8)
        top_cards = sorted(cards, key=lambda c: c.score, reverse=True)[:max_cards]
        sent_cards = disc.send_alerts(top_cards, cfg)
        sent_keys = {(c.symbol, c.alert_type) for c in sent_cards}
        now_iso = datetime.now(tz).isoformat()
        for card in top_cards:
            delivered = (card.symbol, card.alert_type) in sent_keys
            gdb.log_alert(
                conn, card.symbol, asof, card.alert_type,
                alert_payloads[(card.symbol, card.alert_type)],
                now_iso if delivered else None,
            )
        conn.commit()
        log.info("morning: sent %d/%d alerts", len(sent_cards), len(top_cards))
    else:
        log.info("morning: no alerts generated")
```

Three changes versus the current code:
1. `send_alerts` receives `top_cards`, not the full `cards` list — `run()` no longer relies on `send_alerts` applying an identical-by-coincidence truncation.
2. `max_alerts_per_run` is read with `.get(..., 8)`, matching `alerts/discord.py:115` instead of a hard `KeyError`.
3. The dead `alert_payloads.get(key, _alert_payload(card, put_wall_strength_val=None))` fallback becomes a direct index — a payload is always stored when the card is appended in `_process_symbol`, so a `KeyError` here would indicate a real bug and should not be masked.

- [ ] **Step 2: Run the full suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: all tests pass (this block has no direct test coverage; behavior is unchanged by construction).

- [ ] **Step 3: Commit**

```bash
git add src/gexwheel/jobs/morning.py
git commit -m "refactor(morning): single truncation point and direct payload lookup in alert send path"
```

---

### Task 3: Remove unused imports and redundant local import

**Files:**
- Modify: `src/gexwheel/jobs/morning.py:46-47` (imports), `:289-291` (`_refresh_earnings`)

- [ ] **Step 1: Fix the imports**

Line 46: change

```python
from ..data.chains import ChainFetchError, make_chain_source
```

to

```python
from ..data.chains import make_chain_source
```

Line 47: change

```python
from ..data.prices import PriceFetchError, daily_closes, next_earnings, sector
```

to

```python
from ..data.prices import daily_closes, next_earnings, sector
```

(`ChainFetchError`/`PriceFetchError` are never referenced — the per-symbol `except Exception` in `run()` handles all failures.)

In `_refresh_earnings`, delete the local import on line 290 (`from datetime import datetime`) — the module already imports `datetime` at the top (line 36). The `datetime.strptime(...)` call below it is unchanged.

- [ ] **Step 2: Verify nothing else referenced them**

Run: `rg "ChainFetchError|PriceFetchError" src/gexwheel/jobs/`
Expected: no matches.

- [ ] **Step 3: Run the full suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/gexwheel/jobs/morning.py
git commit -m "chore(morning): drop unused exception imports and redundant local datetime import"
```
