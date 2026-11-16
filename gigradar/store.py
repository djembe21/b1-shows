"""Event storage, venue normalization and de-duplication.

The same show is usually posted by the venue, each band, and sometimes a
promoter. We merge them into one listing that links back to every source."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from .config import KST, Account, VenueIndex


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _slug(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", (s or "").lower())


def normalize(raw: dict, source_post, source_account: Account, venues: VenueIndex) -> dict:
    """Clean one extracted event and attach venue + source info."""
    v = venues.lookup(raw.get("venue", ""), raw.get("venue_ko") or "")
    # A venue account posting its own show: trust the account as the venue.
    if v is None and source_account.kind == "venue":
        posted_venue = _slug(raw.get("venue", "")) + _slug(raw.get("venue_ko") or "")
        own = [s for s in (_slug(source_account.name), _slug(source_account.name_ko)) if s]
        if not posted_venue or any(s in posted_venue for s in own):
            v = source_account

    lineup = [x.strip() for x in raw.get("lineup") or [] if x and x.strip()]
    lineup_ko = [x.strip() for x in raw.get("lineup_ko") or [] if x and x.strip()]
    ev = {
        "date": raw["date"],
        "doors": raw.get("doors"),
        "start": raw.get("start"),
        "title": raw.get("title"),
        "title_ko": raw.get("title_ko"),
        "venue": v.name if v else raw.get("venue"),
        "venue_ko": (v.name_ko if v and v.name_ko else raw.get("venue_ko")),
        "venue_handle": v.handle if v else None,
        "area": v.area if v else None,
        "city": raw.get("city") or "Seoul",
        "lineup": lineup,
        "lineup_ko": lineup_ko,
        "price": raw.get("price"),
        "ticket_url": raw.get("ticket_url"),
        "genres": sorted({g.lower() for g in raw.get("genres") or []})[:3],
        "notes": raw.get("notes"),
        "confidence": round(float(raw.get("confidence") or 0), 2),
        "sources": [
            {
                "handle": source_post.handle,
                "url": source_post.permalink,
                "posted": source_post.timestamp.astimezone(KST).isoformat(),
            }
        ],
    }
    return ev


def _venue_key(e: dict) -> str:
    return e.get("venue_handle") or _slug(e.get("venue_ko") or "") or _slug(e.get("venue") or "")


def _same_show(a: dict, b: dict) -> bool:
    if a["date"] != b["date"]:
        return False
    # Same date + same distinctive title = same show, even when one post
    # didn't name the venue (e.g. a promoter's "Live Club Day #85" post).
    ta_, tb_ = _slug(a.get("title") or ""), _slug(b.get("title") or "")
    if len(ta_) >= 6 and ta_ == tb_ and (not a.get("venue_handle") or not b.get("venue_handle") or _venue_key(a) == _venue_key(b)):
        return True
    if _venue_key(a) != _venue_key(b):
        return False
    ta, tb = a.get("start"), b.get("start")
    if ta and tb and ta != tb:
        # Same venue, same day, different times: separate shows unless lineups overlap
        la = {_slug(x) for x in a["lineup"] + a["lineup_ko"]}
        lb = {_slug(x) for x in b["lineup"] + b["lineup_ko"]}
        return bool(la & lb)
    return True


def _merge(into: dict, new: dict) -> dict:
    # Prefer a venue we know over one we don't.
    if not into.get("venue_handle") and new.get("venue_handle"):
        for k in ("venue", "venue_ko", "venue_handle", "area"):
            into[k] = new.get(k)
    for k in ("doors", "start", "title", "title_ko", "venue_ko", "price", "ticket_url", "notes", "area", "venue_handle"):
        if not into.get(k) and new.get(k):
            into[k] = new[k]
    # Compare against both name lists so "소음주의보" in one post's lineup
    # doesn't duplicate "Soeum Juuibo"/"소음주의보" from another.
    seen = {_slug(x) for x in into["lineup"] + into["lineup_ko"]}
    for k in ("lineup", "lineup_ko"):
        for x in new[k]:
            if _slug(x) not in seen:
                # Hangul-only names belong in lineup_ko
                dest = "lineup_ko" if k == "lineup" and re.fullmatch(r"[가-힣\s]+", x) else k
                into[dest].append(x)
                seen.add(_slug(x))
    into["genres"] = sorted(set(into["genres"]) | set(new["genres"]))[:3]
    into["confidence"] = max(into["confidence"], new["confidence"])
    urls = {s["url"] for s in into["sources"]}
    into["sources"] += [s for s in new["sources"] if s["url"] not in urls]
    return into


def event_id(e: dict) -> str:
    key = f"{e['date']}|{_venue_key(e)}|{e.get('start') or ''}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def upsert(events: list[dict], new: dict, now_iso: str) -> str:
    """Merge `new` into the list. Returns 'added' or 'merged'."""
    for e in events:
        if _same_show(e, new):
            _merge(e, new)
            e["updated"] = now_iso
            return "merged"
    new["id"] = event_id(new)
    new["first_seen"] = now_iso
    new["updated"] = now_iso
    events.append(new)
    return "added"


def prune_past(events: list[dict], today: datetime, keep_days: int = 2) -> list[dict]:
    t = today.date()
    return [e for e in events if (datetime.strptime(e["date"], "%Y-%m-%d").date() - t).days >= -keep_days]
