# gexwheel dashboard (Cloudflare Pages)

Public, read-only view of the active watchlist and the day's identified trades.
Reads Turso (hosted libSQL) from a Pages Function with a read-only token.

## Local dev

```bash
cd web
npm install
TURSO_DATABASE_URL=... TURSO_READONLY_TOKEN=... npm run dev
```

## Deploy

1. `turso db tokens create <db> --read-only` → copy the token.
2. Create a Cloudflare Pages project pointing at `web/` (build output dir `public`).
3. Set Pages env vars `TURSO_DATABASE_URL` and `TURSO_READONLY_TOKEN`.
4. `npm run deploy` (or connect the repo for Git-based deploys).

The Function lives at `functions/api/data.js` → served at `/api/data`.
