# gexwheel

Gamma-wall driven wheel-entry alerting. Daily Reddit mention-velocity discovery
feeds a hard screening gate; survivors get GEX profiles computed from option
open interest; identified trades are published to a dashboard when spot approaches a persistent put wall.

```
screen (~21d)                    mentions_daily (07:00 ET)          morning (Mon-Fri 07:15 ET)
┌────────────────────────────┐   ┌──────────────────────────────┐   ┌─────────────────────────────────────────┐
│ ApeWisdom → structural     │   │ velocity on primary members  │   │ active watchlist only:                  │
│ screen → primary_watchlist │   │ → promote to active watchlist│   │ chains → GEX → filters → dashboard    │
└────────────────────────────┘   └──────────────────────────────┘   └─────────────────────────────────────────┘
                       Turso (hosted libSQL) is the spine in production; local dev uses SQLite.
```

## Deploy

gexwheel runs on free cloud services — no personal machine required.

- **Jobs:** GitHub Actions cron (`.github/workflows/`)
- **Database:** Turso (hosted libSQL)
- **Dashboard:** Cloudflare Pages (`web/`)

Full setup: [deploy/INSTALL.md](./deploy/INSTALL.md). Dashboard: [web/README.md](./web/README.md).

## Configuration

All tunables (price band, OI/spread/IV-rank gates, proximity thresholds,
cooldowns, subreddit filter, ...) are documented inline in
[config/config.example.yaml](./config/config.example.yaml). GitHub Actions jobs
use those defaults plus `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` from repo secrets.
For local runs, copy the example to `config/config.yaml` or set `GEXWHEEL_CONFIG`.

## Key design facts

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

## Status

The core pipeline modules are implemented and covered by local tests:
analytics, data adapters, discovery, filters, alert scoring, jobs, config, models,
migrations, and SQLite helpers. Identified trades are published to the Cloudflare dashboard.

The historical build order and live smoke-test checklist are documented in
**IMPLEMENTATION_GUIDE.md**. Live checks still require configured external
services such as ApeWisdom/yfinance.

## Developing

```bash
git clone https://github.com/nsb1014/gexwheel.git && cd gexwheel
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src pytest          # gex + velocity known-answer tests must pass
```

Useful CLI entrypoints (see `python -m gexwheel --help`):

```bash
python -m gexwheel mentions       # daily Reddit scan
python -m gexwheel screen [--force]  # periodic primary-watchlist screen
python -m gexwheel morning        # weekday GEX + identify trades
python -m gexwheel show SYMBOL    # dump latest stored GEX snapshot
```

Contributor ground rules are in [AGENTS.md](./AGENTS.md). Cloud deploy:
[deploy/INSTALL.md](./deploy/INSTALL.md); dashboard: [web/README.md](./web/README.md).

## License

MIT - see [LICENSE](./LICENSE).

## Disclaimer

This software is provided for educational and informational purposes only. It
produces decision support, not financial advice; you are solely responsible
for any trades you place. Data comes from free, delayed, unofficial sources
(yfinance, ApeWisdom) that can be wrong, stale, or rate-limited.
