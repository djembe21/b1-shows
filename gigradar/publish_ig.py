"""Post the latest weekly roundup to Instagram as a carousel.

  python -m gigradar.publish_ig            # post docs/roundup/<latest>/
  python -m gigradar.publish_ig --dry-run  # show what would be posted

Needs the `instagram_content_publish` permission on IG_ACCESS_TOKEN, and the
slides must already be live on the website (Instagram downloads them by URL).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

import requests

from .config import DOCS_DIR, KST
from .instagram import GRAPH
from .store import load_json, save_json

SITE = os.environ.get("SITE_URL", "https://b1shows.com").rstrip("/")


def _post(path: str, token: str, **params) -> dict:
    r = requests.post(f"{GRAPH}/{path}", data={**params, "access_token": token}, timeout=60)
    data = r.json()
    if "error" in data:
        raise SystemExit(f"Instagram error on {path}: {data['error'].get('message')}")
    return data


def _wait_ready(container_id: str, token: str, tries: int = 30):
    for _ in range(tries):
        r = requests.get(f"{GRAPH}/{container_id}", params={"fields": "status_code", "access_token": token}, timeout=30).json()
        status = r.get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise SystemExit(f"Instagram couldn't process {container_id}: {status}")
        time.sleep(5)
    raise SystemExit(f"Timed out waiting for {container_id}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="post even if already posted")
    a = ap.parse_args(argv)

    root = DOCS_DIR / "roundup"
    latest = load_json(root / "latest.json", None)
    if not latest:
        print("No roundup to post.")
        return 1
    week = latest["week_start"]
    manifest_path = root / week / "manifest.json"
    m = load_json(manifest_path, None)
    if m.get("published") and not a.force:
        print(f"Week of {week} was already posted on {m['published']}. Use --force to post again.")
        return 0

    urls = [f"{SITE}/roundup/{week}/{name}" for name in m["slides"]]
    print(f"Posting week of {week}: {len(urls)} slides")
    for u in urls:
        print("  ", u)
    if a.dry_run:
        print("\n" + m["caption"])
        return 0

    # The slides must be reachable before Instagram can fetch them.
    for u in urls:
        if requests.head(u, timeout=30).status_code != 200:
            raise SystemExit(f"Slide not live yet: {u}. Wait for the site to update, then run again.")

    ig, token = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    children = []
    for u in urls:
        c = _post(f"{ig}/media", token, image_url=u, is_carousel_item="true")
        children.append(c["id"])
    for cid in children:
        _wait_ready(cid, token)
    carousel = _post(f"{ig}/media", token, media_type="CAROUSEL", children=",".join(children), caption=m["caption"])
    _wait_ready(carousel["id"], token)
    post = _post(f"{ig}/media_publish", token, creation_id=carousel["id"])

    m["published"] = datetime.now(KST).isoformat(timespec="minutes")
    m["instagram_id"] = post.get("id")
    save_json(manifest_path, m)
    print(f"Posted! Instagram media id {post.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
