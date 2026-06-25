"""SQLite layer. FULLY IMPLEMENTED (thin by design - raw SQL, no ORM).

Every function takes an open sqlite3.Connection so jobs control transactions.
Schema bootstrap and migrations are applied idempotently by connect().
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from pathlib import Path

from .models import GexProfile, MentionRecord

_SCHEMA = Path(__file__).resolve().parents[2] / "schema.sql"
_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
_REMOTE_SCHEMES = ("libsql://", "https://", "http://", "wss://", "ws://")


def _is_remote(url: str) -> bool:
    return url.startswith(_REMOTE_SCHEMES)


def connect(db_path: str) -> sqlite3.Connection:
    """Open the DB. A libsql/Turso URL (via TURSO_DATABASE_URL or db_path) routes
    to the libsql adapter; anything else uses stdlib sqlite3 (local/dev/tests)."""
    turso_url = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
    url = turso_url or db_path
    if _is_remote(url):
        from .db_libsql import LibsqlConnection
        conn = LibsqlConnection(url, os.environ.get("TURSO_AUTH_TOKEN"))
        conn.executescript(_SCHEMA.read_text())
        _apply_migrations(conn)
        return conn

    if db_path.startswith("/data/") and not turso_url:
        raise RuntimeError(
            "TURSO_DATABASE_URL is not set; production db_path requires Turso. "
            "Add TURSO_DATABASE_URL and TURSO_AUTH_TOKEN as GitHub repo secrets "
            "(see deploy/INSTALL.md)."
        )

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA.read_text())
    _apply_migrations(conn)
    return conn


def _apply_migrations(conn: sqlite3.Connection, migrations_dir: Path = _MIGRATIONS) -> None:
    """Apply unapplied SQL migrations in filename order."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version TEXT PRIMARY KEY,
               applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    if not migrations_dir.exists():
        conn.commit()
        return

    applied = {
        r["version"]
        for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for path in sorted(migrations_dir.glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        escaped_version = version.replace("'", "''")
        try:
            conn.executescript(
                "BEGIN;\n"
                f"{path.read_text()}\n"
                f"INSERT INTO schema_migrations(version) VALUES ('{escaped_version}');\n"
                "COMMIT;"
            )
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise


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


# ---------- app_state ----------

def get_app_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_app_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO app_state(key, value) VALUES (?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value),
    )


def morning_spot0_key(asof: date) -> str:
    return f"morning_spot0_{asof.isoformat()}"


def set_morning_spot0(conn: sqlite3.Connection, asof: date, spots: dict[str, float], captured_at: str) -> None:
    set_app_state(conn, morning_spot0_key(asof), json.dumps({"captured_at": captured_at, "spots": spots}))


def get_morning_spot0(conn: sqlite3.Connection, asof: date) -> dict[str, float]:
    raw = get_app_state(conn, morning_spot0_key(asof))
    if not raw:
        return {}
    try:
        return {k: float(v) for k, v in json.loads(raw).get("spots", {}).items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


# ---------- primary watchlist ----------

def primary_symbols(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT symbol FROM primary_watchlist ORDER BY symbol").fetchall()
    return [r["symbol"] for r in rows]


def upsert_primary(conn: sqlite3.Connection, symbol: str, screened: date, *,
                   metrics: dict) -> None:
    """Insert/refresh a primary-watchlist row. `metrics` keys are optional;
    unknown keys are ignored, the full dict is stored as metrics_json."""
    conn.execute(
        """INSERT INTO primary_watchlist(symbol, screened_date, spot, avg_volume,
               near_oi, spread_pct, vrp, sector, metrics_json)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(symbol) DO UPDATE SET
               screened_date=excluded.screened_date, spot=excluded.spot,
               avg_volume=excluded.avg_volume, near_oi=excluded.near_oi,
               spread_pct=excluded.spread_pct, vrp=excluded.vrp,
               sector=excluded.sector, metrics_json=excluded.metrics_json""",
        (symbol, screened.isoformat(), metrics.get("spot"), metrics.get("avg_volume"),
         metrics.get("near_oi"), metrics.get("spread"), metrics.get("vrp"),
         metrics.get("sector"), json.dumps(metrics)),
    )


def delete_primary(conn: sqlite3.Connection, symbol: str) -> None:
    conn.execute("DELETE FROM primary_watchlist WHERE symbol=?", (symbol,))
