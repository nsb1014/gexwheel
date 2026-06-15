"""Real Turso round-trip — skipped unless TURSO creds are set."""
from __future__ import annotations

import os
from datetime import date

import pytest

from gexwheel import db as gdb

pytestmark = pytest.mark.skipif(
    not os.environ.get("TURSO_DATABASE_URL"),
    reason="set TURSO_DATABASE_URL (+ TURSO_AUTH_TOKEN) to run the Turso smoke test",
)


def test_turso_app_state_roundtrip():
    conn = gdb.connect(os.environ["TURSO_DATABASE_URL"])
    gdb.set_app_state(conn, "smoke_test", date.today().isoformat())
    conn.commit()
    assert gdb.get_app_state(conn, "smoke_test") == date.today().isoformat()
    conn.close()
