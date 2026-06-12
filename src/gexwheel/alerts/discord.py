"""Discord webhook delivery.

format_card(card: AlertCard) -> dict
  One Discord embed dict. Target layout:
  {
    "title": f"{card.symbol}  ·  {card.alert_type.replace('_',' ').title()}",
    "color": 0x2ECC71 if card.regime == "positive" else 0xE74C3C,
    "fields": [
      {"name": "Spot",       "value": f"${card.spot:.2f}",                 "inline": True},
      {"name": "Put wall",   "value": f"${card.put_wall:.2f}" or "—",      "inline": True},
      {"name": "Call wall",  "value": ...,                                  "inline": True},
      {"name": "Zero gamma", "value": ...,                                  "inline": True},
      {"name": "IV rank",    "value": f"{card.iv_rank:.0f}" or "n/a",       "inline": True},
      {"name": "VRP",        "value": f"{card.vrp*100:+.1f} vol pts",       "inline": True},
      {"name": "Suggested",  "value": card.suggested_entry,                 "inline": False},
    ],
    "footer": {"text": f"score {card.score:.0f}/100 · {card.notes} · not financial advice"},
  }
  Every numeric field must tolerate None -> "n/a"/"—" (early weeks will have
  None iv_rank until history accrues).

send_alerts(cards, cfg) -> list[AlertCard]
  * sort by score desc, truncate to cfg['discord']['max_alerts_per_run']
  * POST cfg webhook_url, json={"username": cfg username,
      "embeds": [<=10 embeds per message - chunk if needed]}
  * requests.post timeout=15; treat 2xx as success; on 429 read
    retry_after from JSON body, sleep, retry once; other errors: log and
    continue (alerting must never crash the pipeline).
  * Return the cards whose embeds were actually posted; caller writes
    alerts rows with sent_at set only for those (None on failure -> retried
    next run by the dedup rules).

test_webhook(cfg) -> bool
  Sends a single plain {"content": "gexwheel webhook OK"} message. Wired to
  the `test-discord` CLI subcommand for first-time setup.
"""
from __future__ import annotations

import logging
import time

import requests

from ..models import AlertCard

log = logging.getLogger(__name__)

_CHUNK = 10   # Discord max embeds per message


def _fmt_val(v, fmt=".2f", prefix="$", fallback="n/a") -> str:
    if v is None:
        return fallback
    try:
        return f"{prefix}{v:{fmt}}"
    except (TypeError, ValueError):
        return fallback


def format_card(card: AlertCard) -> dict:
    """Build one Discord embed dict from an AlertCard."""
    color = 0x2ECC71 if card.regime == "positive" else 0xE74C3C

    vrp_str = f"{card.vrp * 100:+.1f} vol pts" if card.vrp is not None else "n/a"
    ivr_str = f"{card.iv_rank:.0f}" if card.iv_rank is not None else "n/a (building history)"

    return {
        "title": f"{card.symbol}  ·  {card.alert_type.replace('_', ' ').title()}",
        "color": color,
        "fields": [
            {"name": "Spot",        "value": _fmt_val(card.spot),       "inline": True},
            {"name": "Put wall",    "value": _fmt_val(card.put_wall, fallback="—"),   "inline": True},
            {"name": "Call wall",   "value": _fmt_val(card.call_wall, fallback="—"),  "inline": True},
            {"name": "Zero gamma",  "value": _fmt_val(card.zero_gamma, fallback="—"), "inline": True},
            {"name": "IV rank",     "value": ivr_str,                   "inline": True},
            {"name": "VRP",         "value": vrp_str,                   "inline": True},
            {"name": "Suggested",   "value": card.suggested_entry,      "inline": False},
        ],
        "footer": {
            "text": (
                f"score {card.score:.0f}/100"
                + (f"  ·  {card.notes}" if card.notes else "")
                + "  ·  not financial advice"
            )
        },
    }


def _post(webhook_url: str, payload: dict, timeout: int = 15) -> bool:
    """POST to webhook; handles 429 rate-limit with one retry. Returns True on success."""
    try:
        resp = requests.post(webhook_url, json=payload, timeout=timeout)
        if resp.status_code == 429:
            retry_after = 1.0
            try:
                retry_after = float(resp.json().get("retry_after", 1.0))
            except Exception:
                pass
            log.warning("discord 429, sleeping %.1fs", retry_after)
            time.sleep(retry_after)
            resp = requests.post(webhook_url, json=payload, timeout=timeout)
        if resp.ok:
            return True
        log.error("discord webhook returned %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        log.error("discord send failed: %s", exc)
        return False


def send_alerts(cards: list[AlertCard], cfg: dict) -> list[AlertCard]:
    """Sort by score desc, chunk into Discord messages, return cards actually posted."""
    d = cfg["discord"]
    webhook_url = d["webhook_url"]
    max_cards = d.get("max_alerts_per_run", 8)
    username = d.get("username", "GEX Wheel")

    top = sorted(cards, key=lambda c: c.score, reverse=True)[:max_cards]
    sent_cards: list[AlertCard] = []

    for i in range(0, len(top), _CHUNK):
        chunk_cards = top[i: i + _CHUNK]
        payload = {"username": username, "embeds": [format_card(c) for c in chunk_cards]}
        if _post(webhook_url, payload):
            sent_cards.extend(chunk_cards)
        else:
            log.error(
                "chunk %d-%d failed to send (%s)",
                i, i + len(chunk_cards),
                ", ".join(c.symbol for c in chunk_cards),
            )

    return sent_cards


def test_webhook(cfg: dict) -> bool:
    """Send a plain text message to verify the webhook is wired up."""
    url = cfg["discord"]["webhook_url"]
    username = cfg["discord"].get("username", "GEX Wheel")
    return _post(url, {"username": username, "content": "gexwheel webhook OK"})
