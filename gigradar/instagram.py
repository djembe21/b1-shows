"""Fetch recent posts from public Business/Creator accounts via Meta's
Business Discovery API (Instagram API with Facebook Login).

Docs: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/business-discovery
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

GRAPH_VERSION = "v26.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"

_MEDIA_FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp"
_CHILD_FIELDS = "media_type,media_url,thumbnail_url"


@dataclass
class Post:
    id: str
    handle: str
    caption: str
    permalink: str
    timestamp: datetime
    image_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "handle": self.handle,
            "caption": self.caption,
            "permalink": self.permalink,
            "timestamp": self.timestamp.isoformat(),
            "image_urls": self.image_urls,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Post":
        return cls(
            id=d["id"],
            handle=d["handle"],
            caption=d.get("caption") or "",
            permalink=d.get("permalink") or "",
            timestamp=datetime.fromisoformat(d["timestamp"].replace("+0000", "+00:00")),
            image_urls=list(d.get("image_urls") or []),
        )


class InstagramError(Exception):
    pass


class InstagramClient:
    def __init__(self, ig_user_id: str, access_token: str, session: requests.Session | None = None):
        self.ig_user_id = ig_user_id
        self.token = access_token
        self.http = session or requests.Session()

    def _get(self, fields: str) -> dict:
        for attempt in range(3):
            r = self.http.get(
                f"{GRAPH}/{self.ig_user_id}",
                params={"fields": fields, "access_token": self.token},
                timeout=30,
            )
            if r.status_code == 429 or (r.status_code >= 500 and attempt < 2):
                time.sleep(5 * (attempt + 1))
                continue
            data = r.json()
            if "error" in data:
                err = data["error"]
                raise InstagramError(f"{err.get('code')}/{err.get('error_subcode')}: {err.get('message')}")
            return data
        raise InstagramError(f"HTTP {r.status_code} after retries")

    def recent_posts(self, handle: str, limit: int = 12) -> list[Post]:
        base = f"business_discovery.username({handle}){{media.limit({limit}){{{_MEDIA_FIELDS}%s}}}}"
        try:
            data = self._get(base % f",children{{{_CHILD_FIELDS}}}")
        except InstagramError as e:
            # Some API versions/accounts reject nested children; retry without.
            if "children" not in str(e).lower() and "nested" not in str(e).lower():
                raise
            data = self._get(base % "")

        items = (data.get("business_discovery") or {}).get("media", {}).get("data", [])
        posts = []
        for m in items:
            posts.append(
                Post(
                    id=m["id"],
                    handle=handle,
                    caption=m.get("caption") or "",
                    permalink=m.get("permalink") or "",
                    timestamp=datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00")),
                    image_urls=_image_urls(m),
                )
            )
        return posts

    def check_token(self) -> dict:
        """Cheap call to confirm the token and IG user ID work."""
        return self._get("id,username")


def _image_urls(m: dict, max_images: int = 4) -> list[str]:
    """Pick the images Claude should look at: carousel slides first, else the
    post image, else a video thumbnail. Posters are almost always slide 1-2."""
    urls: list[str] = []
    children = (m.get("children") or {}).get("data") or []
    for c in children:
        u = c.get("media_url") if c.get("media_type") == "IMAGE" else c.get("thumbnail_url")
        if u:
            urls.append(u)
    if not urls:
        u = m.get("media_url") if m.get("media_type") in ("IMAGE", "CAROUSEL_ALBUM") else m.get("thumbnail_url")
        if u:
            urls.append(u)
    return urls[:max_images]


def filter_new(posts: list[Post], seen_ids: set[str], max_age_days: int) -> list[Post]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return [p for p in posts if p.id not in seen_ids and p.timestamp >= cutoff]
