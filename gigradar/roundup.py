"""Weekly Instagram roundup: carousel images + caption for the next 7 days.

  python -m gigradar.roundup                 # this week's roundup from data/events.json
  python -m gigradar.roundup --start 2026-11-16 --events path/to/events.json --out /tmp/x

Writes docs/roundup/<start-date>/ (slide-01.jpg ...), caption.txt and manifest.json,
and points docs/roundup/latest.json at it. Preview it at b1shows.com/roundup/
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import DATA_DIR, DOCS_DIR, KST, ROOT, load_watchlist
from .store import load_json, save_json

W, H = 1080, 1350  # Instagram portrait
MAX_SLIDES = 10    # Instagram carousel limit via the API
KEEP_WEEKS = 8
MIN_CONFIDENCE = 0.7

BG = (14, 13, 12)
PANEL = (11, 10, 9)
LINE = (47, 45, 41)
INK = (236, 231, 219)
INK2 = (179, 172, 158)
INK3 = (138, 132, 120)
LED = (155, 225, 93)

FONT_DIR = ROOT / "assets" / "fonts"
DOT = FONT_DIR / "VT323-Regular.ttf"
SANS = FONT_DIR / "NotoSansKR-Variable.ttf"

HASHTAGS = "#seoulindie #seoullivemusic #koreanindie #seoulgigs #hongdae #홍대 #인디공연 #라이브클럽 #b1shows"


# ---------- fonts & text helpers ----------

_font_cache: dict = {}


def dot(size: int) -> ImageFont.FreeTypeFont:
    key = ("dot", size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(str(DOT), size)
    return _font_cache[key]


def sans(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    key = ("sans", size, weight)
    if key not in _font_cache:
        f = ImageFont.truetype(str(SANS), size)
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        _font_cache[key] = f
    return _font_cache[key]


def text_w(font, s: str) -> int:
    l, _, r, _ = font.getbbox(s)
    return r - l


def wrap(font, s: str, width: int, max_lines: int) -> list[str]:
    """Word-wrap (falls back to character wrap for long Hangul runs)."""
    words = s.split()
    lines, cur = [], ""
    for w_ in words:
        trial = (cur + " " + w_).strip()
        if text_w(font, trial) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = w_
        while text_w(font, cur) > width:  # one very long word
            cut = len(cur)
            while cut > 1 and text_w(font, cur[:cut]) > width:
                cut -= 1
            lines.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and text_w(font, last + "…") > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def ellipsize(font, s: str, width: int) -> str:
    return wrap(font, s, width, 1)[0] if s else ""


def glow_text(img: Image.Image, xy, s: str, font, fill=LED, radius: int = 10, anchor="la"):
    """Draw text with a soft LED glow behind it."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, s, font=font, fill=fill + (170,), anchor=anchor)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius)))
    ImageDraw.Draw(img).text(xy, s, font=font, fill=fill, anchor=anchor)


def triangle(draw, cx, cy, size, fill=LED):
    h = size * 0.75
    draw.polygon([(cx - size / 2, cy - h / 2), (cx + size / 2, cy - h / 2), (cx, cy + h / 2)], fill=fill)


def new_slide() -> Image.Image:
    return Image.new("RGBA", (W, H), BG + (255,))


# ---------- content ----------

def headline(e: dict) -> str:
    who = ", ".join(e.get("lineup") or [])
    return e.get("title") or who or ", ".join(e.get("lineup_ko") or []) or "Live show"


def pick_week(events: list[dict], start: date) -> list[dict]:
    end = start + timedelta(days=6)
    week = []
    for e in events:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if start <= d <= end and float(e.get("confidence") or 0) >= MIN_CONFIDENCE:
            week.append(e)
    return sorted(week, key=lambda e: (e["date"], e.get("start") or e.get("doors") or "99:99", e.get("venue") or ""))


def fmt_range(start: date) -> str:
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.day}-{end.day} {start:%b}".upper()
    return f"{start.day} {start:%b} - {end.day} {end:%b}".upper()


# ---------- slides ----------

def cover_slide(start: date, n_shows: int, n_venues: int) -> Image.Image:
    img = new_slide()
    d = ImageDraw.Draw(img)
    # LED display panel, like the profile picture
    px, py, pw, ph = 160, 230, 760, 360
    d.rounded_rectangle([px, py, px + pw, py + ph], radius=36, fill=PANEL, outline=LINE, width=14)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    triangle(ImageDraw.Draw(layer), px + 200, py + ph / 2, 120, fill=LED + (170,))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(10)))
    triangle(ImageDraw.Draw(img), px + 200, py + ph / 2, 120)
    glow_text(img, (px + 290, py + ph / 2 + 14), "B1", dot(320), anchor="lm")
    d = ImageDraw.Draw(img)
    d.text((W / 2, 760), "THIS WEEK", font=dot(120), fill=INK, anchor="mm")
    glow_text(img, (W / 2, 870), fmt_range(start), dot(96), anchor="mm")
    d = ImageDraw.Draw(img)
    venues = "VENUE" if n_venues == 1 else "VENUES"
    shows = "SHOW" if n_shows == 1 else "SHOWS"
    d.text((W / 2, 980), f"{n_shows} {shows} · {n_venues} {venues}", font=dot(60), fill=INK2, anchor="mm")
    d.text((W / 2, 1110), "Indie & underground shows in Korea, in English", font=sans(34, 500), fill=INK2, anchor="mm")
    glow_text(img, (W / 2, 1240), "b1shows.com", dot(64), anchor="mm", radius=8)
    return img


TITLE_F = (52, 700)
SUB_F = 36
TIME_F = 76
X_TEXT, TEXT_W = 280, 730
LINE_H = {"title": 66, "sub": 50, "venue": 52}
BLOCK_GAP = 40
DAY_HEAD_H = 140


def show_block_height(e: dict) -> int:
    h = len(wrap(sans(*TITLE_F), headline(e), TEXT_W, 2)) * LINE_H["title"]
    if e.get("title") and e.get("lineup"):
        h += LINE_H["sub"]
    return h + LINE_H["venue"] + BLOCK_GAP


def draw_show(img: Image.Image, y: int, e: dict) -> int:
    t = e.get("start") or e.get("doors") or "TBA"
    glow_text(img, (70, y - 6), t, dot(TIME_F), radius=6)
    d = ImageDraw.Draw(img)
    for line in wrap(sans(*TITLE_F), headline(e), TEXT_W, 2):
        d.text((X_TEXT, y), line, font=sans(*TITLE_F), fill=INK)
        y += LINE_H["title"]
    if e.get("title") and e.get("lineup"):
        d.text((X_TEXT, y), ellipsize(sans(SUB_F), " · ".join(e["lineup"]), TEXT_W), font=sans(SUB_F), fill=INK2)
        y += LINE_H["sub"]
    venue = e.get("venue") or ""
    rest = " · ".join([p for p in (e.get("area"), e.get("price")) if p])
    vf, rf = sans(SUB_F, 600), sans(SUB_F)
    d.text((X_TEXT, y + 2), ellipsize(vf, venue, TEXT_W), font=vf, fill=LED)
    if rest:
        vx = X_TEXT + text_w(vf, venue)
        if vx < X_TEXT + TEXT_W - 80:
            d.text((vx, y + 2), ellipsize(rf, "  ·  " + rest, TEXT_W - (vx - X_TEXT)), font=rf, fill=INK3)
    y += LINE_H["venue"]
    return y + BLOCK_GAP


def slide_top(img: Image.Image) -> int:
    d = ImageDraw.Draw(img)
    triangle(d, 92, 86, 34)
    d.text((122, 86), "B1  THIS WEEK", font=dot(46), fill=INK3, anchor="lm")
    d.text((W - 70, 86), "b1shows.com", font=dot(46), fill=INK3, anchor="rm")
    return 160


def day_header(img: Image.Image, y: int, day: date, cont: bool) -> int:
    label = day.strftime("%a %d %b").upper() + (" (CONT.)" if cont else "")
    glow_text(img, (70, y), label, dot(96))
    d = ImageDraw.Draw(img)
    d.line([(70, y + 104), (W - 70, y + 104)], fill=LINE, width=3)
    return y + DAY_HEAD_H


def footer(img: Image.Image, more: int = 0):
    d = ImageDraw.Draw(img)
    msg = f"+{more} MORE AT B1SHOWS.COM" if more else "DETAILS + TICKETS: B1SHOWS.COM"
    d.line([(70, H - 120), (W - 70, H - 120)], fill=LINE, width=3)
    d.text((W / 2, H - 66), msg, font=dot(52), fill=INK3, anchor="mm")


def day_slides(week: list[dict]) -> list[tuple[Image.Image, int]]:
    """Flow days down each slide; start a new slide when the next block won't fit.
    A day header never sits alone at the bottom of a slide."""
    slides: list[tuple[Image.Image, int]] = []
    limit = H - 150
    img, y, count = None, 0, 0

    def new():
        nonlocal img, y, count
        if img is not None:
            slides.append((img, count))
        img, count = new_slide(), 0
        y = slide_top(img)

    by_day: dict[str, list[dict]] = {}
    for e in week:
        by_day.setdefault(e["date"], []).append(e)
    for ds, evs in by_day.items():
        day = datetime.strptime(ds, "%Y-%m-%d").date()
        if img is None or y + DAY_HEAD_H + show_block_height(evs[0]) > limit:
            new()
        y = day_header(img, y, day, False)
        for e in evs:
            if y + show_block_height(e) > limit:
                new()
                y = day_header(img, y, day, True)
            y = draw_show(img, y, e)
            count += 1
        y += 10
    if img is not None:
        slides.append((img, count))
    return slides


def end_slide() -> Image.Image:
    img = new_slide()
    d = ImageDraw.Draw(img)
    px, py, pw, ph = 290, 250, 500, 240
    d.rounded_rectangle([px, py, px + pw, py + ph], radius=28, fill=PANEL, outline=LINE, width=10)
    triangle(ImageDraw.Draw(img), px + 140, py + ph / 2, 80)
    glow_text(img, (px + 200, py + ph / 2 + 10), "B1", dot(210), anchor="lm")
    d = ImageDraw.Draw(img)
    d.text((W / 2, 640), "EVERY SHOW, EVERY DAY", font=dot(84), fill=INK, anchor="mm")
    glow_text(img, (W / 2, 760), "b1shows.com", dot(110), anchor="mm")
    d = ImageDraw.Draw(img)
    lines = [
        "Full listings, ticket links and maps",
        "Subscribe to the calendar so shows",
        "land on your phone automatically",
    ]
    for i, s in enumerate(lines):
        d.text((W / 2, 900 + i * 54), s, font=sans(36), fill=INK2, anchor="mm")
    d.text((W / 2, 1180), "Shows change. Check the venue's post before you go.", font=sans(30), fill=INK3, anchor="mm")
    return img


# ---------- caption ----------

def caption(week: list[dict], start: date, handles: dict[str, str]) -> str:
    venues = {e.get("venue") for e in week if e.get("venue")}
    head = (
        f"▼ B1 · This week in Seoul: {fmt_range(start).title()}\n\n"
        f"{len(week)} shows across {len(venues)} venues. Swipe for times, venues and prices.\n"
    )
    body, cur = [], None
    for e in week:
        if e["date"] != cur:
            cur = e["date"]
            day = datetime.strptime(cur, "%Y-%m-%d").date()
            body.append(f"\n{day:%a %d %b}".upper())
        t = e.get("start") or e.get("doors") or "TBA"
        tag = handles.get(e.get("venue_handle") or "", "")
        where = f"@{tag}" if tag else (e.get("venue") or "")
        body.append(f"• {t} {headline(e)} / {where}")
    tail = (
        "\n\nFull listings, tickets and a calendar you can subscribe to: b1shows.com (link in bio)"
        "\nShows change, so check the venue's post before heading out."
        "\nPlaying a show we missed? Tag @b1.shows.\n\n" + HASHTAGS
    )
    text = head + "\n".join(body)
    if len(text) + len(tail) > 2150:  # Instagram limit is 2200
        cut = text[: 2150 - len(tail) - 40]
        text = cut[: cut.rfind("\n")] + "\n…and more on b1shows.com"
    return text + tail


# ---------- build ----------

def build(events: list[dict], start: date, out_root: Path, handles: dict[str, str]) -> dict | None:
    week = pick_week(events, start)
    if not week:
        print(f"No shows found for the week of {start}. Nothing generated.")
        return None

    content = day_slides(week)
    room = MAX_SLIDES - 2
    kept, shown = content[:room], sum(n for _, n in content[:room])
    more = len(week) - shown
    for i, (img, _) in enumerate(kept):
        footer(img, more if (more and i == len(kept) - 1) else 0)

    slides = [cover_slide(start, len(week), len({e.get("venue") for e in week}))] + [s for s, _ in kept] + [end_slide()]

    folder = out_root / start.isoformat()
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    names = []
    for i, img in enumerate(slides, 1):
        name = f"slide-{i:02d}.jpg"  # the Instagram API only accepts JPEG
        img.convert("RGB").save(folder / name, "JPEG", quality=90, optimize=True)
        names.append(name)
    cap = caption(week, start, handles)
    (folder / "caption.txt").write_text(cap, encoding="utf-8")
    manifest = {
        "week_start": start.isoformat(),
        "generated": datetime.now(KST).isoformat(timespec="seconds"),
        "shows": len(week),
        "slides": names,
        "caption": cap,
        "published": None,
    }
    save_json(folder / "manifest.json", manifest)
    save_json(out_root / "latest.json", {"week_start": start.isoformat()})
    (out_root / "index.html").write_text(PREVIEW_HTML, encoding="utf-8")
    prune(out_root)
    print(f"Roundup for {start}: {len(week)} shows on {len(names)} slides -> {folder}")
    return manifest


def prune(out_root: Path, keep: int = KEEP_WEEKS):
    weeks = sorted(p for p in out_root.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))
    for old in weeks[:-keep]:
        shutil.rmtree(old)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="first day of the week (YYYY-MM-DD); default today in KST")
    ap.add_argument("--events", default=str(DATA_DIR / "events.json"))
    ap.add_argument("--out", default=str(DOCS_DIR / "roundup"))
    a = ap.parse_args(argv)
    start = date.fromisoformat(a.start) if a.start else datetime.now(KST).date()
    events = load_json(Path(a.events), [])
    if isinstance(events, dict):  # docs/events.json shape
        events = events.get("events", [])
    handles = {acc.handle: acc.handle for acc in load_watchlist().accounts}
    build(events, start, Path(a.out), handles)
    return 0


PREVIEW_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>b1.shows · weekly roundup draft</title>
<style>
 body{margin:0;background:#0e0d0c;color:#ece7db;font:16px/1.5 system-ui,-apple-system,"Apple SD Gothic Neo",sans-serif}
 .wrap{max-width:560px;margin:0 auto;padding:20px 16px 60px}
 h1{font:600 20px system-ui;margin:0 0 4px;color:#9be15d} p{color:#b3ac9e;margin:0 0 16px}
 img{width:100%;display:block;border-radius:8px;margin:0 0 12px;border:1px solid #2f2d29}
 textarea{width:100%;box-sizing:border-box;min-height:320px;background:#181715;color:#ece7db;border:1px solid #2f2d29;border-radius:8px;padding:12px;font:14px/1.5 ui-monospace,Menlo,monospace}
 button{font:600 15px system-ui;background:#9be15d;color:#0b0a09;border:0;border-radius:8px;padding:12px 16px;margin:8px 0 24px;cursor:pointer}
</style></head><body><div class="wrap">
<h1>Weekly roundup draft</h1><p id="meta">Loading…</p>
<div id="slides"></div>
<h1>Caption</h1>
<textarea id="cap"></textarea>
<button id="copy" type="button">Copy caption</button>
<p>To post by hand: long-press each image to save it, then make a carousel on Instagram and paste the caption.
Or run the "Publish weekly roundup" workflow on GitHub to post it automatically.</p>
</div>
<script>
fetch("latest.json",{cache:"no-cache"}).then(r=>r.json()).then(l=>fetch(l.week_start+"/manifest.json",{cache:"no-cache"}).then(r=>r.json()).then(m=>{
 document.getElementById("meta").textContent=`Week of ${m.week_start} · ${m.shows} shows · ${m.slides.length} slides`+(m.published?` · posted ${m.published}`:" · not posted yet");
 const box=document.getElementById("slides");
 m.slides.forEach(s=>{const i=document.createElement("img");i.src=m.week_start+"/"+s;i.alt="Roundup slide";box.append(i);});
 document.getElementById("cap").value=m.caption;
})).catch(()=>{document.getElementById("meta").textContent="No roundup yet.";});
document.getElementById("copy").onclick=()=>{const t=document.getElementById("cap");t.select();
 (navigator.clipboard?navigator.clipboard.writeText(t.value):Promise.reject()).then(()=>{document.getElementById("copy").textContent="Copied!"}).catch(()=>document.execCommand("copy"));};
</script></body></html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
