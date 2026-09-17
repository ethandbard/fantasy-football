"""
Posts agent output to the #team-agent webhook. Long briefs are split into
embed-sized chunks at paragraph boundaries. Without a webhook configured,
everything is logged instead, which is what local dry runs want.
"""
import logging

import requests

logger = logging.getLogger(__name__)

COLORS = {
    "brief": 0x9B59B6,
    "pregame": 0x3498DB,
    "trade": 0xE91E63,
    "alert": 0xE74C3C,
    "info": 0x95A5A6,
    "ask": 0xF1C40F,
    "answer": 0x1ABC9C,
}
EMBED_LIMIT = 3900


def chunk(body, limit=EMBED_LIMIT):
    """Split markdown into pieces under the embed limit, preferring blank lines."""
    body = (body or "").strip()
    if not body:
        return []
    pieces = []
    while len(body) > limit:
        cut = body.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = body.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        pieces.append(body[:cut].rstrip())
        body = body[cut:].lstrip()
    pieces.append(body)
    return pieces


def post(cfg, title, body, kind="brief", wait=False):
    """Post a titled message. Returns the first message id when wait=True and a webhook exists."""
    pieces = chunk(body) or ["(empty)"]
    if not cfg.webhook_url:
        logger.info("DISCORD (no webhook) %s\n%s", title, body)
        return None
    first_id = None
    for i, piece in enumerate(pieces):
        embed = {
            "title": title if i == 0 else f"{title} ({i + 1}/{len(pieces)})",
            "description": piece,
            "color": COLORS.get(kind, COLORS["info"]),
        }
        url = cfg.webhook_url + ("?wait=true" if wait else "")
        try:
            r = requests.post(url, json={"embeds": [embed]}, timeout=20)
        except requests.RequestException as e:
            logger.error("Discord post failed: %s", e)
            return first_id
        if r.status_code not in (200, 204):
            logger.error("Discord webhook returned %s: %s", r.status_code, r.text[:200])
            return first_id
        if wait and first_id is None and r.status_code == 200:
            try:
                first_id = r.json().get("id")
            except ValueError:
                pass
    return first_id


def line(cfg, text, kind="info"):
    if not cfg.webhook_url:
        logger.info("DISCORD (no webhook) %s", text)
        return
    try:
        requests.post(cfg.webhook_url, json={"content": text[:1990]}, timeout=20)
    except requests.RequestException as e:
        logger.error("Discord line failed: %s", e)
