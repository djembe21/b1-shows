"""Turn an Instagram post (caption + poster images) into structured events
using Claude. Posters are usually Korean; Claude reads them and returns
English-first fields with Korean originals kept alongside."""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime

import requests

from .config import KST
from .instagram import Post

# Sonnet reads dense Korean posters reliably. For a cheaper run, set
# GIGRADAR_MODEL to claude-haiku-5-5 in the workflow.
DEFAULT_MODEL = os.environ.get("GIGRADAR_MODEL", "claude-sonnet-5-5")
MAX_IMAGES = int(os.environ.get("GIGRADAR_MAX_IMAGES", "2"))
MAX_SIDE = 1000  # px; image tokens scale with pixel count

SYSTEM = """You extract live music events from Instagram posts by Seoul/Korea venues, bands and promoters.
The audience is foreigners living in Korea who may not read Korean, so give English values, keeping Korean originals where noted.

Rules:
- Only record concrete upcoming LIVE MUSIC with a specific date: bands, solo artists, live jazz/acoustic sets, and club nights with named DJs. Ignore recaps of past shows, merch, album announcements without a show date, general venue promos, and thank-you posts.
- Skip events that are not live music: stand-up comedy, talks, workshops and classes, film screenings, markets, fan meetings.
- Skip playlist, record or request nights at music bars and pubs (e.g. "the greatest music of [artist]", "90s rock special", "sad music club", themed listening nights), even if staff or a resident selector plays the records. Keep a DJ night only when the post bills a specific named guest DJ or DJ lineup, and tag it "dj".
- Posters often omit the year. Use the post date to infer it: choose the nearest date on or after (post date - 7 days).
- One post can list several shows (a monthly schedule, a tour). Record each date/venue as its own event.
- Times are KST in 24h "HH:MM". Leave a field null rather than guessing.
- price: keep the original currency text, e.g. "Advance 25,000 / Door 30,000 KRW". "Free" if free.
- lineup: performer names as written in Latin script if the poster has it, otherwise a romanization; put the Hangul in lineup_ko in the same order.
- venue: the venue name as written; venue_ko for Hangul if shown.
- genres: up to 3 lowercase tags from: rock, indie, punk, hardcore, metal, folk, jazz, electronic, hip-hop, r&b, pop, experimental, shoegaze, psych, acoustic, dj, other.
- confidence: 0-1, how sure you are the date and venue are right.
Always call the record_events tool, with an empty list if the post is not an upcoming show."""

EVENT_TOOL = {
    "name": "record_events",
    "description": "Record the upcoming live events announced in this post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": ["string", "null"], "description": "Show/event title in English if any"},
                        "title_ko": {"type": ["string", "null"]},
                        "date": {"type": "string", "description": "YYYY-MM-DD"},
                        "doors": {"type": ["string", "null"], "description": "HH:MM"},
                        "start": {"type": ["string", "null"], "description": "HH:MM"},
                        "venue": {"type": "string"},
                        "venue_ko": {"type": ["string", "null"]},
                        "city": {"type": ["string", "null"], "description": "e.g. Seoul, Busan"},
                        "lineup": {"type": "array", "items": {"type": "string"}},
                        "lineup_ko": {"type": "array", "items": {"type": "string"}},
                        "price": {"type": ["string", "null"]},
                        "ticket_url": {"type": ["string", "null"]},
                        "genres": {"type": "array", "items": {"type": "string"}},
                        "notes": {"type": ["string", "null"], "description": "Short English note: age limit, sold out, foreigner booking info, etc."},
                        "confidence": {"type": "number"},
                    },
                    "required": ["date", "venue", "lineup", "confidence"],
                },
            }
        },
        "required": ["events"],
    },
}


class ClaudeExtractor:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        import anthropic  # imported lazily so dry runs don't need it

        self.client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self.model = model

    def extract(self, post: Post, account_label: str) -> list[dict]:
        posted_kst = post.timestamp.astimezone(KST)
        text = (
            f"Account: @{post.handle} ({account_label})\n"
            f"Posted: {posted_kst:%Y-%m-%d %A} KST\n"
            f"Caption:\n{post.caption or '(no caption)'}"
        )
        content: list[dict] = []
        for url in post.image_urls[:MAX_IMAGES]:
            try:
                content.append(_image_block(url))
            except Exception as e:  # a dead CDN link shouldn't lose the caption
                text += f"\n(poster image unavailable: {type(e).__name__})"
        content.append({"type": "text", "text": text})

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM,
            tools=[EVENT_TOOL],
            # Newer models don't accept a forced tool_choice; the system
            # prompt tells Claude to always call record_events.
            messages=[{"role": "user", "content": content}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "record_events":
                return list((block.input or {}).get("events") or [])
        # Fallback: Claude answered in text. Pull out any JSON it wrote.
        text_out = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        return _events_from_text(text_out)


def _events_from_text(text: str) -> list[dict]:
    """Best effort: find a JSON object/array in a text reply."""
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start == -1 or end <= start:
            continue
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            continue
        if isinstance(data, dict):
            data = data.get("events", [])
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]
    return []


def _image_block(url: str) -> dict:
    """Download a poster, shrink it, and inline it as JPEG. Instagram CDN URLs
    are short-lived, and smaller images cost far fewer tokens."""
    from io import BytesIO

    from PIL import Image

    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (b1.shows listings bot)"})
    r.raise_for_status()
    img = Image.open(BytesIO(r.content))
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()},
    }


class FixtureExtractor:
    """Stands in for Claude in tests/dry runs: returns canned events keyed by post ID."""

    def __init__(self, fixture_path):
        self.data = json.loads(open(fixture_path, encoding="utf-8").read())

    def extract(self, post: Post, account_label: str) -> list[dict]:
        return list(self.data.get(post.id, []))


def is_valid_event(e: dict, today: datetime) -> bool:
    try:
        d = datetime.strptime(e["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError, TypeError):
        return False
    if not e.get("venue"):
        return False
    if float(e.get("confidence") or 0) < 0.4:
        return False
    # sanity: within the next ~13 months, not more than a day in the past
    delta = (d - today.date()).days
    return -1 <= delta <= 400
