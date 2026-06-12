# Repo review remediation plans — index (2026-06-12)

A full review of the codebase (all 16 source modules, schema/migrations,
config, docs, deploy, 47-test suite) produced 15 findings. This index maps
every finding to the plan and task that fixes it, so a fresh agent can pick up
any plan without re-deriving the review.

**Start here if you are implementing:** each plan is independent and
self-contained — pick one, follow its tasks in order, run
`PYTHONPATH=src pytest` after every change, commit per task. Plans follow the
superpowers writing-plans format; use subagent-driven-development or
executing-plans to work through them.

**Hard constraints (from `.cursor/rules/` and `AGENTS.md`), repeated because
they bound every fix:** do not rename `models.py` fields, do not change
`schema.sql`, do not change public function/class signatures, no new
dependencies, all dates via `zoneinfo`, no threading around SQLite.

## Plans

| Plan | Fixes | Severity |
|------|-------|----------|
| [2026-06-12-chains-nan-coercion.md](./2026-06-12-chains-nan-coercion.md) | NaN handling bug in yfinance chain parsing; dead retry loop | **Bug — highest priority** |
| [2026-06-12-morning-job-fixes.md](./2026-06-12-morning-job-fixes.md) | Velocity baseline mixes mention sources; duplicated truncation; dead code; unused imports | **Bug + cleanup** |
| [2026-06-12-consistency-cleanups.md](./2026-06-12-consistency-cleanups.md) | Dedup blocks retries of unsent alerts; missing retry on price history; deprecated `utcnow()`; assorted consistency fixes | Bug-adjacent + consistency |
| [2026-06-12-docs-and-deps-cleanup.md](./2026-06-12-docs-and-deps-cleanup.md) | Stale build-order scaffolding misdirecting agents; misleading dependency comments; docstring mismatch | Docs only |
| [2026-06-12-deferred-decisions.md](./2026-06-12-deferred-decisions.md) | Findings needing owner sign-off (frozen contracts / product calls) — **not** an executable plan | Decision log |

Suggested execution order: chains → morning → consistency → docs. There are no
code dependencies between plans, but this order front-loads the real bugs.

## Finding-to-plan map

Numbering matches the original review message.

| # | Finding | Where it's handled |
|---|---------|--------------------|
| 1 | `chains.py` drops/pollutes rows with NaN OI/bid/ask (`or 0` idiom; NaN is truthy) | chains-nan-coercion, Task 1 |
| 2 | Morning velocity SQL mixes apewisdom/praw sources | morning-job-fixes, Task 1 |
| 3 | `prices.py` uses deprecated, rule-violating `datetime.utcnow()` | consistency-cleanups, Task 4 |
| 4 | Truncation logic duplicated between `morning.run()` and `discord.send_alerts()`, with mismatched config access | morning-job-fixes, Task 2 |
| 5 | Config defaults triple-sourced (example YAML + scattered `.get` fallbacks) | Partially: morning-job-fixes Task 2 aligns the worst case; full consolidation deliberately not planned (low value, high churn) |
| 6 | `scoring.score()` hardcodes 3% proximity instead of cfg | consistency-cleanups Task 1 (named constant); full cfg threading is deferred-decisions D1 |
| 7 | Redundant function-local `datetime`/`db` imports | consistency-cleanups Task 1 (scoring) + Task 2 (filters); morning-job-fixes Task 3 (morning) |
| 8 | Unused `ChainFetchError`/`PriceFetchError` imports in `morning.py` | morning-job-fixes, Task 3 |
| 9 | `mentions.py` inconsistent upvotes fallback; `MentionFetchError` defined after use | consistency-cleanups, Task 3 |
| 10 | No retry/backoff in `prices.py` network calls | consistency-cleanups, Task 4 |
| 11 | Pointless retry loop around lazy `yf.Ticker` constructor | chains-nan-coercion, Task 2 |
| 12 | Dead fallback payload in morning alert logging | morning-job-fixes, Task 2 |
| 13 | Stale/duplicated build-order docs misdirecting agents | docs-and-deps-cleanup, Task 1 |
| 14 | `schema.sql` vs `migrations/0001` dual schema source | deferred-decisions, D2 (frozen contract — no action without sign-off) |
| 15 | `pandas`/`numpy`/`dateutil` pins look like direct deps but aren't | docs-and-deps-cleanup, Task 2 |

Additional review observations and their disposition:

- `should_alert` dedups on *any* alerts row, so failed sends are never retried
  same-day (contradicts the `discord.py` spec) → consistency-cleanups, Task 1.
- `mentions_daily` docstring claims a commit the code doesn't do →
  docs-and-deps-cleanup, Task 3.
- Wall persistence counts snapshots, not calendar days → deferred-decisions, D3.
- `_refresh_sector` is once-ever and skips manual watchlist names →
  deferred-decisions, D4.
- Below-cap alert cards are never logged → deferred-decisions, D5.
- Thin test coverage for `vol.atm_iv` and chain row parsing → the chains plan
  adds row-parsing coverage; `atm_iv` tests were judged nice-to-have and are
  not planned (add alongside any future `vol.py` change).
