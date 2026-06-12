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
                       SQLite (gexwheel.db) is the spine: mentions,
                       gex_snapshots, vol_stats, tickers, watchlist, alerts
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
2. prompts for your **Discord webhook URL** (required, input hidden) and
   **Reddit/PRAW API credentials** (optional, input hidden - ApeWisdom needs
   no key, PRAW just adds a fallback source);
3. writes `~/gexwheel-data/config.yaml` with permissions `600`;
4. installs and enables systemd user timers for the two jobs and offers a
   one-shot `test-discord` to verify the webhook.

Non-interactive installs: set `GEXWHEEL_WEBHOOK_URL` (and optionally
`GEXWHEEL_PRAW_CLIENT_ID` / `GEXWHEEL_PRAW_CLIENT_SECRET`) in the environment.
Paths are overridable via `GEXWHEEL_INSTALL_DIR` and `GEXWHEEL_DATA_DIR`.

Re-running the installer updates the code and dependencies but keeps your
existing `config.yaml`; delete it and re-run to reconfigure. A
container-based deployment (podman quadlets) is documented in
[deploy/INSTALL.md](./deploy/INSTALL.md).

## Configuration

All tunables (price band, OI/spread/IV-rank gates, proximity thresholds,
cooldowns, subreddit filter, ...) live in `~/gexwheel-data/config.yaml` and
are documented inline in
[config/config.example.yaml](./config/config.example.yaml). The file contains
your webhook URL and any PRAW credentials - keep it private (the installer
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
analytics, data adapters, discovery, filters, alert scoring/Discord delivery,
jobs, config, models, migrations, and SQLite helpers.

The historical build order and live smoke-test checklist are documented in
**IMPLEMENTATION_GUIDE.md**. Live checks still require configured external
services such as ApeWisdom/yfinance/Discord.

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
python -m gexwheel morning        # weekday GEX + screen + alerts
python -m gexwheel test-discord   # one-shot webhook sanity check
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
