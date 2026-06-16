# Deploy (cloud, free tier)

gexwheel runs entirely on free services — no personal machine required.

- Compute: GitHub Actions (cron) — `.github/workflows/{mentions,morning,keepalive,ci}.yml`
- Database: Turso (hosted libSQL)
- Dashboard: Cloudflare Pages (`web/`)

## 1. Turso

```bash
curl -sSfL https://get.tur.so/install.sh | bash
turso auth signup
turso db create gexwheel
turso db show gexwheel --url                 # -> TURSO_DATABASE_URL
turso db tokens create gexwheel              # -> TURSO_AUTH_TOKEN (read-write, for jobs)
turso db tokens create gexwheel --read-only  # -> TURSO_READONLY_TOKEN (for the dashboard)
```

## 2. GitHub repo secrets (Settings → Secrets and variables → Actions)

- `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
- (optional) PRAW creds if you switch `reddit.source` away from apewisdom

Enable Settings → Notifications → Actions → failed workflows only (ops alerting).

## 3. Seed the primary watchlist (once)

Run the `mentions` workflow via "Run workflow" (workflow_dispatch), or locally:

```bash
TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... PYTHONPATH=src python -m gexwheel screen --force
```

## 4. Cloudflare Pages

See `web/README.md`. Create a Pages project from `web/`, set
`TURSO_DATABASE_URL` + `TURSO_READONLY_TOKEN`, deploy.

## Local dev

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src pytest                 # offline, stdlib sqlite
# point at a throwaway local DB:
PYTHONPATH=src python -m gexwheel screen --force   # writes ./config default db_path
```
