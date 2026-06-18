"""CLI entrypoint. FULLY IMPLEMENTED.

  python -m gexwheel mentions          # daily Reddit scan
  python -m gexwheel screen [--force]  # periodic primary-watchlist screen
  python -m gexwheel morning-snapshot  # ~10:15 ET spot₀ for active watchlist
  python -m gexwheel morning           # ~10:45 ET GEX + identify trades
  python -m gexwheel show <SYMBOL>     # dump latest stored GEX snapshot (debug)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import db
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="gexwheel")
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mentions")
    screen_p = sub.add_parser("screen")
    screen_p.add_argument("--force", action="store_true",
                          help="ignore the interval throttle and re-screen now")
    sub.add_parser("morning")
    sub.add_parser("morning-snapshot")
    show = sub.add_parser("show")
    show.add_argument("symbol")
    args = p.parse_args(argv)

    cfg = load_config(args.config)

    if args.cmd == "mentions":
        from .jobs import mentions_daily
        mentions_daily.run(cfg)
    elif args.cmd == "screen":
        from .jobs import screen as screen_job
        screen_job.run(cfg, force=args.force)
    elif args.cmd == "morning":
        from .jobs import morning
        morning.run(cfg)
    elif args.cmd == "morning-snapshot":
        from .jobs import morning_snapshot
        morning_snapshot.run(cfg)
    elif args.cmd == "show":
        conn = db.connect(cfg["db_path"])
        row = conn.execute(
            "SELECT * FROM gex_snapshots WHERE symbol=? ORDER BY date DESC LIMIT 1",
            (args.symbol.upper(),),
        ).fetchone()
        print(json.dumps(dict(row), indent=2) if row else f"no snapshot for {args.symbol}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
