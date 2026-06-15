# Screening inversion + cloud migration — plan index (2026-06-15)

Two subsystems, four executable plans. **Execute strictly in order** — later
plans update tests/code introduced by earlier ones.

## Specs (the why/what)

- Subsystem A: [specs/2026-06-15-screening-inversion-design.md](../specs/2026-06-15-screening-inversion-design.md)
- Subsystem B: [specs/2026-06-15-cloud-hosting-and-dashboard-design.md](../specs/2026-06-15-cloud-hosting-and-dashboard-design.md)

## Plans (the how) — run in this order

| # | Plan | Subsystem | Produces |
|---|------|-----------|----------|
| 1 | [2026-06-15-a1-primary-screen.md](./2026-06-15-a1-primary-screen.md) | A | `screen` job, `primary_watchlist`/`app_state` migration, `screening/primary.py`, shared `chain_metrics`, `avg_volume` gate, config/CLI |
| 2 | [2026-06-15-a2-narrow-reddit-and-morning.md](./2026-06-15-a2-narrow-reddit-and-morning.md) | A | discovery tracks only primary members + promotes via velocity; morning runs on the active watchlist only |
| 3 | [2026-06-15-b1-remove-discord-failloud.md](./2026-06-15-b1-remove-discord-failloud.md) | B | Discord removed (code + docs); trades persisted for the dashboard; jobs fail-loud for GitHub-email alerting |
| 4 | [2026-06-15-b2-turso-actions-dashboard.md](./2026-06-15-b2-turso-actions-dashboard.md) | B | Turso `db.connect()` shim, GitHub Actions cron + keepalive + CI, Cloudflare Pages dashboard, cloud deploy guide (retires `install.sh`/quadlets) |

## Cross-plan dependencies (why the order matters)

- A2 narrows `discovery`/`morning` around the `primary_watchlist` that A1 creates.
- B1 keeps `"discord"` in `config.REQUIRED_KEYS` until its own task removes it; A1 added `"screen"` alongside it.
- B1 Task 7 (fail-loud) updates a test that A1 created (`test_screen_job.py` abort case now expects `JobError`).
- B2 deletes `install.sh`/quadlets and assumes B1 already stripped Discord from them.

## Invariants every plan keeps

- `PYTHONPATH=src pytest` green after every task (offline; the Turso smoke test is skipped without creds).
- Frozen contracts: no `models.py` field renames, no `schema.sql` edits (new tables via migrations), no public `db.*`/job/`run_filters`/`run_discovery` signature changes.
- One new runtime dependency total (`libsql-experimental`, B2) — operator-approved.

## Operator setup required after implementation (not code)

- A Turso database + read-write and read-only tokens.
- GitHub repo secrets: `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`.
- A Cloudflare Pages project from `web/` with `TURSO_DATABASE_URL` + `TURSO_READONLY_TOKEN`.
- One-time seed: run `screen --force` (workflow_dispatch or locally) to populate the primary watchlist.
- Enable GitHub → Settings → Notifications → Actions → failed-workflow emails.
