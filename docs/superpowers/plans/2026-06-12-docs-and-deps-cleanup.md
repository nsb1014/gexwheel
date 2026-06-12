# Docs and Dependency Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire stale build-order process scaffolding that actively misdirects agents, clarify which `requirements.txt` entries are actually used directly, and fix the `jobs/mentions_daily.py` docstring/implementation mismatch about transaction ownership.

**Architecture:** Documentation-only changes plus one docstring edit. No code behavior changes, no schema changes, no signature changes. Each task commits independently.

**Tech Stack:** Markdown, `.cursor/rules/*.mdc` files.

---

## Background

The build-order content exists in four places (`IMPLEMENTATION_GUIDE.md`, `AGENTS.md`, `.cursor/rules/gexwheel-build-order.mdc`, `.cursor/rules/gexwheel-core.mdc`), and all 10 build steps are marked IMPLEMENTED in `AGENTS.md`. Yet the always-applied rule `gexwheel-build-order.mdc` still commands "Do not skip ahead — later steps depend on earlier ones", and `IMPLEMENTATION_GUIDE.md` step 7 tells agents to "unskip tests/test_filters.py" (long since unskipped, 176 lines, passing). A fresh agent following the always-applied rules wastes effort re-validating a finished sequence — or worse, refuses legitimate work because it "skips ahead".

Separately, `requirements.txt` lists `pandas`, `numpy`, and `python-dateutil` under "Core" although nothing in `src/` imports them directly (verify with `rg "import (pandas|numpy|dateutil)" src/` — no matches); they are transitive dependencies of yfinance kept only as version floors. The comment should say so, so nobody "cleans up" unused-looking pins or assumes the codebase is pandas-based.

---

### Task 1: Mark the build-order rule as historical

**Files:**
- Modify: `.cursor/rules/gexwheel-build-order.mdc`

- [ ] **Step 1: Rewrite the rule**

Replace the entire contents of `.cursor/rules/gexwheel-build-order.mdc` with:

```markdown
# Build order (HISTORICAL — all steps complete)

All 10 build-order steps are IMPLEMENTED and covered by local tests
(see the status table in AGENTS.md). The original sequenced table lives in
IMPLEMENTATION_GUIDE.md for historical reference only.

What still applies from the original build rules:

- Run `PYTHONPATH=src pytest` after every change; the suite must stay green.
- Frozen contracts (models.py field names, schema.sql, public signatures in
  the listed modules) remain frozen — see gexwheel-core.mdc.
- Module docstrings remain the behavior spec for each module.

Do NOT treat the build order as a gate on new work: there is no longer any
"do not skip ahead" constraint, and tests/test_filters.py is already
unskipped and passing.
```

- [ ] **Step 2: Add a historical banner to IMPLEMENTATION_GUIDE.md**

Insert at the very top of `IMPLEMENTATION_GUIDE.md`, above the existing title line:

```markdown
> **STATUS: HISTORICAL.** Every build-order step below is implemented and
> tested (see AGENTS.md status table). Kept for reference: the ground rules
> in items 1–5 and the gotchas list still apply to new work; the build-order
> table and its "Verify by" column do not gate anything anymore.

```

Leave the rest of the file untouched (it is referenced from AGENTS.md, README.md, and the core rule).

- [ ] **Step 3: Verify nothing references the removed wording**

Run: `rg -i "do not skip ahead" --glob '!docs/superpowers/**'`
Expected: no matches (the phrase only existed in the rule file you just rewrote).

- [ ] **Step 4: Commit**

```bash
git add .cursor/rules/gexwheel-build-order.mdc IMPLEMENTATION_GUIDE.md
git commit -m "docs: mark build-order rule and guide as historical, drop stale do-not-skip-ahead gate"
```

---

### Task 2: Clarify transitive pins in requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Rewrite the comments**

Replace the contents of `requirements.txt` with:

```text
# Direct dependencies
requests>=2.31
PyYAML>=6.0

# Data sources
yfinance>=0.2.50        # option chains + price history (free tier)
praw>=7.7               # OPTIONAL: Reddit API fallback for mention counts

# Transitive version floors (pulled in by yfinance; nothing in src/ imports
# these directly — pinned only to keep resolver behavior predictable)
pandas>=2.0
numpy>=1.26
python-dateutil>=2.8

# Dev
pytest>=8.0
```

- [ ] **Step 2: Verify install still resolves and tests pass**

Run: `pip install -r requirements.txt -q && PYTHONPATH=src python3 -m pytest -q`
Expected: clean install, all tests pass.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "docs(deps): label pandas/numpy/dateutil as transitive yfinance pins"
```

---

### Task 3: Fix mentions_daily docstring transaction-ownership mismatch

**Files:**
- Modify: `src/gexwheel/jobs/mentions_daily.py:13-14` (docstring only)

**Background.** The docstring's step 6 says `conn.commit(); conn.close()`, but `run()` only closes — correctly, because `screening.discovery.run_discovery` commits internally (twice: after persisting mentions and after upserting triggered tickers). The docstring and code should agree on who owns the transaction.

- [ ] **Step 1: Amend the docstring**

Change step 6 of the module docstring from:

```
  6. conn.commit(); conn.close(). Exit code 0 unless db itself failed -
     systemd treats nonzero as failure and journald captures the trace.
```

to:

```
  6. conn.close(). run_discovery() owns its commits (it commits after
     persisting mentions and after upserting triggers). Exit code 0 unless
     db itself failed - systemd treats nonzero as failure and journald
     captures the trace.
```

No code changes.

- [ ] **Step 2: Run the full suite, then commit**

Run: `PYTHONPATH=src python3 -m pytest -q` — all pass.

```bash
git add src/gexwheel/jobs/mentions_daily.py
git commit -m "docs(mentions_daily): docstring matches actual transaction ownership"
```
