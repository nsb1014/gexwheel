"""SQLite layer. FULLY IMPLEMENTED (thin by design - raw SQL, no ORM).

Every function takes an open sqlite3.Connection so jobs control transactions.
Schema is applied idempotently by connect().
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from .models import GexProfile, MentionRecord

_SCHEMA = Path(__file__).resolve().parents[2] / "schema.sql"


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA.read_text())
    return conn


# ---------- mentions ----------

def record_mention(conn: sqlite3.Connection, m: MentionRecord) -> None:
    conn.execute(
        """INSERT INTO mentions(symbol, date, source, mentions, rank, upvotes)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(symbol, date, source) DO UPDATE
           SET mentions=excluded.mentions, rank=excluded.rank, upvotes=excluded.upvotes""",
        (m.symbol, m.asof.isoformat(), m.source, m.mentions, m.rank, m.upvotes),
    )


def mention_history(conn: sqlite3.Connection, symbol: str, *, days: int = 14,
                    source: str = "apewisdom") -> list[tuple[str, int]]:
    """Most-recent-first [(iso_date, mentions), ...] excluding nothing; caller slices."""
    rows = conn.execute(
        """SELECT date, mentions FROM mentions
           WHERE symbol=? AND source=? ORDER BY date DESC LIMIT ?""",
        (symbol, source, days),
    ).fetchall()
    return [(r["date"], r["mentions"]) for r in rows]


# ---------- gex ----------

def record_gex(conn: sqlite3.Connection, p: GexProfile) -> None:
    conn.execute(
        """INSERT INTO gex_snapshots(symbol, date, spot, call_wall, put_wall,
                                     zero_gamma, net_gex, regime, profile_json)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(symbol, date) DO UPDATE SET
             spot=excluded.spot, call_wall=excluded.call_wall, put_wall=excluded.put_wall,
             zero_gamma=excluded.zero_gamma, net_gex=excluded.net_gex,
             regime=excluded.regime, profile_json=excluded.profile_json""",
        (p.symbol, p.asof.isoformat(), p.spot, p.call_wall, p.put_wall,
         p.zero_gamma, p.net_gex, p.regime,
         json.dumps({str(k): v for k, v in p.by_strike.items()})),
    )


def recent_put_walls(conn: sqlite3.Connection, symbol: str, days: int) -> list[float | None]:
    """Put wall strikes, most recent first, for persistence checks."""
    rows = conn.execute(
        "SELECT put_wall FROM gex_snapshots WHERE symbol=? ORDER BY date DESC LIMIT ?",
        (symbol, days),
    ).fetchall()
    return [r["put_wall"] for r in rows]


# ---------- watchlist / tickers ----------

def upsert_ticker(conn: sqlite3.Connection, symbol: str, *, source: str,
                  asof: date, sector: str | None = None) -> None:
    conn.execute(
        """INSERT INTO tickers(symbol, added_date, source, sector)
           VALUES (?,?,?,?) ON CONFLICT(symbol) DO NOTHING""",
        (symbol, asof.isoformat(), source, sector),
    )


def watchlist_add(conn: sqlite3.Connection, symbol: str, asof: date, score: float | None = None) -> None:
    conn.execute(
        """INSERT INTO watchlist(symbol, date_added, status, last_score)
           VALUES (?,?, 'active', ?)
           ON CONFLICT(symbol) DO UPDATE SET status='active', last_score=excluded.last_score""",
        (symbol, asof.isoformat(), score),
    )


def watchlist_active(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT symbol FROM watchlist WHERE status='active'").fetchall()
    return [r["symbol"] for r in rows]


def bench_ticker(conn: sqlite3.Connection, symbol: str, until: date, note: str = "") -> None:
    conn.execute("UPDATE watchlist SET status='benched', notes=? WHERE symbol=?", (note, symbol))
    conn.execute("UPDATE tickers SET cooldown_until=? WHERE symbol=?", (until.isoformat(), symbol))


def log_alert(conn: sqlite3.Connection, symbol: str, asof: date, alert_type: str,
              payload: dict, sent_at: str | None) -> None:
    conn.execute(
        "INSERT INTO alerts(symbol, date, type, payload_json, sent_at) VALUES (?,?,?,?,?)",
        (symbol, asof.isoformat(), alert_type, json.dumps(payload), sent_at),
    )
