"""Reddit mention counts. TODO(sonnet): implement fetch_apewisdom (+ optional PRAW).

fetch_apewisdom(filter_name, pages, asof) -> list[MentionRecord]
  * GET https://apewisdom.io/api/v1.0/filter/{filter_name}/page/{n}/
    for n in 1..pages. Plain requests.get, 15s timeout, retry x3 w/ backoff.
  * Response JSON: {"results": [{"ticker": "SMR", "mentions": 123,
    "upvotes": 456, "rank": 7, ...}, ...]}  -- VERIFY the exact field names
    at runtime on first call and log the raw keys of results[0] at DEBUG;
    apewisdom is an unofficial free API and may drift. "mentions" may arrive
    as a string - coerce with int().
  * Normalize tickers: .upper().strip(); drop anything that is not 1-5
    alphabetic chars (kills '$', crypto pairs, garbage).
  * Dedup across pages keeping the higher mention count.
  * Map to MentionRecord(source='apewisdom', asof=asof).
  * On total failure raise MentionFetchError - the job decides whether to
    fall back to PRAW.

fetch_praw(cfg, asof) -> list[MentionRecord]   [OPTIONAL fallback - implement last]
  * praw.Reddit(client_id, client_secret, user_agent) read-only.
  * Pull the ~500 newest posts + top comments from r/wallstreetbets for the
    day, regex tickers as cashtags (\$[A-Z]{1,5}) and bare uppercase words
    that appear in a known-symbols set (seed from DB tickers table), count.
  * This is noisier than apewisdom; tag source='praw' so velocity baselines
    never mix sources (db PK already separates by source).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date

import requests

from ..models import MentionRecord

log = logging.getLogger(__name__)

APEWISDOM_URL = "https://apewisdom.io/api/v1.0/filter/{flt}/page/{page}/"
_TICKER_RE = re.compile(r'^[A-Z]{1,5}$')


def _get_with_retry(url: str, retries: int = 3, timeout: int = 15) -> dict:
    """GET with exponential backoff. Returns parsed JSON or raises MentionFetchError."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt == retries - 1:
                raise MentionFetchError(f"failed after {retries} attempts: {url}: {exc}") from exc
            sleep = 2 ** attempt
            log.warning("apewisdom attempt %d failed (%s), retrying in %ds", attempt + 1, exc, sleep)
            time.sleep(sleep)


class MentionFetchError(RuntimeError):
    pass


def fetch_apewisdom(filter_name: str, pages: int, asof: date) -> list[MentionRecord]:
    """Fetch mention counts from ApeWisdom for `pages` pages of `filter_name`."""
    seen: dict[str, MentionRecord] = {}
    pages_ok = 0

    for page in range(1, pages + 1):
        url = APEWISDOM_URL.format(flt=filter_name, page=page)
        try:
            data = _get_with_retry(url)
        except MentionFetchError as exc:
            log.error("apewisdom page %d failed: %s", page, exc)
            continue
        pages_ok += 1

        results = data.get("results", [])
        if results:
            log.debug("apewisdom page %d raw keys: %s", page, list(results[0].keys()))

        for item in results:
            raw_ticker = item.get("ticker") or item.get("name") or ""
            symbol = raw_ticker.upper().strip()
            if not _TICKER_RE.match(symbol):
                continue

            try:
                mentions = int(item.get("mentions", 0))
            except (TypeError, ValueError):
                mentions = 0

            try:
                rank = int(item["rank"]) if item.get("rank") is not None else None
            except (TypeError, ValueError):
                rank = None

            try:
                upvotes = int(item.get("upvotes", 0) or 0)
            except (TypeError, ValueError):
                upvotes = None

            # keep highest mention count if symbol appears across pages
            if symbol not in seen or mentions > seen[symbol].mentions:
                seen[symbol] = MentionRecord(
                    symbol=symbol, asof=asof, mentions=mentions,
                    rank=rank, upvotes=upvotes, source="apewisdom",
                )

    if pages_ok == 0:
        raise MentionFetchError(
            f"all {pages} apewisdom page(s) failed for filter {filter_name!r}"
        )

    log.info("apewisdom: %d unique tickers fetched for %s", len(seen), asof)
    return list(seen.values())


def fetch_praw(cfg: dict, asof: date) -> list[MentionRecord]:
    raise NotImplementedError("TODO: optional fallback, implement last")
