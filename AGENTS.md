# gexwheel — Cursor agent guide

Gamma-wall driven wheel-entry alerting. Daily Reddit mention-velocity discovery feeds a
hard screening gate; survivors get GEX profiles from option OI; identified trades are
published to a dashboard when spot approaches a persistent put wall.

**Authoritative build spec:** [IMPLEMENTATION_GUIDE.md](./IMPLEMENTATION_GUIDE.md)

## Agent workflow

1. Read the target module's **module docstring** — it is the spec. Implement exactly that.
2. Work **one build-order step at a time** (see IMPLEMENTATION_GUIDE.md table).
3. After each change, run `PYTHONPATH=src pytest` — gex/velocity tests are the regression net.
4. Use the step's **Verify by** column before moving on.
5. If a spec is ambiguous, pick the simpler behavior and leave a `# NOTE:` comment.

## Ground rules (non-negotiable)

- **Do not change** `models.py` field names, `schema.sql`, or public signatures in stub modules.
- **No new dependencies** beyond `requirements.txt` without strong reason.
- **Network code:** stdlib `logging`, timeouts on every request, exponential-backoff retry, and never let one symbol's failure kill a run.
- **Dates:** always use `zoneinfo.ZoneInfo(cfg['timezone'])` — never bare `date.today()` (containers run UTC).
- **SQLite:** single writer; jobs run serially via timers — do not add threading.

## Implementation status

| Status | Modules |
|--------|---------|
| IMPLEMENTED (local tests) | `analytics/gex.py`, `analytics/velocity.py`, `analytics/vol.py`, `models.py`, `db.py`, `config.py`, `__main__.py`, `data/mentions.py`, `data/chains.py`, `data/prices.py`, `screening/discovery.py`, `screening/filters.py`, `screening/primary.py`, `screening/chain_metrics.py`, `alerts/scoring.py`, `jobs/mentions_daily.py`, `jobs/morning.py`, `jobs/screen.py` |
| LIVE/ENV VERIFICATION | ApeWisdom/yfinance smoke checks from `IMPLEMENTATION_GUIDE.md` require configured external services. |

Historical build order and verification commands: **IMPLEMENTATION_GUIDE.md**.

## Dev commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src pytest                    # must stay green after every step
python -m gexwheel mentions              # step 4 smoke test
```

Deploy: [deploy/INSTALL.md](./deploy/INSTALL.md) (Turso + GitHub Actions + Cloudflare Pages).
Dashboard: [web/README.md](./web/README.md).

## Known gotchas

- yfinance `option_chain()` off-hours returns bid/ask of 0 — filters define `no_quote` FAIL behavior.
- yfinance throttles: respect `request_sleep_s` between **every** chain call, not just per symbol.
- ApeWisdom field names are unofficial — verify `results[0].keys()` on first run; adapt mapping in ONE place.
- IV rank needs ~20+ days of `vol_stats` history; None fails closed until then (by design).

## Architecture (quick reference)

```
screen (periodic, ~3 weeks)       mentions_daily (07:00 ET)       morning (Mon–Fri 07:15 ET)
  ApeWisdom → primary screen        velocity on primary members     active watchlist → GEX → filters → dashboard
  → primary_watchlist               → promote to active watchlist   (structural gate is in screen, not morning)
```

SQLite spine: `mentions`, `gex_snapshots`, `vol_stats`, `tickers`, `watchlist`, `alerts`, `primary_watchlist`, `app_state`.
