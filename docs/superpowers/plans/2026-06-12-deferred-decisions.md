# Deferred Decisions (need owner sign-off — do NOT implement without it)

> **STATUS: OPEN** — sole remaining track from the 2026-06-12 repo review.
> All four executable remediation plans are complete (see [README.md](./README.md)).

These review findings touch frozen contracts (`models.py` fields, `schema.sql`,
public signatures) or require a product decision. They are documented here so
they are not lost and not accidentally "fixed" by an agent. Each entry states
the finding, the options, and a recommendation.

## D1. `scoring.score()` cannot see configured proximity

**Finding:** `score()`'s proximity component uses a fixed 3% (`DEFAULT_PROXIMITY_PCT`
after the consistency-cleanups plan), while `should_alert()` reads
`cfg["alerts"]["put_wall_proximity_pct"]`. If a user tunes proximity to 5%,
alerts fire at 5% but the proximity score is clamped to 0 beyond 3%.

**Blocker:** `score(profile, iv_rank_val, vrp_val, velocity_ratio)` is a frozen
public signature; passing `cfg` requires a signature change.

**Options:** (a) add an optional keyword arg `proximity_pct=DEFAULT_PROXIMITY_PCT`
(arguably not a breaking change — existing call sites keep working);
(b) accept the constant and document it (current state).

**Recommendation:** (a), with the call site in `jobs/morning.py` passing the
configured value. Needs owner confirmation that optional kwargs don't violate
the frozen-signature rule.

## D2. `schema.sql` + `migrations/0001_initial.sql` dual bootstrap

**Finding:** `db.connect()` executes `schema.sql` AND all migrations on every
connect; `0001_initial.sql` is a wholesale copy of `schema.sql`. Going forward
`schema.sql` becomes a permanently stale baseline that must never change while
still being named "the schema".

**Blocker:** `schema.sql` is explicitly frozen by the core rules, and
`docs/database-migrations.md` documents the dual arrangement as intended.

**Options:** (a) keep as is (documented, works, mildly confusing);
(b) make `connect()` apply only migrations and delete the
`executescript(schema.sql)` line, keeping `schema.sql` as a read-only
reference document; (c) generate `schema.sql` from migrations in CI.

**Recommendation:** (a) for now; revisit at migration 0003+ when drift becomes
real. Any change requires explicit owner approval per the frozen-contract rule.

## D3. Wall "persistence" has no date window

**Finding:** `should_alert` requires the put wall to match across the last N
`gex_snapshots` **rows**, but `db.recent_put_walls` has no date constraint.
If a symbol skipped days (benched, fetch failures, weekends), "2 consecutive
days" can actually span weeks. The docstring promises consecutive days; the
code delivers consecutive snapshots.

**Blocker:** product decision — snapshot-persistence may actually be the better
signal (walls only move when OI moves, and OI snapshots only exist on fetch
days). Changing `recent_put_walls` semantics alters alerting behavior.

**Options:** (a) keep row-based persistence, amend the `scoring.py` docstring
to say "last N snapshots"; (b) add a date-window predicate
(`date >= asof - N*2 days`) to `recent_put_walls`'s query.

**Recommendation:** (a) — simpler, and the existing tests
(`tests/test_scoring.py`) encode row-based semantics.

## D4. `_refresh_sector` is once-ever, not once-per-week

**Finding:** `jobs/morning.py:_refresh_sector` populates `tickers.sector` only
when a row exists with NULL sector. The `prices.py` docstring describes an
"at most once per symbol per week" cache. Consequences: a sector change never
refreshes, and a watchlist symbol added manually without a `tickers` row never
gets a sector at all — making the biotech exclusion silently pass for it.

**Blocker:** needs a small schema addition (`tickers.sector_updated_at`) to do
properly, i.e. a new migration — schema evolution needs owner sign-off per
`docs/database-migrations.md`.

**Options:** (a) keep once-ever (sectors rarely change; manual watchlist adds
are expected to insert a tickers row); (b) add `0002_ticker_sector_updated_at.sql`
migration + weekly refresh logic; (c) at minimum, make `_refresh_sector` insert
a tickers row (via `db.upsert_ticker`) when missing, so manual watchlist names
get classified.

**Recommendation:** (c) as a low-risk improvement; (b) only if sector data
quality becomes a real problem.

## D5. Cards beyond `max_alerts_per_run` are never logged

**Finding:** only the top-N cards are written to the `alerts` table; cards
ranked below the cap leave no trace. The morning-job docstring says "db.log_alert
for each candidate card", which is ambiguous about below-cap cards.

**Options:** (a) current behavior — below-cap cards vanish (they will re-qualify
and alert on a later run if still valid); (b) log them with `sent_at=NULL`.
Note (b) interacts with dedup: after the scoring fix (consistency-cleanups
Task 1), unsent rows no longer dedup, so (b) becomes safe but also pointless
except as an audit trail.

**Recommendation:** (a); revisit only if an audit trail of suppressed alerts
is wanted.
