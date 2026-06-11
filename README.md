# gexwheel

Gamma-wall driven wheel-entry alerting. Daily Reddit mention-velocity discovery
feeds a hard screening gate; survivors get GEX profiles computed from option
open interest; Discord alerts fire when spot approaches a persistent put wall.

```
mentions_daily (every day 07:00 ET)          morning (Mon-Fri 07:15 ET)
┌──────────────────────────────┐   ┌─────────────────────────────────────────┐
│ ApeWisdom/PRAW mention pull  │   │ for each watchlist + discovery ticker:  │
│ -> mentions table            │   │   yfinance chain -> GEX profile/walls   │
│ -> velocity (3x over 7d avg, │   │   IV rank / realized vol / VRP          │
│    baseline floor 10)        │   │   Stage-2 hard filters (no overrides)   │
│ -> tickers table (candidates)│   │   wall-break -> bench w/ cooldown       │
└──────────────────────────────┘   │   persistent wall + proximity -> score  │
                                   │   -> Discord webhook (top N embeds)     │
                                   └─────────────────────────────────────────┘
                       SQLite (/data/gexwheel.db) is the spine: mentions,
                       gex_snapshots, vol_stats, tickers, watchlist, alerts
```

Key design facts:
- **OI updates once daily**, so free delayed data (yfinance) is sufficient for
  walls. Real-time data only matters for intraday proximity alerts (not in v1).
- Walls are **Schelling points**, not physics - the edge is that everyone
  computes them the same way. Persistence (same strike >= 2 days) is required
  before a wall is trusted.
- **IV rank needs history**: the system self-bootstraps `vol_stats` daily; for
  the first ~4 weeks iv_rank is None and the gate fails closed (by design).
  Seed faster by backfilling iv_atm from any historical source if impatient.
- This produces **decision support, not financial advice**. Position sizing
  rules (max assignment per ticker etc.) live in your head, not in code - the
  footer of every alert says so.

## Status / build order
Reference modules (DONE, tested): `analytics/gex.py`, `analytics/velocity.py`,
`models.py`, `db.py`, `config.py`, `__main__.py`.
To implement (specs in each module docstring, order matters):
see **IMPLEMENTATION_GUIDE.md**.

## Dev quickstart
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src pytest          # gex + velocity known-answer tests must pass
```
Deploy: `deploy/INSTALL.md`.
