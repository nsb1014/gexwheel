"""db.connect routes remote URLs to the libsql adapter, local to stdlib sqlite3."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

from gexwheel import db as gdb


def test_local_path_uses_stdlib_sqlite(tmp_path):
    conn = gdb.connect(str(tmp_path / "x.db"))
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_memory_uses_stdlib_sqlite():
    conn = gdb.connect(":memory:")
    assert isinstance(conn, sqlite3.Connection)


def test_libsql_url_routes_to_adapter(monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    fake_conn = MagicMock()
    with patch("gexwheel.db_libsql.LibsqlConnection", return_value=fake_conn) as ctor:
        out = gdb.connect("libsql://example.turso.io")
    ctor.assert_called_once()
    # connect() runs schema bootstrap + migrations against the adapter
    assert fake_conn.executescript.called
    assert out is fake_conn


def test_production_db_path_requires_turso(monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    try:
        gdb.connect("/data/gexwheel.db")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "TURSO_DATABASE_URL" in str(exc)


def test_env_url_overrides_local_path(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://from-env.turso.io")
    fake_conn = MagicMock()
    with patch("gexwheel.db_libsql.LibsqlConnection", return_value=fake_conn) as ctor:
        gdb.connect("/data/gexwheel.db")  # local path ignored in favor of env URL
    ctor.assert_called_once()
    args, kwargs = ctor.call_args
    assert "from-env.turso.io" in (args[0] if args else kwargs.get("url", ""))
