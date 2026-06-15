# Plan A2 — Narrow Reddit tracking + simplify morning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the daily Reddit job track only primary-watchlist members and promote velocity triggers onto the secondary/active watchlist, and simplify the morning job to operate solely on the active watchlist (structural entry-gating now lives in the periodic screen).

**Architecture:** `screening/discovery.py` filters fetched mentions to `primary_watchlist` members before persisting/computing velocity, and on a trigger promotes the name via `db.upsert_ticker` (metadata row, keeps cooldown/sector working) + `db.watchlist_add` (active). `jobs/morning.py` drops the discovery-promotion branch and the universe union; candidates become the active watchlist only, and watchlist removal keys off the still-daily checks (`above_50dma`, `earnings`).

**Tech Stack:** Python stdlib, `sqlite3` in-memory via `gexwheel.db.connect(":memory:")`, pytest, `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-06-15-screening-inversion-design.md`

**Prerequisite:** Plan A1 merged (provides `primary_watchlist`, `db.primary_symbols`, the screen job).

---

## Background

After A1 the primary watchlist exists but nothing consumes it yet. This plan wires it into the daily flow:

- **Reddit narrowing:** `run_discovery` currently persists/velocity-checks *every* ApeWisdom ticker. It should only consider primary members — this is the "reduce the number of tickers tracked on Reddit" win.
- **Promotion mechanism:** a primary name whose mentions trigger velocity (3×/7d, unchanged math) is promoted to the secondary/active `watchlist`. The `tickers` row is still upserted so `bench_ticker` (which writes `tickers.cooldown_until`) and sector caching keep working for that name.
- **Morning simplification:** candidates become the active watchlist only. The per-candidate structural entry gate and discovery promotion move out of morning; only the daily checks (`above_50dma`, `earnings`) can demote an active name (plus wall-break bench, unchanged).

`run_filters` is still called in the morning loop (it's a cheap pure computation over already-fetched chain/price data and provides the `iv_rank`/`vrp`/`regime`/proximity context the alert path and notes need). What changes is which check failures *act* on watchlist membership.

---

## File structure

- Modify: `src/gexwheel/screening/discovery.py` — primary filter + promotion.
- Modify: `src/gexwheel/jobs/morning.py` — candidate sourcing + membership logic.
- Tests: `tests/test_discovery.py` (update + add), `tests/test_morning_pruning.py` (rewrite for new semantics), `tests/test_morning_candidates.py` (create).

---

### Task 1: Narrow discovery to primary members + promote on trigger

**Files:**
- Modify: `src/gexwheel/screening/discovery.py`
- Test: `tests/test_discovery.py`

- [ ] **Step 1: Update the existing discovery test and add a narrowing test**

Replace the whole body of `tests/test_discovery.py` with:

```python
"""Stage-1 discovery: primary-member narrowing, promotion, praw fallback."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from gexwheel import db as gdb
from gexwheel.data.mentions import MentionFetchError
from gexwheel.models import MentionRecord
from gexwheel.screening.discovery import run_discovery

ASOF = date(2026, 6, 10)

CFG = {
    "reddit": {"source": "both", "apewisdom_filter": "wallstreetbets", "apewisdom_pages": 1},
    "discovery": {
        "velocity_trigger": 3.0, "baseline_floor": 10,
        "min_history_days": 5, "max_daily_mentions": 1000,
    },
}


def _seed_primary(conn, *symbols):
    for s in symbols:
        gdb.upsert_primary(conn, s, ASOF, metrics={"spot": 20.0})
    conn.commit()


def test_discovery_skips_when_primary_empty():
    conn = gdb.connect(":memory:")
    with patch(
        "gexwheel.screening.discovery.fetch_apewisdom",
        return_value=[MentionRecord("AAA", ASOF, 100, source="apewisdom")],
    ):
        assert run_discovery(conn, CFG, ASOF) == []
    # nothing persisted
    assert conn.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"] == 0


def test_discovery_drops_non_primary_symbols():
    conn = gdb.connect(":memory:")
    _seed_primary(conn, "AAA")
    # baseline history for AAA so it can trigger
    for d in range(1, 6):
        gdb.record_mention(conn, MentionRecord("AAA", ASOF - timedelta(days=d), 10, source="apewisdom"))
    conn.commit()
    records = [
        MentionRecord("AAA", ASOF, 50, source="apewisdom"),   # primary -> kept
        MentionRecord("ZZZ", ASOF, 9999, source="apewisdom"), # not primary -> dropped
    ]
    with patch("gexwheel.screening.discovery.fetch_apewisdom", return_value=records):
        triggered = run_discovery(conn, CFG, ASOF)
    assert [r.symbol for r in triggered] == ["AAA"]
    # ZZZ never persisted
    assert conn.execute("SELECT COUNT(*) c FROM mentions WHERE symbol='ZZZ'").fetchone()["c"] == 0


def test_triggered_primary_name_is_promoted_to_active_watchlist():
    conn = gdb.connect(":memory:")
    _seed_primary(conn, "AAA")
    for d in range(1, 6):
        gdb.record_mention(conn, MentionRecord("AAA", ASOF - timedelta(days=d), 10, source="apewisdom"))
    conn.commit()
    with patch(
        "gexwheel.screening.discovery.fetch_apewisdom",
        return_value=[MentionRecord("AAA", ASOF, 50, source="apewisdom")],
    ):
        run_discovery(conn, CFG, ASOF)
    assert "AAA" in gdb.watchlist_active(conn)
    # tickers metadata row exists (so cooldown/bench works later)
    assert conn.execute("SELECT COUNT(*) c FROM tickers WHERE symbol='AAA'").fetchone()["c"] == 1


def test_discovery_falls_back_to_praw_for_primary_member():
    conn = gdb.connect(":memory:")
    _seed_primary(conn, "TEST")
    for d in range(1, 6):
        gdb.record_mention(conn, MentionRecord("TEST", ASOF - timedelta(days=d), 10, source="praw"))
    conn.commit()
    with patch(
        "gexwheel.screening.discovery.fetch_apewisdom",
        side_effect=MentionFetchError("down"),
    ), patch(
        "gexwheel.data.mentions.fetch_praw",
        return_value=[MentionRecord("TEST", ASOF, 50, source="praw")],
    ):
        triggered = run_discovery(conn, CFG, ASOF)
    assert [r.symbol for r in triggered] == ["TEST"]
    row = conn.execute(
        "SELECT mentions FROM mentions WHERE symbol='TEST' AND date=? AND source='praw'",
        (ASOF.isoformat(),),
    ).fetchone()
    assert row["mentions"] == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_discovery.py -v`
Expected: failures (e.g. `test_discovery_drops_non_primary_symbols` persists ZZZ; `test_discovery_skips_when_primary_empty` persists AAA) — current code does not filter on primary.

- [ ] **Step 3: Implement the primary filter + promotion in `discovery.py`**

In `src/gexwheel/screening/discovery.py`, immediately AFTER the fetch-failure guard (the block ending `return []` around line 60-62) and BEFORE "`# --- 2. Persist all mention records ---`", insert:

```python
    # --- 1b. Narrow to primary-watchlist members (entry universe) ---
    primary = set(db.primary_symbols(conn))
    if not primary:
        log.warning(
            "discovery: primary watchlist empty — run `python -m gexwheel screen --force` "
            "to seed it; skipping velocity for %s", asof,
        )
        return []
    records = [r for r in records if r.symbol in primary]
    if not records:
        log.info("discovery: no primary-member mentions for %s", asof)
        return []
```

Then in the velocity loop, replace the trigger block (currently lines ~89-91):

```python
        if result.triggered:
            db.upsert_ticker(conn, rec.symbol, source="wsb_velocity", asof=asof)
            triggered.append(result)
```

with:

```python
        if result.triggered:
            # tickers row keeps cooldown/sector metadata; watchlist promotes to secondary/active
            db.upsert_ticker(conn, rec.symbol, source="wsb_velocity", asof=asof)
            db.watchlist_add(conn, rec.symbol, asof)
            triggered.append(result)
```

Update the module docstring step 4 to read: "For triggered results: db.upsert_ticker(source='wsb_velocity') AND db.watchlist_add() — promotion to the secondary/active watchlist happens here now." Update step 1/2 notes to mention the primary-member narrowing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_discovery.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gexwheel/screening/discovery.py tests/test_discovery.py
git commit -m "feat(discovery): track only primary members; promote velocity triggers to active watchlist"
```

---

### Task 2: Simplify morning candidate sourcing

**Files:**
- Modify: `src/gexwheel/jobs/morning.py` (`run()` candidate block, lines ~69-90)
- Test: `tests/test_morning_candidates.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_morning_candidates.py`:

```python
"""Morning candidates are the active watchlist only (no discovery union)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from gexwheel import db as gdb
from gexwheel.jobs import morning as morning_job


def _base_cfg(tmp_path):
    return {
        "db_path": str(tmp_path / "g.db"),
        "timezone": "America/New_York",
        "data": {"chain_source": "yfinance", "max_dte": 60,
                 "request_sleep_s": 0, "request_retries": 1, "risk_free_rate": 0.045},
        "reddit": {"source": "apewisdom"},
        "filters": {}, "alerts": {"cooldown_days": 5},
        "discord": {"max_alerts_per_run": 8, "webhook_url": "x"},
    }


def test_morning_candidates_are_active_watchlist_only(tmp_path):
    cfg = _base_cfg(tmp_path)
    conn = gdb.connect(cfg["db_path"])
    # an active watchlist name
    gdb.watchlist_add(conn, "ACTIVE", date(2026, 6, 10))
    # a discovery ticker NOT on the watchlist (old code would have included it)
    gdb.upsert_ticker(conn, "DISCO", source="wsb_velocity", asof=date(2026, 6, 10))
    conn.commit()
    conn.close()

    processed = []

    def _fake_process(symbol, *a, **k):
        processed.append(symbol)

    with patch("gexwheel.jobs.morning.make_chain_source"), \
         patch("gexwheel.jobs.morning._process_symbol", side_effect=_fake_process):
        morning_job.run(cfg)

    assert processed == ["ACTIVE"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_morning_candidates.py -v`
Expected: FAIL — current code unions discovery tickers, so `processed` contains `DISCO` too.

- [ ] **Step 3: Replace the candidate block in `run()`**

In `src/gexwheel/jobs/morning.py`, replace the candidate-building block (currently lines 69-80, from the `# Candidates = ...` comment through the `log.info("morning: %d candidates ...")` call) with:

```python
    # Candidates = the active (secondary) watchlist only. Structural entry-gating
    # now lives in the periodic screen (jobs/screen.py); discovery promotes names
    # onto this list via velocity. See specs/2026-06-15-screening-inversion-design.md.
    candidates = sorted(gdb.watchlist_active(conn))
    log.info("morning: %d active watchlist candidates", len(candidates))
```

This removes the `watchlist = set(...)` variable and the `discovery_rows` query. Because `watchlist` is gone, update the `_process_symbol(...)` call (lines ~85-90) to drop the `watchlist` argument:

```python
        try:
            _process_symbol(
                symbol, asof, conn, chain_src, cfg, data_cfg,
                cooldown_days, cards, alert_payloads
            )
        except Exception as exc:
            log.error("morning: unhandled error for %s: %s", symbol, exc, exc_info=True)
```

- [ ] **Step 4: Update `_process_symbol` signature + its membership call**

Change the `_process_symbol` definition (lines 120-121) to drop `watchlist`:

```python
def _process_symbol(symbol, asof, conn, chain_src, cfg, data_cfg,
                    cooldown_days, cards, alert_payloads):
```

Inside it, change the membership call (line 179) from
`_update_watchlist_membership(symbol, watchlist, report, conn, asof)` to:

```python
    _update_watchlist_membership(symbol, report, conn, asof)
```

(The next task rewrites `_update_watchlist_membership` itself.)

- [ ] **Step 5: Run the candidate test**

Run: `PYTHONPATH=src python3 -m pytest tests/test_morning_candidates.py -v`
Expected: PASS. (Other morning tests may fail until Task 3 — that's fine, finish Task 3 before the full suite.)

- [ ] **Step 6: Commit**

```bash
git add src/gexwheel/jobs/morning.py tests/test_morning_candidates.py
git commit -m "feat(morning): candidates are the active watchlist only"
```

---

### Task 3: Rewrite watchlist-membership logic for the daily gate

**Files:**
- Modify: `src/gexwheel/jobs/morning.py` (`_DAILY_REMOVE_CHECKS`/`_WEEKLY_PRUNE_CHECKS` consts + `_update_watchlist_membership`)
- Test: `tests/test_morning_pruning.py` (rewrite)

- [ ] **Step 1: Rewrite the test for the new semantics**

Replace the whole body of `tests/test_morning_pruning.py` with:

```python
"""Active-watchlist removal keys off the still-daily checks only."""
from __future__ import annotations

from datetime import date

from gexwheel import db as gdb
from gexwheel.jobs.morning import _update_watchlist_membership
from gexwheel.models import FilterReport

ASOF = date(2026, 6, 15)


def _conn_with_active_watchlist(symbol: str = "TEST"):
    conn = gdb.connect(":memory:")
    conn.execute(
        "INSERT INTO tickers(symbol, added_date, source, excluded) VALUES (?, ?, 'manual', 0)",
        (symbol, "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO watchlist(symbol, date_added, status) VALUES (?, ?, 'active')",
        (symbol, "2026-01-01"),
    )
    conn.commit()
    return conn


def _report(**overrides: bool) -> FilterReport:
    checks = {
        "price_range": True, "open_interest": True, "iv_rank": True, "vrp": True,
        "spread": True, "above_50dma": True, "earnings": True, "sector": True,
        "not_blocklisted": True, "not_cooled_down": True, "regime": True,
    }
    checks.update(overrides)
    return FilterReport("TEST", all(checks.values()), checks=checks, values={})


def _status_and_notes(conn):
    row = conn.execute("SELECT status, notes FROM watchlist WHERE symbol='TEST'").fetchone()
    return row["status"], row["notes"]


def test_above_50dma_failure_removes_name():
    conn = _conn_with_active_watchlist()
    _update_watchlist_membership("TEST", _report(above_50dma=False), conn, ASOF)
    assert _status_and_notes(conn) == ("removed", "daily fail: above_50dma")


def test_earnings_failure_removes_name():
    conn = _conn_with_active_watchlist()
    _update_watchlist_membership("TEST", _report(earnings=False), conn, ASOF)
    assert _status_and_notes(conn) == ("removed", "daily fail: earnings")


def test_structural_failure_does_not_remove_active_name():
    # price/oi/spread/iv/vrp/sector are the SCREEN's job now, not a daily removal reason
    conn = _conn_with_active_watchlist()
    _update_watchlist_membership(
        "TEST", _report(open_interest=False, iv_rank=False, sector=False), conn, ASOF
    )
    assert _status_and_notes(conn) == ("active", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_morning_pruning.py -v`
Expected: FAIL — current `_update_watchlist_membership` signature still takes `watchlist`, uses the old check sets, and emits "structural fail"/"weekly prune" notes.

- [ ] **Step 3: Rewrite the constants and the function**

In `src/gexwheel/jobs/morning.py`, replace the two constants (lines 53-54):

```python
_DAILY_REMOVE_CHECKS = ("price_range", "sector", "not_blocklisted")
_WEEKLY_PRUNE_CHECKS = ("price_range", "open_interest", "iv_rank", "vrp")
```

with:

```python
# Active names are demoted only on the still-daily checks. Structural gating
# (price/volume/oi/spread/vrp/sector/blocklist) lives in the periodic screen.
_DAILY_REMOVE_CHECKS = ("above_50dma", "earnings")
```

Replace the whole `_update_watchlist_membership` function (lines 218-243) with:

```python
def _update_watchlist_membership(symbol: str, report: FilterReport, conn, asof) -> None:
    """Demote an active name only when a still-daily check fails.

    Promotion onto the watchlist happens in screening.discovery (velocity);
    structural entry-gating happens in jobs.screen. The morning job's job here
    is just to drop names that fail a daily, time-sensitive check.
    """
    failures = _failed_checks(report, _DAILY_REMOVE_CHECKS)
    if not failures:
        return
    note = f"daily fail: {', '.join(failures)}"
    conn.execute(
        "UPDATE watchlist SET status='removed', notes=? WHERE symbol=?",
        (note, symbol),
    )
    log.info("morning: %s removed from watchlist (%s)", symbol, note)
```

(`_failed_checks` is unchanged and still used.)

- [ ] **Step 4: Run the pruning tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_morning_pruning.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Run the FULL suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: all green. If `tests/test_morning_velocity.py` or `tests/test_morning_alert_notes.py` reference removed symbols, they should be unaffected (they test `_velocity_ratio`/`_alert_notes`/`_alert_payload`, which this plan does not touch).

- [ ] **Step 6: Commit**

```bash
git add src/gexwheel/jobs/morning.py tests/test_morning_pruning.py
git commit -m "feat(morning): demote active names only on daily checks (above_50dma, earnings)"
```

---

### Task 4: Refresh the pipeline narrative in docs

**Files:**
- Modify: `README.md` (pipeline diagram), `AGENTS.md` (architecture quick-reference)

- [ ] **Step 1: Update the README pipeline diagram**

Replace the ASCII pipeline at the top of `README.md` so it reflects three stages: `screen` (every ~21d) → `primary_watchlist`; `mentions_daily` (velocity on primary members) → active `watchlist`; `morning` (GEX/proximity/daily gates) → identified trades. Keep it concise; the spec is authoritative. Mention that structural qualification happens in the periodic screen, not every morning.

- [ ] **Step 2: Update AGENTS.md architecture block**

Update the "Architecture (quick reference)" diagram similarly (screen → primary; daily velocity on primary → watchlist; morning on watchlist). Keep the SQLite spine line including `primary_watchlist` and `app_state` (added in A1).

- [ ] **Step 3: Run the full suite & commit**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: all green.

```bash
git add README.md AGENTS.md
git commit -m "docs: pipeline narrative reflects screen -> primary -> velocity -> morning"
```

---

## Self-review notes (for the executor)

- After every task: `PYTHONPATH=src pytest` must stay green.
- Frozen contracts respected: no `models.py` field renames, no `schema.sql` change, `run_discovery`/`run_filters`/`db.*` public signatures unchanged. (`_process_symbol`/`_update_watchlist_membership` are private — safe to re-sign.)
- `run_filters` is still invoked in morning; only the *actions* taken on its results changed. The alert path `if report.passed and should_alert(...)` is intentionally unchanged — for an already-screened active name the structural checks pass, so the effective alert gate is the daily set (iv_rank/vrp/above_50dma/earnings/regime/proximity).
- Discord is still present after this plan; Plan B1 removes it. Execute A1 → A2 → B1 → B2.
