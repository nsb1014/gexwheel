# Subsystem B — Cloud hosting, remote DB & public dashboard (2026-06-15)

> **Depends on Subsystem A** (`2026-06-15-screening-inversion-design.md`), which
> stabilizes the data model this subsystem serves: the secondary/active
> `watchlist` and the day's `alerts` ("identified trades"). Implement A first.

## Goal

Take the operator's personal machine entirely out of the loop. Move scheduled
execution to free CI, the data store to a free hosted SQLite, and publish a
public read-only dashboard — replacing Discord push delivery with a web pull.

```
GitHub Actions (cron, UTC)        Turso (hosted libSQL)        Cloudflare Pages
  mentions.yml  (daily)      ──▶   single source of truth  ◀──  static page +
  morning.yml   (weekday)          (mentions, gex, vol,          /api/data Pages
  screen runs in daily,            tickers, primary_watchlist,   Function reads
    self-throttled (21d)           watchlist, alerts, …)         Turso (read-only
  keepalive.yml (weekly)                                         token) → JSON
```

## Decisions (locked with the operator)

1. **Compute = GitHub Actions scheduled workflows.** The repo is public, so
   standard-runner minutes are unlimited/free. The Python jobs need
   yfinance/pandas/praw (impossible on Cloudflare Workers), so they run on
   GitHub-hosted Ubuntu runners.
2. **Database = Turso (libSQL), single source of truth.** Free tier (5 GB,
   500M reads / 10M writes per month, no cold starts) dwarfs this app's needs.
   Ephemeral runners have no persistent disk, so the DB must be remote; Turso is
   SQLite-compatible, minimizing change.
3. **Frontend = Cloudflare Pages**, public, read-only, with the existing "not
   financial advice" disclaimer.
4. **Discord is removed entirely** — from code *and* all documentation — so
   future agents are not misled. The dashboard is the sole output surface.
5. **Ops alerting = GitHub's built-in failure emails** (operator's email is on
   their GitHub account). Jobs must therefore **exit non-zero on real failures**
   so failed runs actually email. A **keepalive workflow** prevents the 60-day
   scheduled-workflow auto-disable.

## Component 1 — Turso as the data store

### `db.connect()` shim (the one core code change)

`schema.sql`, `migrations/`, and every `db.py` function signature stay frozen.
Only the connection factory changes:

- If `db_path` (or a new `TURSO_DATABASE_URL`) is a `libsql://` / `https://`
  Turso URL → return a **libsql connection** (auth token from
  `TURSO_AUTH_TOKEN`). Otherwise → stdlib `sqlite3.connect()` exactly as today.
- The libsql connection must support everything `db.py`/jobs rely on:
  `executescript()` (schema bootstrap + migration runner), `ON CONFLICT`
  upserts, `executescript` with `BEGIN/COMMIT` (migration runner), and
  row-access by column name (`sqlite3.Row` semantics). The plan **validates each
  of these** against the chosen client and adds a thin adapter only where the
  client deviates (e.g. a Row-like wrapper) — kept behind `db.connect()` so no
  caller changes.

### Client choice & dependency

Add the Turso/libsql Python client to `requirements.txt` (this is the "strong
reason + explicit approval" exception to the no-new-deps rule — the move off the
personal machine requires it). Pin a known-good version. Candidate:
`libsql-experimental` (sqlite3-style API) or `libsql-client`; the plan picks one
after validating the API-surface checklist above, and documents the choice.

### Tests stay offline

The entire suite keeps using stdlib `sqlite3` via `db.connect(":memory:")` —
the shim only routes to libsql for real Turso URLs, so `PYTHONPATH=src pytest`
needs no network and stays green. Add one **opt-in, network-gated** smoke test
(skipped unless `TURSO_DATABASE_URL`/token are set) that round-trips a write/read
against a real Turso DB.

### Migration applicability

Turso runs the same `schema.sql` + numbered migrations through the same runner.
Add a migration-runner note/test that the runner works against libsql (the
`executescript` BEGIN/COMMIT pattern in `db._apply_migrations`). If libsql
rejects multi-statement transactional scripts, the adapter executes the wrapped
statements individually inside an explicit transaction — behavior-preserving.

## Component 2 — Remove Discord entirely

**Delete:**
- `src/gexwheel/alerts/discord.py`
- `tests/test_discord.py`
- the `test-discord` subcommand + its handler in `src/gexwheel/__main__.py`
  (lines wiring `test-discord` / `from .alerts.discord import test_webhook`)
- the `discord:` block in `config/config.example.yaml`
- `"discord"` from `REQUIRED_KEYS` in `src/gexwheel/config.py`

**Rewrite `src/gexwheel/jobs/morning.py` alert path:** drop
`from ..alerts import discord as disc` and the `disc.send_alerts(...)` block.
The morning job still computes `AlertCard`s (scoring unchanged) and **persists
every identified trade** to the `alerts` table via `db.log_alert`. Without a
delivery step there is no "delivered vs failed" distinction:
- Set `sent_at` to the identification timestamp for every logged trade. The
  column stays (schema frozen) but its meaning becomes "identified/published
  at"; every identified trade is now always "published" to the dashboard. The
  dashboard reads same-day rows by `date`.
- Remove `max_alerts_per_run` truncation (the dashboard shows all identified
  trades; scoring still orders them). The `discord.max_alerts_per_run` config key
  is removed; if a display cap is wanted later it belongs in a `dashboard:`
  config block, not `discord:`.

**Update `should_alert` dedup (`src/gexwheel/alerts/scoring.py`):** the dedup
currently keys off `sent_at IS NOT NULL` (a Discord-delivery artifact). Change it
to "no alert already identified today for `(symbol, date, 'put_wall_entry')`"
(existence check), and reword the docstrings that mention Discord
(`max_alerts_per_run caps the Discord batch`, `per the alerts/discord.py … spec`,
`Human-readable entry suggestion for the Discord card`).

**Reword (no logic change) the remaining Discord mentions in comments/docstrings:**
- `src/gexwheel/__init__.py` line 8 — "Discord alerts …" → "publishes the day's
  identified trades to the dashboard".
- `src/gexwheel/models.py` line 76 — "Everything the Discord formatter needs …"
  → "Everything the dashboard needs to render one identified trade". (`AlertCard`
  fields stay frozen; only the docstring changes.)
- `src/gexwheel/screening/filters.py` line 45 — "logging/Discord notes" →
  "logging/dashboard notes".
- `src/gexwheel/data/prices.py` line 15 — "shows in the Discord card" → "shows
  on the dashboard".

**Docs:** strip Discord from `README.md`, `AGENTS.md`, `IMPLEMENTATION_GUIDE.md`,
`config/config.example.yaml`, `deploy/INSTALL.md`, `deploy/*.container`,
`install.sh`, and the historical plan/index docs under
`docs/superpowers/plans/` (add a one-line "Discord delivery removed in
2026-06-15 cloud migration; see specs/2026-06-15-cloud-hosting-and-dashboard-design.md"
note rather than rewriting completed-plan history). Verify with
`rg -i 'discord|webhook'` returning only intentional historical pointers.

## Component 3 — GitHub Actions hosting

New `.github/workflows/`:

- **`mentions.yml`** — `schedule: cron` daily (UTC equivalent of ~07:00 ET; note
  UTC-only + DST drift in a comment). Steps: checkout, setup-python, `pip install
  -r requirements.txt`, `python -m gexwheel mentions`, then `python -m gexwheel
  screen` (self-throttles to 21d). `workflow_dispatch` for manual runs.
- **`morning.yml`** — `schedule: cron` Mon–Fri (~07:15 ET in UTC). Steps as
  above running `python -m gexwheel morning`. `workflow_dispatch` included.
- **`keepalive.yml`** — weekly; re-enables scheduled workflows via the REST API
  (or a no-op heartbeat commit) to defeat the 60-day auto-disable.
- **`ci.yml`** — on push/PR: `PYTHONPATH=src pytest` (keeps the green-suite rule
  enforced in CI).

**Secrets (GitHub repo → Settings → Secrets):** `TURSO_DATABASE_URL`,
`TURSO_AUTH_TOKEN`, and PRAW creds if used. No Discord secret. Config for CI is
provided via env vars (the job runs with `GEXWHEEL_CONFIG` pointing at a
committed CI config, or env overrides) — finalized in the plan; **no secret
values committed**.

**Fail-loud rule:** jobs/CLI must return non-zero when a run is materially broken
(universe pull totally failed in `screen`, DB/Turso unreachable, mentions fetch
hard-failed with no fallback) so GitHub emails the operator. Per-symbol errors
stay non-fatal (existing resilience rule). This is a small, testable change to
the job `run()` functions / `__main__` exit codes.

## Component 4 — Cloudflare Pages dashboard

New top-level `web/` (kept out of the Python package):

- **Static page** (`web/public/index.html` + a little CSS/JS, no heavy build):
  modern, mobile-friendly, read-only. Sections:
  1. **Active (secondary) watchlist** — symbol, sector, last score, date added,
     key metrics (spot, put/call wall, regime, iv_rank, vrp).
  2. **Today's identified trades** — from `alerts` for the current trading day:
     symbol, spot, put wall, score, suggested entry, notes.
  3. **Recent trades (last N days)** — small history list.
  4. **Status footer** — last screen date, last mentions/morning run, and the
     "decision support, not financial advice" disclaimer.
- **`/api/data` Pages Function** (`web/functions/api/data.js`): queries Turso
  with a **read-only** token via `@libsql/client`, returns the JSON the page
  renders. Token from a Cloudflare environment variable/secret (never shipped to
  the browser; all DB access is server-side in the Function). Read-only token
  created with `turso db tokens create --read-only`.
- **Deploy:** `wrangler` (Pages) or the Pages Git integration. A
  `web/wrangler.toml` + README in `web/` documents `npm`/`wrangler` setup.
- **Caching:** the Function may set a short `Cache-Control` (data updates a few
  times daily) to stay well inside free limits.

## Component 5 — Deploy story rewrite

The personal-machine deploy (podman quadlets `deploy/*.container` / `*.timer`,
`install.sh`) targets exactly the setup the operator is leaving and is
Discord-coupled. **Retire it:** remove the quadlet/timer units and `install.sh`,
and replace `deploy/INSTALL.md` (and the README deploy section) with a concise
**cloud deploy guide**:

1. Fork/clone; create a Turso DB (`turso db create`, get URL + tokens).
2. Add GitHub repo secrets (Turso URL/token, PRAW optional).
3. Seed once: run the `screen` workflow via `workflow_dispatch` with `--force`
   (or `python -m gexwheel screen --force` locally against Turso) to populate
   the primary watchlist.
4. Create a Cloudflare Pages project from `web/`, set the read-only Turso token
   env var, deploy.
5. Enable GitHub Actions email notifications for failed workflows.

A minimal **local-dev** path remains documented (stdlib sqlite via a local
`db_path`, `PYTHONPATH=src pytest`) so contributors don't need cloud accounts.

> Retiring `install.sh`/quadlets is a deliberate scope call surfaced for operator
> sign-off in the spec-review gate; if the operator wants a self-host option
> preserved, we keep a Discord-free, Turso-capable variant instead of deleting.

## Error handling & resilience

- Network rules unchanged: stdlib `logging`, per-call timeouts, exponential
  backoff, per-symbol try/except (one symbol never kills a run).
- Turso unreachable at job start → fail the run non-zero (so email fires) rather
  than silently proceeding against a non-existent DB.
- Dashboard Function: on Turso error, return a 5xx with a small JSON error; the
  page shows "data temporarily unavailable" instead of blank.
- All dates still via `zoneinfo.ZoneInfo(cfg['timezone'])`. Cron is UTC; the
  job's internal `asof` remains market-tz correct regardless of trigger time.

## Testing

- Full suite stays green offline (`PYTHONPATH=src pytest`) — shim routes tests to
  stdlib sqlite.
- New unit tests: `morning` alert path without Discord (trades persisted, no
  truncation, dedup by identification not delivery); `config` no longer requires
  `discord`; `__main__` has no `test-discord`.
- `db` connection-shim unit test: `libsql://`-style URL selects the libsql path
  (mocked client), everything else selects stdlib sqlite.
- Network-gated Turso smoke test (skipped without creds).
- `rg -i 'discord|webhook'` shows no live code references (only intentional
  historical notes).
- The `web/` Function gets a tiny test or a documented manual `wrangler dev`
  check (JS side; not part of pytest).

## Out of scope

- Real-time/intraday data; the dashboard reflects the last completed run.
- Auth/private access (public read-only by decision; Cloudflare Access can be
  added later without code changes).
- Migrating Subsystem A logic (done in A's spec) — B only changes
  storage/hosting/output.
- Position/PnL tracking (system remains alert/identification-only).
```
