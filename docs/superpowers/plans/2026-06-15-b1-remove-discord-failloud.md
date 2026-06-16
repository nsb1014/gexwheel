# Plan B1 — Remove Discord + fail-loud jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Discord delivery path entirely from code and documentation (the Cloudflare dashboard becomes the sole output surface), and make the jobs exit non-zero on material failures so GitHub Actions emails the operator.

**Architecture:** The morning job stops pushing to Discord and instead persists every identified trade to the `alerts` table (the dashboard reads them). `should_alert` dedups by existence (not delivery). `alerts/discord.py` and its test are deleted; the `test-discord` CLI, the `discord:` config block, and all doc references go away. A new `JobError` plus targeted `raise`s in the fetch paths make data-outage failures visible via GitHub's failed-run emails.

**Tech Stack:** Python stdlib, `sqlite3` in-memory via `gexwheel.db.connect(":memory:")`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-15-cloud-hosting-and-dashboard-design.md`

**Prerequisite:** Plans A1 + A2 merged.

---

## Background

The operator is removing the Discord webhook; the dashboard (Plan B2) replaces it. Discord must be removed from code *and docs* so future agents are not misled. Separately, the move to GitHub-Actions hosting means ops alerting comes from GitHub's failed-run emails — so the jobs must actually *fail* (non-zero exit) when something material breaks, instead of logging and exiting 0.

Discord footprint to remove (from `rg -i 'discord|webhook'`): `alerts/discord.py`, `tests/test_discord.py`, `__main__.py` (`test-discord`), `config.py` (`REQUIRED_KEYS`), `config/config.example.yaml` (`discord:` block), `jobs/morning.py` (send path + import), plus docstring/comment mentions in `alerts/scoring.py`, `__init__.py`, `models.py`, `screening/filters.py`, `data/prices.py`, and the docs.

`AlertCard`, `scoring.score`, `scoring.suggested_entry`, and `scoring.should_alert` are KEPT — they identify and rank the trades; only delivery is removed.

---

## File structure

- Delete: `src/gexwheel/alerts/discord.py`, `tests/test_discord.py`.
- Modify: `src/gexwheel/jobs/morning.py` — drop Discord import/send; add `_persist_trades`.
- Modify: `src/gexwheel/alerts/scoring.py` — existence-based dedup + docstring rewording.
- Modify: `src/gexwheel/__main__.py` — remove `test-discord`.
- Modify: `src/gexwheel/config.py` — drop `"discord"` from `REQUIRED_KEYS`.
- Modify: `config/config.example.yaml` — remove `discord:` block.
- Create: `src/gexwheel/jobs/__init__.py` content — `JobError`.
- Modify: `src/gexwheel/jobs/screen.py`, `src/gexwheel/screening/discovery.py` — fail-loud raises.
- Modify (docstrings/comments only): `src/gexwheel/__init__.py`, `src/gexwheel/models.py`, `src/gexwheel/screening/filters.py`, `src/gexwheel/data/prices.py`.
- Modify (docs): `README.md`, `AGENTS.md`, `IMPLEMENTATION_GUIDE.md`, `docs/superpowers/plans/README.md`, `deploy/*`.
- Tests: `tests/test_scoring.py` (update), `tests/test_morning_trades.py` (create), `tests/test_screen_job.py` (update abort case), `tests/test_failloud.py` (create).

---

### Task 1: Morning persists trades instead of pushing to Discord

**Files:**
- Modify: `src/gexwheel/jobs/morning.py`
- Test: `tests/test_morning_trades.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_morning_trades.py`:

```python
"""Morning persists every identified trade (no Discord, no truncation)."""
from __future__ import annotations

from datetime import date

from gexwheel import db as gdb
from gexwheel.jobs.morning import _persist_trades
from gexwheel.models import AlertCard


def _card(symbol, score):
    return AlertCard(
        symbol=symbol, alert_type="put_wall_entry", spot=20.0, put_wall=18.0,
        call_wall=22.0, zero_gamma=20.0, regime="positive", iv_rank=70.0,
        vrp=0.1, score=score, suggested_entry="CSP 18P", notes="",
    )


def test_persist_trades_logs_all_with_timestamp():
    conn = gdb.connect(":memory:")
    asof = date(2026, 6, 15)
    cards = [_card("AAA", 90.0), _card("BBB", 80.0)]
    payloads = {
        ("AAA", "put_wall_entry"): {"spot": 20.0, "score": 90.0},
        ("BBB", "put_wall_entry"): {"spot": 20.0, "score": 80.0},
    }
    _persist_trades(conn, cards, payloads, asof, "2026-06-15T07:20:00")
    rows = conn.execute(
        "SELECT symbol, sent_at FROM alerts WHERE date=? ORDER BY symbol", (asof.isoformat(),)
    ).fetchall()
    assert [r["symbol"] for r in rows] == ["AAA", "BBB"]
    assert all(r["sent_at"] == "2026-06-15T07:20:00" for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_morning_trades.py -v`
Expected: FAIL with `ImportError: cannot import name '_persist_trades'`.

- [ ] **Step 3: Edit `morning.py`**

Remove the Discord import (line 40): delete `from ..alerts import discord as disc`.

Replace the `if cards:` send/log block in `run()` (currently lines 94-114) with:

```python
    # Persist every identified trade (the dashboard is the delivery surface now).
    if cards:
        now_iso = datetime.now(tz).isoformat()
        _persist_trades(conn, cards, alert_payloads, asof, now_iso)
        log.info("morning: identified %d trades", len(cards))
    else:
        log.info("morning: no trades identified")

    conn.commit()
    conn.close()
```

(Keep the existing trailing `conn.commit(); conn.close()` only once — if the original had a commit/close after the block, ensure there is exactly one commit and one close at the end of `run()`.)

Add the helper near the other private helpers (e.g. after `_velocity_ratio`):

```python
def _persist_trades(conn, cards, alert_payloads, asof, now_iso: str) -> None:
    """Write every identified trade to the alerts table, highest score first.

    There is no separate delivery step anymore: the dashboard reads these rows,
    so sent_at records the identification/publish time for every trade.
    """
    for card in sorted(cards, key=lambda c: c.score, reverse=True):
        gdb.log_alert(
            conn, card.symbol, asof, card.alert_type,
            alert_payloads[(card.symbol, card.alert_type)],
            now_iso,
        )
```

Update the `run()` docstring (lines 29-31) to describe persisting trades to the dashboard instead of `discord.send_alerts`, and the one-line summary wording (`fire Discord alerts` on line 58 → `identify and persist trades`).

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH=src python3 -m pytest tests/test_morning_trades.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gexwheel/jobs/morning.py tests/test_morning_trades.py
git commit -m "feat(morning): persist identified trades for the dashboard (no Discord push)"
```

---

### Task 2: Existence-based dedup + scoring docstring rewording

**Files:**
- Modify: `src/gexwheel/alerts/scoring.py`
- Test: `tests/test_scoring.py` (update two tests)

- [ ] **Step 1: Update the dedup tests**

In `tests/test_scoring.py`, replace the last two tests (lines 57-68) with:

```python
def test_should_alert_dedups_when_trade_already_identified_today():
    conn = _conn_with_recent_walls(18.0, 18.0)
    gdb.log_alert(conn, "TEST", ASOF, "put_wall_entry", {}, sent_at="2026-06-10T07:20:00")

    assert should_alert(_profile(spot=18.0), _cfg(), conn, ASOF) is False


def test_should_alert_dedups_regardless_of_sent_at_value():
    # Every identified trade is published to the dashboard, so a NULL sent_at
    # row still counts as "already identified today".
    conn = _conn_with_recent_walls(18.0, 18.0)
    gdb.log_alert(conn, "TEST", ASOF, "put_wall_entry", {}, sent_at=None)

    assert should_alert(_profile(spot=18.0), _cfg(), conn, ASOF) is False
```

- [ ] **Step 2: Run tests to verify the second one fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_scoring.py -v`
Expected: `test_should_alert_dedups_regardless_of_sent_at_value` FAILS (current code only dedups on `sent_at IS NOT NULL`).

- [ ] **Step 3: Implement existence-based dedup + reword docstrings**

In `src/gexwheel/alerts/scoring.py`, replace the dedup block in `should_alert` (lines 80-87) with:

```python
    # Dedup: one identified trade per (symbol, day, type). Every identified
    # trade is published to the dashboard, so existence — not delivery — dedups.
    dup = conn.execute(
        "SELECT 1 FROM alerts WHERE symbol=? AND date=? AND type=?",
        (profile.symbol, asof.isoformat(), "put_wall_entry"),
    ).fetchone()
    return dup is None
```

Reword the Discord mentions in docstrings (no logic change):
- Module docstring line ~16-17: `Simple 0-100 composite for ranking which alerts to send first (max_alerts_per_run caps the Discord batch):` → `Simple 0-100 composite for ranking trades (highest score shown first on the dashboard):`
- The `should_alert` docstring's `* no duplicate: no DELIVERED row (sent_at set) ...` (lines ~11-13) → `* no duplicate: no existing alerts row for (symbol, asof, 'put_wall_entry') — one identified trade per day.`
- `suggested_entry` docstring (line 123): `Human-readable entry suggestion for the Discord card.` → `Human-readable entry suggestion for the dashboard.`

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_scoring.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gexwheel/alerts/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): dedup identified trades by existence; drop Discord delivery semantics"
```

---

### Task 3: Delete `discord.py`, its test, and the `test-discord` CLI

**Files:**
- Delete: `src/gexwheel/alerts/discord.py`, `tests/test_discord.py`
- Modify: `src/gexwheel/__main__.py`

- [ ] **Step 1: Delete the files**

```bash
git rm src/gexwheel/alerts/discord.py tests/test_discord.py
```

- [ ] **Step 2: Remove `test-discord` from `__main__.py`**

In `src/gexwheel/__main__.py`:
- Delete the docstring line `  python -m gexwheel test-discord      # one-shot webhook sanity check` (line 5).
- Delete `sub.add_parser("test-discord")` (line 27).
- Delete the dispatch branch (lines 40-44):

```python
    elif args.cmd == "test-discord":
        from .alerts.discord import test_webhook
        ok = test_webhook(cfg)
        print("webhook OK" if ok else "webhook FAILED")
        return 0 if ok else 1
```

- [ ] **Step 3: Verify nothing imports discord anymore**

Run: `rg -n "alerts.discord|test_webhook|send_alerts|format_card" src/ tests/`
Expected: no matches.

- [ ] **Step 4: Run the full suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: green (test_discord.py is gone; nothing references discord).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: delete Discord delivery module, test, and test-discord CLI"
```

---

### Task 4: Drop `discord` from config

**Files:**
- Modify: `src/gexwheel/config.py`, `config/config.example.yaml`

- [ ] **Step 1: Update `REQUIRED_KEYS`**

In `src/gexwheel/config.py`, change line 14 to drop `"discord"`:

```python
REQUIRED_KEYS = ["db_path", "data", "reddit", "discovery", "screen", "filters", "alerts"]
```

- [ ] **Step 2: Remove the `discord:` block from the example config**

In `config/config.example.yaml`, delete the entire `discord:` block (lines 4-7):

```yaml
discord:
  webhook_url: "https://discord.com/api/webhooks/CHANGE_ME"
  username: "GEX Wheel"
  max_alerts_per_run: 8          # don't spam the channel
```

- [ ] **Step 3: Confirm no code reads `cfg["discord"]`**

Run: `rg -n '\["discord"\]|discord\b' src/`
Expected: no live references (docstrings already reworded in Task 2; if any remain, reword them).

- [ ] **Step 4: Run the full suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: green. (If a test cfg dict still includes a `discord` key, that's harmless — extra keys are ignored — but remove obvious ones in `tests/test_morning_candidates.py` for clarity.)

- [ ] **Step 5: Commit**

```bash
git add src/gexwheel/config.py config/config.example.yaml
git commit -m "chore(config): remove discord block and required key"
```

---

### Task 5: Reword remaining Discord mentions in code comments/docstrings

**Files:**
- Modify: `src/gexwheel/__init__.py`, `src/gexwheel/models.py`, `src/gexwheel/screening/filters.py`, `src/gexwheel/data/prices.py`

- [ ] **Step 1: Reword each (no logic change)**

- `src/gexwheel/__init__.py` line 8: `  Discord alerts when spot approaches a qualifying put wall.` →
  `  dashboard-published trades when spot approaches a qualifying put wall.`
- `src/gexwheel/models.py` line 76: `"""Everything the Discord formatter needs to render one alert embed."""` →
  `"""Everything the dashboard needs to render one identified trade."""`
- `src/gexwheel/screening/filters.py` line 45: `so the caller gets the full picture for logging/Discord notes."""` →
  `so the caller gets the full picture for logging/dashboard notes."""`
- `src/gexwheel/data/prices.py` line 15: `= 'unknown' so it shows in the Discord card).` →
  `= 'unknown' so it shows on the dashboard).`

- [ ] **Step 2: Verify no live Discord references remain in `src/`**

Run: `rg -in 'discord|webhook' src/`
Expected: no matches.

- [ ] **Step 3: Run the full suite & commit**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: green.

```bash
git add src/gexwheel/__init__.py src/gexwheel/models.py src/gexwheel/screening/filters.py src/gexwheel/data/prices.py
git commit -m "docs: reword code comments from Discord to dashboard"
```

---

### Task 6: Scrub Discord from project documentation

**Files:**
- Modify: `README.md`, `AGENTS.md`, `IMPLEMENTATION_GUIDE.md`, `docs/superpowers/plans/README.md`, `deploy/gexwheel-morning.container`, `deploy/INSTALL.md`, `install.sh`

> NOTE: `deploy/*` and `install.sh` are fully replaced/retired in Plan B2. Here, only remove the *Discord* references so an interim state isn't misleading; B2 deletes the files outright. If B2 is executed immediately after B1, you may instead leave `deploy/`/`install.sh` for B2 to delete and skip them here — but still scrub README/AGENTS/IMPLEMENTATION_GUIDE.

- [ ] **Step 1: README.md** — remove Discord from the pipeline diagram and prose: the "Discord webhook (top N embeds)" box becomes "dashboard (active watchlist + the day's trades)"; the install step prompting for "Discord webhook URL" is removed (B2 rewrites install fully, but scrub the mention now); the `test-discord` CLI line is removed from the entrypoints list; the Disclaimer's data-source line stays.

- [ ] **Step 2: AGENTS.md** — remove `alerts/discord.py` from the IMPLEMENTED status row; remove the `test-discord` smoke-test line from Dev commands; change the architecture diagram's `→ Discord` to `→ dashboard`; drop the Discord gotcha if present.

- [ ] **Step 3: IMPLEMENTATION_GUIDE.md** — build-order rows 9 (`alerts/discord.py` / `test-discord`) reference Discord; since the guide is historical, add a one-line note at the top: `Discord delivery was removed in the 2026-06-15 cloud migration (see docs/superpowers/specs/2026-06-15-cloud-hosting-and-dashboard-design.md); rows referencing it are historical.` Do not rewrite historical rows.

- [ ] **Step 4: docs/superpowers/plans/README.md** — add a one-line note under the status banner: `Discord delivery removed in the 2026-06-15 cloud migration; historical references below predate it.` Leave completed-plan history intact.

- [ ] **Step 5: deploy + install.sh** — if not deferring to B2: in `deploy/gexwheel-morning.container` change the `Description=` to drop "Discord alerts"; in `deploy/INSTALL.md` and `install.sh` remove Discord-webhook prompts/env (`GEXWHEEL_WEBHOOK_URL`) references.

- [ ] **Step 6: Verify**

Run: `rg -in 'discord|webhook' README.md AGENTS.md IMPLEMENTATION_GUIDE.md`
Expected: only the intentional historical pointer notes remain (no live instructions).

- [ ] **Step 7: Commit**

```bash
git add README.md AGENTS.md IMPLEMENTATION_GUIDE.md docs/superpowers/plans/README.md deploy/ install.sh
git commit -m "docs: remove Discord from project documentation"
```

---

### Task 7: Fail-loud jobs (non-zero exit on material failures)

**Files:**
- Modify: `src/gexwheel/jobs/__init__.py` (add `JobError`)
- Modify: `src/gexwheel/jobs/screen.py`, `src/gexwheel/screening/discovery.py`
- Test: `tests/test_failloud.py` (create), `tests/test_screen_job.py` (update abort case)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_failloud.py`:

```python
"""Material data-outage failures must raise (so GitHub emails the operator)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from gexwheel import db as gdb
from gexwheel.data.mentions import MentionFetchError
from gexwheel.jobs import JobError
from gexwheel.jobs import screen as screen_job
from gexwheel.screening.discovery import run_discovery

ASOF = date(2026, 6, 10)


def test_discovery_raises_when_all_sources_fail():
    conn = gdb.connect(":memory:")
    gdb.upsert_primary(conn, "AAA", ASOF, metrics={"spot": 20.0})
    conn.commit()
    cfg = {
        "reddit": {"source": "apewisdom", "apewisdom_filter": "wsb", "apewisdom_pages": 1},
        "discovery": {"velocity_trigger": 3.0, "baseline_floor": 10,
                      "min_history_days": 5, "max_daily_mentions": 1000},
    }
    with patch("gexwheel.screening.discovery.fetch_apewisdom",
               side_effect=MentionFetchError("down")):
        with pytest.raises(MentionFetchError):
            run_discovery(conn, cfg, ASOF)


def test_screen_raises_on_universe_failure(tmp_path):
    cfg = {
        "db_path": str(tmp_path / "g.db"), "timezone": "America/New_York",
        "data": {"chain_source": "yfinance", "max_dte": 60, "request_sleep_s": 0, "request_retries": 1},
        "reddit": {"apewisdom_filter": "wsb"},
        "screen": {"primary_screen_interval_days": 21, "screen_pages": 5,
                   "avg_volume_days": 20, "min_avg_volume": 1_000_000},
        "filters": {"price_min": 10.0, "price_max": 45.0, "min_open_interest": 500,
                    "max_spread_pct": 0.10, "min_vrp": 0.0,
                    "excluded_sectors": [], "excluded_symbols": []},
    }
    conn = gdb.connect(cfg["db_path"])
    gdb.upsert_primary(conn, "KEEP", ASOF, metrics={"spot": 20.0})
    conn.commit()
    conn.close()
    with patch("gexwheel.jobs.screen.fetch_apewisdom", side_effect=MentionFetchError("down")):
        with pytest.raises(JobError):
            screen_job.run(cfg, force=True)
    # primary still intact (no destructive wipe before the raise)
    conn = gdb.connect(cfg["db_path"])
    assert gdb.primary_symbols(conn) == ["KEEP"]
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src python3 -m pytest tests/test_failloud.py -v`
Expected: FAIL — `JobError` does not exist; discovery/screen currently return instead of raise.

- [ ] **Step 3: Add `JobError`**

Set the contents of `src/gexwheel/jobs/__init__.py` to:

```python
"""Scheduled jobs package."""
from __future__ import annotations


class JobError(RuntimeError):
    """Raised when a job hits a material failure that should fail the run
    (non-zero exit) so the scheduler/CI surfaces it (e.g. GitHub email)."""
```

- [ ] **Step 4: Make discovery raise on total fetch failure**

In `src/gexwheel/screening/discovery.py`, change the two fetch-failure `return []` paths to raise. The praw-fallback failure path becomes:

```python
    if not records and source in ("praw", "both"):
        try:
            from ..data.mentions import fetch_praw
            records = fetch_praw(cfg, asof)
        except Exception as exc:
            log.error("praw fetch also failed: %s", exc)
            raise MentionFetchError(f"all mention sources failed for {asof}") from exc
```

And the subsequent no-records guard:

```python
    if not records:
        raise MentionFetchError(f"no mention records retrieved for {asof}")
```

(`MentionFetchError` is already imported in `discovery.py`.) Keep the later "primary empty" and "no primary-member mentions" `return []` paths unchanged — those are legitimate empties, not outages.

- [ ] **Step 5: Make screen raise on universe failure / empty universe**

In `src/gexwheel/jobs/screen.py`, add to the imports:

```python
from . import JobError
```

Change the universe-fetch `except` to raise after logging:

```python
        except MentionFetchError as exc:
            log.error("screen: universe pull failed (%s) — aborting without changes", exc)
            raise JobError(f"screen universe pull failed for {asof}") from exc
```

And the empty-universe guard:

```python
        if not universe:
            log.warning("screen: empty universe for %s — aborting", asof)
            raise JobError(f"screen universe empty for {asof}")
```

(Both happen before any mutation, so the primary list is untouched — the test asserts this.)

- [ ] **Step 6: Update the A1 abort test for the new raise**

In `tests/test_screen_job.py`, replace `test_screen_aborts_without_wiping_on_fetch_failure` with:

```python
def test_screen_aborts_without_wiping_on_fetch_failure(tmp_path):
    from gexwheel.jobs import JobError
    cfg = _cfg(tmp_path)
    conn = gdb.connect(cfg["db_path"])
    gdb.upsert_primary(conn, "KEEP", ASOF - timedelta(days=21), metrics={"spot": 20.0})
    conn.commit()
    conn.close()
    with patch("gexwheel.jobs.screen.fetch_apewisdom",
               side_effect=MentionFetchError("down")):
        with pytest.raises(JobError):
            screen_job.run(cfg, force=True)
    conn = gdb.connect(cfg["db_path"])
    assert gdb.primary_symbols(conn) == ["KEEP"]  # untouched
```

(`pytest` is already imported in that test file.)

- [ ] **Step 7: Run the targeted tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_failloud.py tests/test_screen_job.py -v`
Expected: all PASS.

- [ ] **Step 8: Run the FULL suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: all green. `mentions_daily.run` already lets `run_discovery` exceptions propagate (it only wraps `conn.close()` in `finally`), and `__main__` runs `sys.exit(main())`, so a propagating exception yields a non-zero exit — no `__main__` change needed for fail-loud.

- [ ] **Step 9: Commit**

```bash
git add src/gexwheel/jobs/__init__.py src/gexwheel/jobs/screen.py src/gexwheel/screening/discovery.py tests/test_failloud.py tests/test_screen_job.py
git commit -m "feat(jobs): fail-loud on data-outage failures so CI emails the operator"
```

---

## Self-review notes (for the executor)

- After every task: `PYTHONPATH=src pytest` must stay green.
- Frozen contracts respected: `models.py` fields unchanged (only a docstring), `schema.sql` unchanged (the `alerts.sent_at` column is reused, not altered), `db.*`/`run_filters`/`run_discovery`/`should_alert`/`score` public signatures unchanged.
- `rg -in 'discord|webhook' src/ tests/` must return nothing after Task 5; `rg -in 'discord|webhook'` across the repo should return only the intentional historical doc pointers.
- Execution order: A1 → A2 → **B1** → B2. Task 7 updates a test that A1 created — expected, since fail-loud is a B-subsystem (ops/email) concern layered on A1's job.
