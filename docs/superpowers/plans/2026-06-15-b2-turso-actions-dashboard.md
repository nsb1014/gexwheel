# Plan B2 — Turso store + GitHub Actions + Cloudflare dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the data store to Turso (hosted libSQL) via a `db.connect()` shim, run the jobs on free GitHub Actions cron, publish a public read-only Cloudflare Pages dashboard of the active watchlist + the day's trades, and retire the personal-machine deploy.

**Architecture:** `db.connect()` routes libsql/Turso URLs to a small adapter (`db_libsql.py`) that exposes the sqlite3-like surface the codebase uses (dict rows, `executescript`, `commit`); everything else stays on stdlib `sqlite3`, so the test suite runs offline. GitHub Actions runs `mentions`/`screen`/`morning` on cron against Turso (creds from repo secrets). A Cloudflare Pages Function queries Turso with a read-only token and a static page renders it. The podman/systemd deploy and `install.sh` are deleted in favor of a cloud deploy guide.

**Tech Stack:** Python (`libsql-experimental`), pytest, GitHub Actions, Cloudflare Pages + Functions (`@libsql/client`), Turso.

**Spec:** `docs/superpowers/specs/2026-06-15-cloud-hosting-and-dashboard-design.md`

**Prerequisite:** Plans A1 + A2 + B1 merged (B1 removed Discord and added fail-loud; this plan assumes `REQUIRED_KEYS` has no `discord`).

---

## Background & key risk

Ephemeral CI runners have no persistent disk, so the DB must be remote. Turso is SQLite-compatible, so `schema.sql`, the migration runner, and every SQL query work unchanged — only the **driver/row layer** differs. The codebase relies on:

- `conn.execute(sql, params).fetchone()/.fetchall()` returning rows with **column-name access** (`row["col"]`, `dict(row)`, iteration).
- `conn.executescript(text)` (schema bootstrap + migration runner).
- `conn.commit()`, `conn.close()`, `conn.in_transaction`, `conn.rollback()`.

`libsql-experimental` returns tuple rows from a sqlite3-like API. Task 2's adapter converts those to dicts (which support `row["col"]`, `dict(row)`, and iteration). Task 1 is a **validation spike** to confirm the exact client API before coding the adapter.

Tests keep using `db.connect(":memory:")` → stdlib sqlite3, so the suite needs no network. One network-gated smoke test exercises the real adapter only when Turso creds are present.

---

## File structure

- Modify: `requirements.txt` — add `libsql-experimental`.
- Create: `src/gexwheel/db_libsql.py` — the adapter.
- Modify: `src/gexwheel/db.py` — `connect()` routing.
- Create: `.github/workflows/ci.yml`, `mentions.yml`, `morning.yml`, `keepalive.yml`.
- Create: `web/public/index.html`, `web/public/styles.css`, `web/public/app.js`, `web/functions/api/data.js`, `web/package.json`, `web/wrangler.toml`, `web/README.md`.
- Delete: `install.sh`, `deploy/Containerfile`, `deploy/gexwheel-mentions.container`, `deploy/gexwheel-mentions.timer`, `deploy/gexwheel-morning.container`, `deploy/gexwheel-morning.timer`.
- Rewrite: `deploy/INSTALL.md`, README deploy section.
- Tests: `tests/test_db_routing.py` (create), `tests/test_db_turso_smoke.py` (create, network-gated).

---

### Task 1: Add the libSQL dependency + validation spike

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Install the client and pin the resolved version**

```bash
. .venv/bin/activate 2>/dev/null || (python -m venv .venv && . .venv/bin/activate)
pip install libsql-experimental
python -c "import libsql_experimental as l; print(getattr(l, '__version__', 'unknown'))"
pip show libsql-experimental | grep -i version
```

Add to `requirements.txt` under the "Data sources" section, pinning the **floor to the version pip just installed** (do not invent a number — use the one printed):

```
# Remote store (hosted SQLite). Required for the cloud deployment; local/dev and
# tests fall back to stdlib sqlite3 via db.connect(), so this is import-light.
libsql-experimental>=<PASTE_INSTALLED_VERSION>
```

- [ ] **Step 2: Spike — confirm the adapter's assumptions**

Run this throwaway script (against a local libsql file so it needs no account) and record the output in the commit message:

```bash
python - <<'PY'
import libsql_experimental as libsql
conn = libsql.connect("spike.db")
conn.execute("CREATE TABLE t(a INTEGER, b TEXT)")
conn.execute("INSERT INTO t(a,b) VALUES (?,?)", (1, "x"))
conn.commit()
cur = conn.execute("SELECT a, b FROM t")
print("description:", cur.description)
print("fetchall:", cur.fetchall())          # expect [(1, 'x')] (tuples)
print("has executescript:", hasattr(conn, "executescript"))
PY
rm -f spike.db
```

Confirm: `description` exposes column names at index 0 of each entry; `fetchall()` returns tuples; `executescript` exists (or note its absence — the adapter handles both). If the API differs materially from these assumptions, adjust the adapter in Task 2 accordingly (the conversion logic is the only thing that depends on it).

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "build: add libsql-experimental for the Turso remote store"
```

---

### Task 2: `db.connect()` Turso routing + adapter

**Files:**
- Create: `src/gexwheel/db_libsql.py`
- Modify: `src/gexwheel/db.py`
- Tests: `tests/test_db_routing.py` (create), `tests/test_db_turso_smoke.py` (create)

- [ ] **Step 1: Write the routing test (offline, mocked)**

Create `tests/test_db_routing.py`:

```python
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


def test_env_url_overrides_local_path(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://from-env.turso.io")
    fake_conn = MagicMock()
    with patch("gexwheel.db_libsql.LibsqlConnection", return_value=fake_conn) as ctor:
        gdb.connect("/data/gexwheel.db")  # local path ignored in favor of env URL
    ctor.assert_called_once()
    args, kwargs = ctor.call_args
    assert "from-env.turso.io" in (args[0] if args else kwargs.get("url", ""))
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_db_routing.py -v`
Expected: FAIL — `gexwheel.db_libsql` does not exist and `connect()` does not route.

- [ ] **Step 3: Implement the adapter**

Create `src/gexwheel/db_libsql.py`:

```python
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
```

- [ ] **Step 4: Route in `db.connect()`**

In `src/gexwheel/db.py`, add `import os` at the top (after `import json`), and replace `connect()` (lines 19-25) with:

```python
_REMOTE_SCHEMES = ("libsql://", "https://", "http://", "wss://", "ws://")


def _is_remote(url: str) -> bool:
    return url.startswith(_REMOTE_SCHEMES)


def connect(db_path: str) -> sqlite3.Connection:
    """Open the DB. A libsql/Turso URL (via TURSO_DATABASE_URL or db_path) routes
    to the libsql adapter; anything else uses stdlib sqlite3 (local/dev/tests)."""
    url = os.environ.get("TURSO_DATABASE_URL") or db_path
    if _is_remote(url):
        from .db_libsql import LibsqlConnection
        conn = LibsqlConnection(url, os.environ.get("TURSO_AUTH_TOKEN"))
        conn.executescript(_SCHEMA.read_text())
        _apply_migrations(conn)
        return conn

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA.read_text())
    _apply_migrations(conn)
    return conn
```

> NOTE: the return type hint stays `sqlite3.Connection` for callers' benefit; `LibsqlConnection` is duck-compatible with the surface they use. `_apply_migrations` already only uses `execute().fetchall()`, `executescript`, `in_transaction`, and `rollback`, all provided by the adapter.

- [ ] **Step 5: Write the network-gated smoke test**

Create `tests/test_db_turso_smoke.py`:

```python
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
```

- [ ] **Step 6: Run the routing tests + full suite**

Run: `PYTHONPATH=src python3 -m pytest tests/test_db_routing.py -v && PYTHONPATH=src python3 -m pytest -q`
Expected: routing tests PASS; full suite green; the Turso smoke test is SKIPPED (no creds).

- [ ] **Step 7: Commit**

```bash
git add src/gexwheel/db_libsql.py src/gexwheel/db.py tests/test_db_routing.py tests/test_db_turso_smoke.py
git commit -m "feat(db): route libsql/Turso URLs through an adapter; stdlib sqlite for local/tests"
```

---

### Task 3: GitHub Actions workflows

**Files:**
- Create: `.github/workflows/ci.yml`, `mentions.yml`, `morning.yml`, `keepalive.yml`

- [ ] **Step 1: CI workflow (keeps the suite green on every push/PR)**

Create `.github/workflows/ci.yml`:

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: PYTHONPATH=src pytest -q
```

- [ ] **Step 2: Daily mentions + screen workflow**

Create `.github/workflows/mentions.yml` (UTC cron; ~07:00 ET ≈ 11:00 UTC during EDT — note the DST caveat inline):

```yaml
name: mentions
on:
  schedule:
    - cron: "0 11 * * *"   # ~07:00 America/New_York (EDT); UTC-only, drifts 1h at DST
  workflow_dispatch:
jobs:
  run:
    runs-on: ubuntu-latest
    env:
      TURSO_DATABASE_URL: ${{ secrets.TURSO_DATABASE_URL }}
      TURSO_AUTH_TOKEN: ${{ secrets.TURSO_AUTH_TOKEN }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - name: Daily mentions scan
        run: PYTHONPATH=src python -m gexwheel mentions
      - name: Periodic primary screen (self-throttles to interval)
        run: PYTHONPATH=src python -m gexwheel screen
```

- [ ] **Step 3: Weekday morning workflow**

Create `.github/workflows/morning.yml`:

```yaml
name: morning
on:
  schedule:
    - cron: "15 11 * * 1-5"   # ~07:15 ET weekdays (EDT); UTC-only
  workflow_dispatch:
jobs:
  run:
    runs-on: ubuntu-latest
    env:
      TURSO_DATABASE_URL: ${{ secrets.TURSO_DATABASE_URL }}
      TURSO_AUTH_TOKEN: ${{ secrets.TURSO_AUTH_TOKEN }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - name: Morning GEX + screen + identify trades
        run: PYTHONPATH=src python -m gexwheel morning
```

> The jobs need no committed `config.yaml`: `config.load_config()` falls back to `config/config.example.yaml` defaults (ApeWisdom needs no key), and `db.connect()` prefers `TURSO_DATABASE_URL` over the example's `db_path`. If PRAW is ever enabled, add `GEXWHEEL_*` secrets and a committed config; not required for the default ApeWisdom source.

- [ ] **Step 4: Keepalive workflow (defeats the 60-day auto-disable)**

Create `.github/workflows/keepalive.yml`:

```yaml
name: keepalive
on:
  schedule:
    - cron: "0 12 * * 1"   # weekly Monday; re-enables scheduled workflows
  workflow_dispatch:
permissions:
  actions: write
jobs:
  keepalive:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Re-enable scheduled workflows
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          for wf in mentions.yml morning.yml; do
            gh workflow enable "$wf" --repo "$GITHUB_REPOSITORY" || true
          done
```

> GitHub disables scheduled workflows after 60 days of no repo activity. This weekly job (plus normal commits) keeps `mentions`/`morning` enabled. GitHub emails the repo owner on any *failed* run — enable Settings → Notifications → Actions → "failed workflows only".

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/
git commit -m "ci: GitHub Actions cron for mentions/screen/morning + keepalive + test CI"
```

---

### Task 4: Cloudflare Pages dashboard

**Files:**
- Create: `web/functions/api/data.js`, `web/public/index.html`, `web/public/styles.css`, `web/public/app.js`, `web/package.json`, `web/wrangler.toml`, `web/README.md`

- [ ] **Step 1: The data API (Pages Function)**

Create `web/functions/api/data.js`:

```js
import { createClient } from "@libsql/client/web";

function etTodayISO() {
  // YYYY-MM-DD in America/New_York (matches how the jobs store dates).
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const get = (t) => parts.find((p) => p.type === t).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export async function onRequest(context) {
  const { env } = context;
  const client = createClient({
    url: env.TURSO_DATABASE_URL,
    authToken: env.TURSO_READONLY_TOKEN,
  });

  try {
    const today = etTodayISO();

    const watchlist = await client.execute(
      `SELECT w.symbol, w.last_score, w.date_added, pw.sector,
              g.spot, g.put_wall, g.call_wall, g.regime,
              v.iv_rank, v.vrp
         FROM watchlist w
         LEFT JOIN primary_watchlist pw ON pw.symbol = w.symbol
         LEFT JOIN gex_snapshots g
                ON g.symbol = w.symbol
               AND g.date = (SELECT MAX(date) FROM gex_snapshots WHERE symbol = w.symbol)
         LEFT JOIN vol_stats v
                ON v.symbol = w.symbol
               AND v.date = (SELECT MAX(date) FROM vol_stats WHERE symbol = w.symbol)
        WHERE w.status = 'active'
        ORDER BY w.last_score DESC NULLS LAST, w.symbol`
    );

    const trades = await client.execute({
      sql: `SELECT symbol, type, payload_json, sent_at
              FROM alerts WHERE date = ? ORDER BY id DESC`,
      args: [today],
    });

    const recent = await client.execute({
      sql: `SELECT date, symbol, type, payload_json
              FROM alerts WHERE date < ? ORDER BY date DESC, id DESC LIMIT 25`,
      args: [today],
    });

    const lastScreen = await client.execute(
      `SELECT value FROM app_state WHERE key = 'last_screen_date'`
    );
    const lastGex = await client.execute(
      `SELECT MAX(date) AS d FROM gex_snapshots`
    );

    const body = {
      today,
      watchlist: watchlist.rows,
      trades: trades.rows.map((r) => ({ ...r, payload: JSON.parse(r.payload_json || "{}") })),
      recent: recent.rows.map((r) => ({ ...r, payload: JSON.parse(r.payload_json || "{}") })),
      last_screen_date: lastScreen.rows[0]?.value ?? null,
      last_run_date: lastGex.rows[0]?.d ?? null,
    };
    return new Response(JSON.stringify(body), {
      headers: { "content-type": "application/json", "cache-control": "public, max-age=300" },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: "data temporarily unavailable" }), {
      status: 502, headers: { "content-type": "application/json" },
    });
  }
}
```

- [ ] **Step 2: Static page**

Create `web/public/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>gexwheel — active watchlist & trades</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <header>
    <h1>gexwheel</h1>
    <p id="status" class="status">Loading…</p>
  </header>
  <main>
    <section>
      <h2>Today's identified trades</h2>
      <div id="trades" class="cards"></div>
    </section>
    <section>
      <h2>Active watchlist</h2>
      <div id="watchlist" class="table-wrap"></div>
    </section>
    <section>
      <h2>Recent trades</h2>
      <div id="recent" class="table-wrap"></div>
    </section>
  </main>
  <footer>
    Decision support, not financial advice. Data is free, delayed, and may be wrong.
  </footer>
  <script src="/app.js"></script>
</body>
</html>
```

Create `web/public/styles.css`:

```css
:root { color-scheme: light dark; --fg:#e8eaed; --bg:#0e1116; --muted:#9aa4b2;
  --pos:#2ecc71; --neg:#e74c3c; --card:#161b22; --line:#222a35; }
* { box-sizing: border-box; }
body { margin:0; font:16px/1.5 system-ui,Segoe UI,Roboto,sans-serif; color:var(--fg); background:var(--bg); }
header,main,footer { max-width:980px; margin:0 auto; padding:16px; }
h1 { margin:0; font-size:1.4rem; } h2 { font-size:1.05rem; border-bottom:1px solid var(--line); padding-bottom:6px; }
.status { color:var(--muted); margin:4px 0 0; }
.cards { display:grid; gap:12px; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }
.card .sym { font-weight:700; font-size:1.1rem; }
.card .score { float:right; color:var(--muted); }
.pos { color:var(--pos); } .neg { color:var(--neg); }
table { width:100%; border-collapse:collapse; font-size:.92rem; }
th,td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-weight:600; }
footer { color:var(--muted); border-top:1px solid var(--line); margin-top:24px; font-size:.85rem; }
.empty { color:var(--muted); font-style:italic; }
```

Create `web/public/app.js`:

```js
const fmt = (v, d = 2) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));

async function load() {
  const status = document.getElementById("status");
  let data;
  try {
    const res = await fetch("/api/data");
    data = await res.json();
    if (data.error) throw new Error(data.error);
  } catch (e) {
    status.textContent = "Data temporarily unavailable.";
    return;
  }
  status.textContent =
    `Last screen: ${data.last_screen_date ?? "—"} · Last run: ${data.last_run_date ?? "—"} · ${data.today}`;

  const trades = document.getElementById("trades");
  if (!data.trades.length) {
    trades.innerHTML = `<p class="empty">No trades identified today.</p>`;
  } else {
    trades.innerHTML = data.trades.map((t) => {
      const p = t.payload || {};
      return `<div class="card">
        <span class="sym">${t.symbol}</span>
        <span class="score">${fmt(p.score, 0)}/100</span>
        <div>Spot ${fmt(p.spot)} · Put wall ${fmt(p.put_wall)}</div>
        <div>${p.suggested ?? ""}</div>
        <div class="empty">${p.notes ?? ""}</div>
      </div>`;
    }).join("");
  }

  const wl = document.getElementById("watchlist");
  wl.innerHTML = data.watchlist.length ? `<table>
    <tr><th>Symbol</th><th>Score</th><th>Spot</th><th>Put wall</th><th>Regime</th><th>IV rank</th><th>VRP</th><th>Sector</th></tr>
    ${data.watchlist.map((r) => `<tr>
      <td>${r.symbol}</td><td>${fmt(r.last_score, 0)}</td><td>${fmt(r.spot)}</td>
      <td>${fmt(r.put_wall)}</td>
      <td class="${r.regime === "positive" ? "pos" : r.regime === "negative" ? "neg" : ""}">${r.regime ?? "—"}</td>
      <td>${fmt(r.iv_rank, 0)}</td><td>${fmt(r.vrp)}</td><td>${r.sector ?? "—"}</td>
    </tr>`).join("")}
  </table>` : `<p class="empty">Watchlist is empty — run the screen to seed it.</p>`;

  const recent = document.getElementById("recent");
  recent.innerHTML = data.recent.length ? `<table>
    <tr><th>Date</th><th>Symbol</th><th>Score</th><th>Suggested</th></tr>
    ${data.recent.map((r) => `<tr>
      <td>${r.date}</td><td>${r.symbol}</td><td>${fmt(r.payload?.score, 0)}</td>
      <td>${r.payload?.suggested ?? ""}</td></tr>`).join("")}
  </table>` : `<p class="empty">No recent trades.</p>`;
}

load();
```

- [ ] **Step 3: Project files**

Create `web/package.json`:

```json
{
  "name": "gexwheel-web",
  "private": true,
  "version": "0.1.0",
  "dependencies": {
    "@libsql/client": "^0.14.0"
  },
  "devDependencies": {
    "wrangler": "^3.0.0"
  },
  "scripts": {
    "dev": "wrangler pages dev public",
    "deploy": "wrangler pages deploy public"
  }
}
```

> Pin `@libsql/client`/`wrangler` to whatever `npm install` resolves; update the carets to the installed majors. Run `cd web && npm install` to generate `package-lock.json` and commit it.

Create `web/wrangler.toml`:

```toml
name = "gexwheel"
pages_build_output_dir = "public"
compatibility_date = "2024-09-23"
compatibility_flags = ["nodejs_compat"]
```

> `TURSO_DATABASE_URL` and `TURSO_READONLY_TOKEN` are set as Pages **environment variables/secrets** in the Cloudflare dashboard (or via `wrangler pages secret put`), never committed. The read-only token is created with `turso db tokens create <db> --read-only`.

- [ ] **Step 4: web/README.md**

Create `web/README.md` documenting local dev + deploy:

```markdown
# gexwheel dashboard (Cloudflare Pages)

Public, read-only view of the active watchlist and the day's identified trades.
Reads Turso (hosted libSQL) from a Pages Function with a read-only token.

## Local dev
    cd web
    npm install
    TURSO_DATABASE_URL=... TURSO_READONLY_TOKEN=... npm run dev

## Deploy
1. `turso db tokens create <db> --read-only` → copy the token.
2. Create a Cloudflare Pages project pointing at `web/` (build output dir `public`).
3. Set Pages env vars `TURSO_DATABASE_URL` and `TURSO_READONLY_TOKEN`.
4. `npm run deploy` (or connect the repo for Git-based deploys).

The Function lives at `functions/api/data.js` → served at `/api/data`.
```

- [ ] **Step 5: Validate the Function locally (manual; not part of pytest)**

Run: `cd web && npm install && TURSO_DATABASE_URL=<url> TURSO_READONLY_TOKEN=<tok> npm run dev`
Expected: `wrangler pages dev` serves the page; `/api/data` returns JSON (or the friendly error if Turso is empty). This is a manual check — JS has no pytest coverage.

- [ ] **Step 6: Commit**

```bash
cd web && npm install && cd ..
git add web/
git commit -m "feat(web): public Cloudflare Pages dashboard reading Turso"
```

---

### Task 5: Retire the personal-machine deploy + rewrite docs

**Files:**
- Delete: `install.sh`, `deploy/Containerfile`, `deploy/gexwheel-mentions.container`, `deploy/gexwheel-mentions.timer`, `deploy/gexwheel-morning.container`, `deploy/gexwheel-morning.timer`
- Rewrite: `deploy/INSTALL.md`, `README.md` (deploy/install sections)

- [ ] **Step 1: Delete the retired deploy artifacts**

```bash
git rm install.sh deploy/Containerfile \
  deploy/gexwheel-mentions.container deploy/gexwheel-mentions.timer \
  deploy/gexwheel-morning.container deploy/gexwheel-morning.timer
```

- [ ] **Step 2: Rewrite `deploy/INSTALL.md` as the cloud deploy guide**

Replace `deploy/INSTALL.md` with a guide covering exactly these steps (write them out fully):

```markdown
# Deploy (cloud, free tier)

gexwheel runs entirely on free services — no personal machine required.

- Compute: GitHub Actions (cron) — `.github/workflows/{mentions,morning,keepalive,ci}.yml`
- Database: Turso (hosted libSQL)
- Dashboard: Cloudflare Pages (`web/`)

## 1. Turso
    curl -sSfL https://get.tur.so/install.sh | bash
    turso auth signup
    turso db create gexwheel
    turso db show gexwheel --url                 # -> TURSO_DATABASE_URL
    turso db tokens create gexwheel              # -> TURSO_AUTH_TOKEN (read-write, for jobs)
    turso db tokens create gexwheel --read-only  # -> TURSO_READONLY_TOKEN (for the dashboard)

## 2. GitHub repo secrets (Settings → Secrets and variables → Actions)
- `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
- (optional) PRAW creds if you switch `reddit.source` away from apewisdom

Enable Settings → Notifications → Actions → failed workflows only (ops alerting).

## 3. Seed the primary watchlist (once)
Run the `mentions` workflow via "Run workflow" (workflow_dispatch), or locally:
    TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... PYTHONPATH=src python -m gexwheel screen --force

## 4. Cloudflare Pages
See `web/README.md`. Create a Pages project from `web/`, set
`TURSO_DATABASE_URL` + `TURSO_READONLY_TOKEN`, deploy.

## Local dev
    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    PYTHONPATH=src pytest                 # offline, stdlib sqlite
    # point at a throwaway local DB:
    PYTHONPATH=src python -m gexwheel screen --force   # writes ./config default db_path
```

- [ ] **Step 3: Rewrite the README install/deploy sections**

In `README.md`: replace the "Install (Linux)" curl-installer section and the container-deploy reference with a short "Deploy" section pointing at `deploy/INSTALL.md` (cloud) and `web/README.md` (dashboard). Remove the `install.sh` description and the systemd/podman language. Keep the "Developing" section (local pytest). Ensure the pipeline diagram (updated in A2/B1) shows `… → dashboard`.

- [ ] **Step 4: Verify no stale references remain**

Run: `rg -in 'install\.sh|quadlet|podman|systemd|\.container|\.timer' README.md deploy/ AGENTS.md`
Expected: no live instructions referencing the retired deploy (historical plan docs may still mention them — that's fine).

- [ ] **Step 5: Run the full suite & commit**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: green.

```bash
git add -A
git commit -m "docs(deploy): retire personal-machine install; cloud deploy guide (Turso + Actions + Pages)"
```

---

## Self-review notes (for the executor)

- After every Python task: `PYTHONPATH=src pytest` must stay green offline (the Turso smoke test stays skipped without creds).
- Frozen contracts respected: `schema.sql`, `models.py` fields, and all `db.*` public signatures unchanged; the adapter is additive and `connect()` keeps its signature/return type.
- New dependency (`libsql-experimental`) is pre-approved (operator sign-off in the spec-review gate).
- The adapter is the main risk; Task 1's spike validates the libsql API before Task 2 codes against it. If the spike shows a different row/description shape, adjust `_RowCursor` only.
- Secrets are never committed — only referenced via `${{ secrets.* }}` (Actions) and Pages env vars (dashboard).
- Execution order: A1 → A2 → B1 → **B2** (last).
```
