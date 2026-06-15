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
                       SQLite (gexwheel.db) is the spine: mentions, gex_snapshots,
                       vol_stats, tickers, watchlist, alerts, primary_watchlist, app_state
```

## Install (Linux)

Requirements: Linux with `git` and Python 3.10+ (with venv support, e.g.
`sudo apt install git python3-venv`). A systemd user session is used for
scheduling if available; otherwise the installer tells you what to schedule
yourself.

```bash
curl -fsSL https://raw.githubusercontent.com/nsb1014/gexwheel/main/install.sh | bash
```

The installer:

1. clones the repo to `~/.local/share/gexwheel/app` (or installs in place if
   you run `./install.sh` from a checkout) and builds a private virtualenv;
2. prompts for **Reddit/PRAW API credentials** (optional, input hidden - ApeWisdom needs
   no key, PRAW just adds a fallback source);
3. writes `~/gexwheel-data/config.yaml` with permissions `600`;
4. installs and enables systemd user timers for the jobs.

Non-interactive installs: set `GEXWHEEL_PRAW_CLIENT_ID` / `GEXWHEEL_PRAW_CLIENT_SECRET`
in the environment if using PRAW.
Paths are overridable via `GEXWHEEL_INSTALL_DIR` and `GEXWHEEL_DATA_DIR`.

Re-running the installer updates the code and dependencies but keeps your
existing `config.yaml`; delete it and re-run to reconfigure. A
container-based deployment (podman quadlets) is documented in
[deploy/INSTALL.md](./deploy/INSTALL.md).

## Configuration

All tunables (price band, OI/spread/IV-rank gates, proximity thresholds,
cooldowns, subreddit filter, ...) live in `~/gexwheel-data/config.yaml` and
are documented inline in
[config/config.example.yaml](./config/config.example.yaml). Keep the file private (the installer
sets `chmod 600`).

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
migrations, and SQLite helpers. Identified trades are read by the Cloudflare dashboard (Plan B2).

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

Contributor ground rules are in [AGENTS.md](./AGENTS.md); deployment details
in [deploy/INSTALL.md](./deploy/INSTALL.md).

## License

MIT - see [LICENSE](./LICENSE).

## Disclaimer

This software is provided for educational and informational purposes only. It
produces decision support, not financial advice; you are solely responsible
for any trades you place. Data comes from free, delayed, unofficial sources
(yfinance, ApeWisdom) that can be wrong, stale, or rate-limited.
