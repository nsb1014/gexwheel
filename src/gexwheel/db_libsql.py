"""libSQL/Turso adapter exposing the small sqlite3-like surface the codebase uses.

Only used when the DB URL is a libsql/Turso URL; local/in-memory paths stay on
stdlib sqlite3 so the test suite needs no network. Rows are returned as dicts,
which support row["col"], dict(row), and iteration — everything db.py/jobs use.
See docs/superpowers/specs/2026-06-15-cloud-hosting-and-dashboard-design.md.
"""
from __future__ import annotations


class _RowCursor:
    """Wraps a libsql cursor so fetch* return dict rows (column-name access)."""

    def __init__(self, cur):
        self._cur = cur

    def _cols(self) -> list[str]:
        desc = self._cur.description
        return [d[0] for d in desc] if desc else []

    def fetchone(self):
        row = self._cur.fetchone()
        return None if row is None else dict(zip(self._cols(), row))

    def fetchall(self):
        cols = self._cols()
        return [dict(zip(cols, row)) for row in self._cur.fetchall()]

    def __iter__(self):
        cols = self._cols()
        for row in self._cur.fetchall():
            yield dict(zip(cols, row))


class LibsqlConnection:
    """Minimal sqlite3.Connection-compatible wrapper over libsql_experimental."""

    def __init__(self, url: str, auth_token: str | None):
        import libsql_experimental as libsql  # lazy: optional at runtime
        self._conn = libsql.connect(database=url, auth_token=auth_token)
        self.row_factory = None  # accepted for parity; rows are always dicts

    def execute(self, sql: str, params: tuple = ()):
        return _RowCursor(self._conn.execute(sql, params))

    def executescript(self, script: str):
        # Run statements individually for portability (libsql may reject the
        # BEGIN/COMMIT-wrapped multi-statement scripts the migration runner
        # builds). DDL here contains no semicolons inside string literals.
        for stmt in (s.strip() for s in script.split(";")):
            if not stmt or stmt.upper() in ("BEGIN", "COMMIT"):
                continue
            self._conn.execute(stmt)
        self._conn.commit()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    @property
    def in_transaction(self) -> bool:
        # executescript autocommits per statement above; nothing pending.
        return False

    def close(self):
        self._conn.close()
