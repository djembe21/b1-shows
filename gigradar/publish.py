"""Write the public outputs: docs/events.json (read by the site) and
docs/calendar.ics (a feed people can subscribe to in Google/Apple Calendar)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import DOCS_DIR, KST
from .store import save_json

SITE_NAME = "b1.shows"


def public_events(events: list[dict], today: datetime) -> list[dict]:
    t = today.date()
    up = [e for e in events if datetime.strptime(e["date"], "%Y-%m-%d").date() >= t]
    return sorted(up, key=lambda e: (e["date"], e.get("start") or e.get("doors") or "99:99", e["venue"] or ""))


def write_outputs(events: list[dict], now: datetime, docs_dir=DOCS_DIR) -> list[dict]:
    pub = public_events(events, now)
    save_json(
        docs_dir / "events.json",
        {"generated": now.isoformat(), "count": len(pub), "events": pub},
    )
    (docs_dir / "calendar.ics").write_text(to_ics(pub, now), encoding="utf-8", newline="")
    return pub


# ---------- iCalendar ----------

def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets are folded."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def event_summary(e: dict) -> str:
    who = ", ".join(e["lineup"][:4]) + (" +more" if len(e["lineup"]) > 4 else "")
    head = e.get("title") or who or "Live show"
    if e.get("title") and who:
        head = f"{e['title']} ({who})"
    return f"{head} @ {e['venue']}"


def event_description(e: dict) -> str:
    lines = []
    if e["lineup"]:
        lines.append("Lineup: " + ", ".join(e["lineup"]))
    if e.get("lineup_ko"):
        lines.append("라인업: " + ", ".join(e["lineup_ko"]))
    if e.get("doors"):
        lines.append(f"Doors: {e['doors']}")
    if e.get("price"):
        lines.append(f"Price: {e['price']}")
    if e.get("notes"):
        lines.append(e["notes"])
    if e.get("ticket_url"):
        lines.append(f"Tickets: {e['ticket_url']}")
    for s in e["sources"]:
        lines.append(f"Source: {s['url']}")
    lines.append(f"Listed by {SITE_NAME}. Always check the original post before you go.")
    return "\n".join(lines)


def to_ics(events: list[dict], now: datetime) -> str:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{SITE_NAME}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{SITE_NAME}",
        "X-WR-TIMEZONE:Asia/Seoul",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Seoul",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0900",
        "TZOFFSETTO:+0900",
        "TZNAME:KST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for e in events:
        d = datetime.strptime(e["date"], "%Y-%m-%d")
        t = e.get("start") or e.get("doors")
        lines += ["BEGIN:VEVENT", f"UID:{e['id']}@b1.shows", f"DTSTAMP:{stamp}"]
        if t:
            hh, mm = (int(x) for x in t.split(":")[:2])
            start = d.replace(hour=hh % 24, minute=mm)
            end = start + timedelta(hours=3)
            lines += [
                f"DTSTART;TZID=Asia/Seoul:{start:%Y%m%dT%H%M00}",
                f"DTEND;TZID=Asia/Seoul:{end:%Y%m%dT%H%M00}",
            ]
        else:
            lines += [
                f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
                f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
            ]
        loc = e["venue"] + (f" ({e['venue_ko']})" if e.get("venue_ko") else "")
        lines += [
            f"SUMMARY:{_esc(event_summary(e))}",
            f"LOCATION:{_esc(loc)}",
            f"DESCRIPTION:{_esc(event_description(e))}",
            f"URL:{e['sources'][0]['url']}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
