> **STATUS: HISTORICAL.** Every build-order step below is implemented and
> tested (see AGENTS.md status table). Kept for reference: the ground rules
> in items 1–5 and the gotchas list still apply to new work; the build-order
> table and its "Verify by" column do not gate anything anymore.

# Implementation guide (for the implementing model)

Ground rules:
1. **Do not change** `models.py` field names, `schema.sql`, or the public
   signatures in stub modules - tests and DB rows key off them.
2. Every stub's docstring is the spec. Implement exactly that; if the spec is
   ambiguous, choose the simpler behavior and leave a `# NOTE:` comment.
3. After each step run `PYTHONPATH=src pytest` - the gex/velocity tests are a
   regression net and must stay green.
4. Network code: stdlib `logging`, timeouts on every request, retry w/
   exponential backoff, and **never let one symbol's failure kill a run**.
5. No new dependencies beyond requirements.txt without strong reason.

Build order (each step is independently testable):

| # | Module | Verify by |
|---|--------|-----------|
| 1 | `analytics/vol.py` | unit tests you write: realized_vol of a constant series = 0; iv_rank of max value = ~100 |
| 2 | `data/mentions.py` (apewisdom only) | `python - <<'P'` snippet calling fetch_apewisdom and printing 5 records; log raw keys at DEBUG |
| 3 | `screening/discovery.py` | seed a temp DB with 6 days of fake mentions, assert trigger list |
| 4 | `jobs/mentions_daily.py` | `python -m gexwheel mentions` against a scratch config; check mentions table |
| 5 | `data/chains.py` YFinanceChains | fetch SMR; assert quotes nonempty, then `gex.compute_profile` prints sane walls |
| 6 | `data/prices.py` | closes len >= 60; sma(closes,50) < spot for an uptrending name |
| 7 | `screening/filters.py` | **unskip tests/test_filters.py and make them pass** (build the fixtures the docstrings describe) |
| 8 | `alerts/scoring.py` | unit tests: persistence gate, proximity edge cases (spot exactly at wall, spot below wall) |
| 9 | `alerts/discord.py` | `python -m gexwheel test-discord` posts to a real webhook |
| 10 | `jobs/morning.py` | end-to-end dry run with 2-3 hand-seeded watchlist tickers |

Gotchas already known (do not rediscover them the hard way):
- yfinance `option_chain()` off-hours returns bid/ask of 0 - filters spec
  already defines the 'no_quote' FAIL behavior.
- yfinance throttles aggressively: respect `request_sleep_s` between EVERY
  chain call, not just per symbol.
- ApeWisdom field names are unofficial - verify `results[0].keys()` on first
  run and adapt the mapping in ONE place.
- All dates flow through `zoneinfo.ZoneInfo(cfg['timezone'])`; never
  `date.today()` bare (the container runs UTC).
- sqlite: single writer - jobs already run serially via timers, do not add
  threading.
