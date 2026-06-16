"""SQLite migration tracking."""
from __future__ import annotations

import sqlite3

import pytest

from gexwheel import db as gdb


def test_connect_records_initial_migration(tmp_path):
    conn = gdb.connect(str(tmp_path / "gexwheel.db"))

    rows = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()

    assert [r["version"] for r in rows] == ["0001_initial", "0002_primary_watchlist"]


def test_apply_migrations_runs_new_files_once_in_order(tmp_path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_create_probe.sql").write_text(
        "CREATE TABLE migration_probe (id INTEGER PRIMARY KEY);\n"
    )
    (migrations_dir / "0002_add_note.sql").write_text(
        "ALTER TABLE migration_probe ADD COLUMN note TEXT;\n"
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    gdb._apply_migrations(conn, migrations_dir=migrations_dir)
    gdb._apply_migrations(conn, migrations_dir=migrations_dir)

    versions = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    columns = conn.execute("PRAGMA table_info(migration_probe)").fetchall()

    assert [r["version"] for r in versions] == [
        "0001_create_probe",
        "0002_add_note",
    ]
    assert [r["name"] for r in columns] == ["id", "note"]


def test_failed_migration_rolls_back_partial_schema_and_version(tmp_path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_bad.sql").write_text(
        "CREATE TABLE partial_migration (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO missing_table(id) VALUES (1);\n"
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    with pytest.raises(sqlite3.OperationalError):
        gdb._apply_migrations(conn, migrations_dir=migrations_dir)

    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='partial_migration'"
    ).fetchone()
    version = conn.execute(
        "SELECT version FROM schema_migrations WHERE version='0001_bad'"
    ).fetchone()

    assert table is None
    assert version is None


def test_connect_preserves_existing_database_rows_while_recording_baseline(tmp_path):
    db_path = tmp_path / "existing.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE watchlist (
               symbol TEXT PRIMARY KEY,
               date_added TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'active',
               last_score REAL,
               notes TEXT
           )"""
    )
    conn.execute(
        "INSERT INTO watchlist(symbol, date_added, status) VALUES ('KEEP', '2026-01-01', 'active')"
    )
    conn.commit()
    conn.close()

    upgraded = gdb.connect(str(db_path))
    watchlist_row = upgraded.execute(
        "SELECT symbol, status FROM watchlist WHERE symbol='KEEP'"
    ).fetchone()
    migration_row = upgraded.execute(
        "SELECT version FROM schema_migrations WHERE version='0001_initial'"
    ).fetchone()

    assert dict(watchlist_row) == {"symbol": "KEEP", "status": "active"}
    assert migration_row["version"] == "0001_initial"


def test_migration_0002_adds_primary_tables_and_preserves_data(tmp_path):
    db_path = tmp_path / "existing.db"
    seed = sqlite3.connect(db_path)
    seed.execute(
        """CREATE TABLE watchlist (
               symbol TEXT PRIMARY KEY, date_added TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'active', last_score REAL, notes TEXT)"""
    )
    seed.execute("INSERT INTO watchlist(symbol, date_added) VALUES ('KEEP', '2026-01-01')")
    seed.commit()
    seed.close()

    conn = gdb.connect(str(db_path))

    assert conn.execute(
        "SELECT symbol FROM watchlist WHERE symbol='KEEP'"
    ).fetchone()["symbol"] == "KEEP"
    tables = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"primary_watchlist", "app_state"} <= tables
    versions = [
        r["version"]
        for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    ]
    assert "0002_primary_watchlist" in versions
