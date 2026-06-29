# Dashboard UI requirements

## Price band (user-adjustable, end of pipeline)

The Python pipeline applies a **loose structural floor only** (`filters.price_min`,
default **$5**). There is **no pipeline `price_max`** — names above any account
collateral cap still flow through screen → watchlist → morning identification.

**Account-specific sizing belongs on the dashboard**, after identification:

### Requirement: client-side price band filter

1. **Placement** — above "Today's identified trades" and affecting both that
   section and the active watchlist table (same band applies to all displayed
   rows that expose `spot`).
2. **Controls** — numeric inputs:
   - **Min price** (default `$5`, must be ≥ pipeline floor but user may raise it)
   - **Max price** (default **empty / unlimited**; blank means no upper cap)
3. **Behavior** — filter rows where `spot` is within `[min, max]` (inclusive).
   Hide non-matching rows; do not delete or mutate backend data.
4. **Persistence** — save min/max in `localStorage` so refresh keeps the user's band.
5. **Feedback** — show `Showing N of M` when the filter hides rows.
6. **Out of scope (v1)** — no server-side config write; no Turso column for user
   preferences; no auth. Collateral guidance is copy-only (e.g. helper text:
   "Set max to match your per-contract collateral budget").

### Pipeline vs UI

| Layer | Min | Max |
|-------|-----|-----|
| `screen` / `run_filters` | `config filters.price_min` ($5) | none (`price_max: null`) |
| Dashboard | user input (default $5) | user input (default unlimited) |

Implement in `web/public/` when building the price-band UI; see
`docs/superpowers/specs/2026-06-15-cloud-hosting-and-dashboard-design.md`
(Component 4) for the overall dashboard layout.
