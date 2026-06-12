r"""Reddit mention counts from ApeWisdom and optional PRAW fallback.

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
from collections import Counter
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from .. import db
from ..models import MentionRecord

log = logging.getLogger(__name__)

APEWISDOM_URL = "https://apewisdom.io/api/v1.0/filter/{flt}/page/{page}/"
_TICKER_RE = re.compile(r'^[A-Z]{1,5}$')
_CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")
_BARE_RE = re.compile(r"(?<!\$)\b[A-Z]{1,5}\b")


class MentionFetchError(RuntimeError):
    pass


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
                upvotes = 0   # NOTE: same fallback as mentions; bad data == no data

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
    """Fetch noisy Reddit mention counts through PRAW for known tickers and cashtags."""
    try:
        import praw
    except Exception as exc:
        raise MentionFetchError(f"praw import failed: {exc}") from exc

    known_symbols = _known_symbols(cfg)
    praw_cfg = cfg["reddit"].get("praw", {})
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    counts: Counter[str] = Counter()

    posts = _praw_posts_with_retry(praw, praw_cfg)

    try:
        for post in posts:
            if _created_date(getattr(post, "created_utc", None), tz) != asof:
                continue
            _count_text(counts, f"{getattr(post, 'title', '')} {getattr(post, 'selftext', '')}", known_symbols)
            comments = getattr(post, "comments", None)
            if comments is None:
                continue
            try:
                comments.replace_more(limit=0)
                for comment in comments.list():
                    if _created_date(getattr(comment, "created_utc", None), tz) == asof:
                        _count_text(counts, getattr(comment, "body", ""), known_symbols)
            except Exception as exc:
                log.debug("praw comments skipped: %s", exc)
    except Exception as exc:
        raise MentionFetchError(f"praw fetch failed: {exc}") from exc

    records = [
        MentionRecord(symbol=symbol, asof=asof, mentions=count, source="praw")
        for symbol, count in counts.items()
        if count > 0
    ]
    log.info("praw: %d unique tickers fetched for %s", len(records), asof)
    return records


def _praw_posts_with_retry(praw_module, praw_cfg: dict, retries: int = 3) -> list:
    for attempt in range(retries):
        try:
            reddit = praw_module.Reddit(
                client_id=praw_cfg.get("client_id"),
                client_secret=praw_cfg.get("client_secret"),
                user_agent=praw_cfg.get("user_agent", "gexwheel/0.1"),
                requestor_kwargs={"timeout": 15},
            )
            return list(reddit.subreddit("wallstreetbets").new(limit=500))
        except Exception as exc:
            if attempt == retries - 1:
                raise MentionFetchError(f"praw setup/listing failed: {exc}") from exc
            sleep = 2 ** attempt
            log.warning("praw listing attempt %d failed (%s), retrying in %ds", attempt + 1, exc, sleep)
            time.sleep(sleep)
    return []


def _known_symbols(cfg: dict) -> set[str]:
    db_path = cfg.get("db_path")
    if not db_path:
        return set()
    conn = db.connect(db_path)
    try:
        rows = conn.execute("SELECT symbol FROM tickers WHERE excluded=0").fetchall()
        return {r["symbol"].upper() for r in rows if _TICKER_RE.match(r["symbol"].upper())}
    finally:
        conn.close()


def _created_date(created_utc: float | None, tz: ZoneInfo) -> date | None:
    if created_utc is None:
        return None
    return datetime.fromtimestamp(created_utc, tz).date()


def _count_text(counts: Counter[str], text: str, known_symbols: set[str]) -> None:
    for raw in _CASHTAG_RE.findall(text or ""):
        symbol = raw.upper().strip()
        if _TICKER_RE.match(symbol):
            counts[symbol] += 1
    for raw in _BARE_RE.findall(text or ""):
        symbol = raw.upper().strip()
        if symbol in known_symbols:
            counts[symbol] += 1
