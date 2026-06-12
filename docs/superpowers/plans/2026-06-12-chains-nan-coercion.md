# Chains NaN Coercion Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `YFinanceChains` coerce pandas NaN values to 0 (as its docstring spec requires) instead of silently dropping rows or propagating NaN into `OptionQuote`, and remove the dead retry loop around the lazy `yf.Ticker` constructor.

**Architecture:** Add one private numeric-coercion helper to `src/gexwheel/data/chains.py` and use it in the per-row parsing loop inside `YFinanceChains.fetch`. No public signatures change (frozen contract). Tests use real `pandas.DataFrame` objects with NaN cells and a fake ticker, following the monkeypatch pattern already used in `tests/test_chains.py`.

**Tech Stack:** Python stdlib (`math`), pandas (already a dependency via yfinance), pytest with `monkeypatch`.

---

## Background (why this is a bug)

The module docstring spec says: *"Skip rows where openInterest is NaN/0 AND impliedVolatility is NaN (dead strikes). Coerce NaN OI to 0, NaN bid/ask to 0.0."*

The current implementation uses the `or 0` idiom, which does NOT handle NaN because `float('nan')` is truthy:

```113:131:src/gexwheel/data/chains.py
            for df, kind in ((oc.calls, "C"), (oc.puts, "P")):
                for _, row in df.iterrows():
                    try:
                        oi = int(row.get("openInterest") or 0)
                        iv = float(row.get("impliedVolatility") or 0)
                        if oi == 0 and iv == 0:
                            continue  # dead strike
                        bid = float(row.get("bid") or 0.0)
                        ask = float(row.get("ask") or 0.0)
                        strike = float(row["strike"])
                        quotes.append(OptionQuote(
                            symbol=symbol, strike=strike, expiry=exp_date,
                            kind=kind, open_interest=max(oi, 0),
                            iv=iv if iv > 0 else 0.0,
                            bid=bid, ask=ask,
                        ))
                    except Exception as exc:
                        log.debug("skipping bad row %s %s: %s", symbol, kind, exc)
```

Two failure modes (verified in a Python shell):

1. `int(float('nan') or 0)` raises `ValueError`, so a row with NaN open interest but a **valid IV** is swallowed by the catch-all `except` and silently dropped — contradicting the spec.
2. `float(float('nan') or 0.0)` returns `nan`, so NaN bid/ask propagate into `OptionQuote`. Downstream, `OptionQuote.spread_pct` and the `bid == 0 and ask == 0` "no_quote" check in `screening/filters.py` misbehave because all NaN comparisons are False.

Separately, `YFinanceChains._get_ticker` wraps `yf.Ticker(symbol)` in a 3-attempt retry loop, but the constructor is lazy (no network I/O) and effectively cannot fail; the loop is dead code that obscures where real network failures happen.

---

### Task 1: NaN coercion in quote-row parsing

**Files:**
- Modify: `src/gexwheel/data/chains.py` (imports near line 30; row loop at lines 113-131)
- Test: `tests/test_chains.py` (append new tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chains.py` (file already imports `logging`, `date`, `SimpleNamespace`, `YFinanceChains`; add `timedelta` and `pandas` imports at the top of the file alongside the existing imports):

```python
import pandas as pd
from datetime import timedelta

ASOF = date(2026, 6, 10)
EXP_STR = (ASOF + timedelta(days=30)).isoformat()

_COLS = ["strike", "openInterest", "impliedVolatility", "bid", "ask"]


def _ticker_with_chain(calls: pd.DataFrame, puts: pd.DataFrame):
    class FakeTicker:
        fast_info = {"last_price": 100.0}
        options = [EXP_STR]

        def option_chain(self, exp):
            return SimpleNamespace(calls=calls, puts=puts)

    return FakeTicker()


def _fetch(monkeypatch, calls: pd.DataFrame, puts: pd.DataFrame):
    ticker = _ticker_with_chain(calls, puts)
    monkeypatch.setattr(YFinanceChains, "_get_ticker", lambda self, symbol: ticker)
    return YFinanceChains(request_sleep_s=0).fetch("TEST", ASOF, max_dte=60)


def test_nan_open_interest_coerced_to_zero_not_dropped(monkeypatch):
    calls = pd.DataFrame([{"strike": 100.0, "openInterest": float("nan"),
                           "impliedVolatility": 0.5, "bid": 1.0, "ask": 1.1}])
    puts = pd.DataFrame([], columns=_COLS)

    _, quotes = _fetch(monkeypatch, calls, puts)

    assert len(quotes) == 1
    assert quotes[0].open_interest == 0
    assert quotes[0].iv == 0.5


def test_nan_bid_ask_coerced_to_zero(monkeypatch):
    calls = pd.DataFrame([{"strike": 100.0, "openInterest": 250,
                           "impliedVolatility": 0.4,
                           "bid": float("nan"), "ask": float("nan")}])
    puts = pd.DataFrame([], columns=_COLS)

    _, quotes = _fetch(monkeypatch, calls, puts)

    assert len(quotes) == 1
    assert quotes[0].bid == 0.0
    assert quotes[0].ask == 0.0


def test_row_with_nan_oi_and_nan_iv_is_skipped_as_dead_strike(monkeypatch):
    calls = pd.DataFrame([{"strike": 100.0, "openInterest": float("nan"),
                           "impliedVolatility": float("nan"),
                           "bid": 1.0, "ask": 1.1}])
    puts = pd.DataFrame([], columns=_COLS)

    _, quotes = _fetch(monkeypatch, calls, puts)

    assert quotes == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_chains.py -v`
Expected: the two coercion tests FAIL (`len(quotes) == 1` assertions fail because the NaN-OI row is dropped, and `bid == 0.0` fails because bid is `nan`); the dead-strike test may already pass (the row is dropped today, just for the wrong reason — `ValueError`, not the dead-strike branch). The three pre-existing tests must still PASS.

- [ ] **Step 3: Implement the coercion helper and use it in the row loop**

In `src/gexwheel/data/chains.py`, add `import math` to the stdlib import block at the top (after `import logging`), then add this helper above `class ChainFetchError`:

```python
def _num(value, default: float = 0.0) -> float:
    """Coerce a pandas cell to float; NaN/None/unparseable -> default.

    `value or 0` does NOT work here: float('nan') is truthy.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f
```

Replace the row-parsing body (the five assignment lines inside the `try` at lines 116-122) with:

```python
                    try:
                        oi = int(_num(row.get("openInterest")))
                        iv = _num(row.get("impliedVolatility"))
                        if oi == 0 and iv == 0:
                            continue  # dead strike
                        bid = _num(row.get("bid"))
                        ask = _num(row.get("ask"))
                        strike = float(row["strike"])
```

Everything else in the loop (the `OptionQuote(...)` construction and the `except`) stays identical.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_chains.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: all tests pass (50 = 47 pre-existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add src/gexwheel/data/chains.py tests/test_chains.py
git commit -m "fix(chains): coerce NaN OI/bid/ask to 0 per spec instead of dropping rows"
```

---

### Task 2: Remove dead retry loop around the lazy yf.Ticker constructor

**Files:**
- Modify: `src/gexwheel/data/chains.py:55-62` (`_get_ticker`)

- [ ] **Step 1: Replace the retry loop with a direct call**

Replace the entire `_get_ticker` method:

```python
    def _get_ticker(self, symbol: str):
        # yf.Ticker is a lazy constructor (no network I/O); real failures
        # surface later on fast_info/options/option_chain access, which
        # have their own retry handling.
        return yf.Ticker(symbol)
```

Keep the method (do not inline it): `tests/test_chains.py` monkeypatches `YFinanceChains._get_ticker` and it is part of the class's internal seam.

- [ ] **Step 2: Run the full suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: all tests pass. The two existing tests that monkeypatch `_get_ticker` or `yf.Ticker` are unaffected.

- [ ] **Step 3: Commit**

```bash
git add src/gexwheel/data/chains.py
git commit -m "refactor(chains): drop pointless retry around lazy yf.Ticker constructor"
```
