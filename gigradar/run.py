"""Daily pipeline: fetch new posts -> extract events -> merge -> publish.

Usage:
  python -m gigradar.run                 # real run (needs env vars below)
  python -m gigradar.run --check         # just verify the Instagram token works
  python -m gigradar.run --dry-run       # offline run on tests/fixtures (no API calls)

Env vars for a real run:
  IG_USER_ID        your Instagram professional account's IG user ID
  IG_ACCESS_TOKEN   token for the Instagram API with Facebook Login
  ANTHROPIC_API_KEY Claude API key
  GIGRADAR_MODEL    optional, defaults to claude-sonnet-5-5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, DOCS_DIR, KST, ROOT, VenueIndex, load_watchlist
from .extract import is_valid_event
from .instagram import InstagramClient, InstagramError, Post, filter_new
from .publish import write_outputs
from .store import load_json, normalize, prune_past, save_json, upsert

SEEN_PATH = DATA_DIR / "seen_posts.json"
EVENTS_PATH = DATA_DIR / "events.json"
REPORT_PATH = DATA_DIR / "fetch_report.json"
FIXTURES = ROOT / "tests" / "fixtures"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="use fixtures instead of live APIs")
    ap.add_argument("--check", action="store_true", help="verify Instagram credentials and exit")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--docs-dir", default=None)
    args = ap.parse_args(argv)

    data_dir = DATA_DIR if args.data_dir is None else Path(args.data_dir)
    docs_dir = DOCS_DIR if args.docs_dir is None else Path(args.docs_dir)
    seen_path, events_path, report_path = (data_dir / p.name for p in (SEEN_PATH, EVENTS_PATH, REPORT_PATH))

    now = datetime.now(timezone.utc).astimezone(KST)
    if os.environ.get("GIGRADAR_TODAY"):  # pin "today" for tests
        now = datetime.fromisoformat(os.environ["GIGRADAR_TODAY"]).replace(tzinfo=KST, hour=9)
    now_iso = now.isoformat(timespec="seconds")
    wl = load_watchlist(FIXTURES / "watchlist.yaml" if args.dry_run else None)
    accounts = {a.handle.lower(): a for a in wl.accounts}
    venues = VenueIndex(wl.venues)

    if args.dry_run:
        from .extract import FixtureExtractor

        posts_by_handle = _fixture_posts()
        extractor = FixtureExtractor(FIXTURES / "extractions.json")
        fetch = lambda h: posts_by_handle.get(h, [])  # noqa: E731
        max_age = 3650
    else:
        ig = InstagramClient(os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"])
        if args.check:
            print(json.dumps(ig.check_token(), indent=2))
            return 0
        from .extract import ClaudeExtractor

        extractor = ClaudeExtractor()
        fetch = lambda h: ig.recent_posts(h, wl.posts_per_account)  # noqa: E731
        max_age = wl.max_post_age_days

    seen: dict[str, str] = load_json(seen_path, {})
    events: list[dict] = load_json(events_path, [])
    report = {"run": now_iso, "accounts": {}, "posts_checked": 0, "added": 0, "merged": 0, "errors": 0}
    # "errors" counts accounts that failed plus posts that couldn't be read

    for handle, acct in accounts.items():
        try:
            posts = fetch(acct.handle)
        except InstagramError as e:
            report["accounts"][handle] = {"status": "error", "error": str(e)}
            report["errors"] += 1
            print(f"[warn] @{handle}: {e}", file=sys.stderr)
            continue

        new_posts = filter_new(posts, set(seen), max_age)
        stats = {"status": "ok", "posts": len(posts), "new": len(new_posts), "events": 0}
        label = f"{acct.kind}: {acct.name or acct.handle}"
        for post in new_posts:
            report["posts_checked"] += 1
            try:
                raw_events = extractor.extract(post, label)
            except Exception as e:  # one bad post shouldn't sink the run
                print(f"[warn] extract failed for {post.permalink}: {e}", file=sys.stderr)
                report["errors"] += 1
                stats.setdefault("extract_errors", [])
                if len(stats["extract_errors"]) < 2:
                    stats["extract_errors"].append(f"{type(e).__name__}: {str(e)[:300]}")
                continue  # not marked seen, so it's retried next run
            for raw in raw_events:
                if not is_valid_event(raw, now):
                    continue
                ev = normalize(raw, post, acct, venues)
                report[upsert(events, ev, now_iso)] += 1
                stats["events"] += 1
            seen[post.id] = now_iso
        report["accounts"][handle] = stats

    events = prune_past(events, now)
    seen = _trim_seen(seen, keep=5000)
    save_json(events_path, events)
    save_json(seen_path, seen)
    save_json(report_path, report)
    pub = write_outputs(events, now, docs_dir)

    print(
        f"checked {report['posts_checked']} new posts from {len(accounts)} accounts: "
        f"{report['added']} new shows, {report['merged']} merged, {report['errors']} errors. "
        f"{len(pub)} upcoming shows published."
    )
    # Fail loudly (GitHub emails you) if every account errored - usually an expired token.
    if accounts and report["errors"] and all(a.get("status") == "error" for a in report["accounts"].values()):
        print("[error] every account failed - check IG_ACCESS_TOKEN", file=sys.stderr)
        return 1
    return 0


def _fixture_posts() -> dict[str, list[Post]]:
    raw = json.loads((FIXTURES / "posts.json").read_text(encoding="utf-8"))
    out: dict[str, list[Post]] = {}
    for d in raw:
        out.setdefault(d["handle"], []).append(Post.from_dict(d))
    return out


def _trim_seen(seen: dict[str, str], keep: int) -> dict[str, str]:
    if len(seen) <= keep:
        return seen
    return dict(sorted(seen.items(), key=lambda kv: kv[1])[-keep:])


if __name__ == "__main__":
    sys.exit(main())
