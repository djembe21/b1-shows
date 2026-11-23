"""Load the watchlist and shared settings."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
KST = ZoneInfo("Asia/Seoul")


@dataclass
class Account:
    handle: str
    kind: str  # venue | band | promoter
    name: str = ""
    name_ko: str = ""
    area: str = ""
    aliases: list[str] = field(default_factory=list)
    fetch: bool = True  # False = only used to recognise the venue on posters


@dataclass
class Watchlist:
    accounts: list[Account]
    posts_per_account: int = 12
    max_post_age_days: int = 30

    @property
    def venues(self) -> list[Account]:
        return [a for a in self.accounts if a.kind == "venue"]


def load_watchlist(path: Path | None = None) -> Watchlist:
    path = path or ROOT / "watchlist.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    accounts: list[Account] = []
    for section, kind in (("venues", "venue"), ("bands", "band"), ("promoters", "promoter")):
        for item in raw.get(section) or []:
            if not item or not item.get("handle"):
                continue
            accounts.append(
                Account(
                    handle=str(item["handle"]).lstrip("@").strip(),
                    kind=kind,
                    name=item.get("name", ""),
                    name_ko=item.get("name_ko", ""),
                    area=item.get("area", ""),
                    aliases=[str(a) for a in item.get("aliases") or []],
                    fetch=bool(item.get("fetch", True)),
                )
            )
    settings = raw.get("settings") or {}
    return Watchlist(
        accounts=accounts,
        posts_per_account=int(settings.get("posts_per_account", 12)),
        max_post_age_days=int(settings.get("max_post_age_days", 30)),
    )


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_.·()'’]+", "", (s or "").lower())


class VenueIndex:
    """Maps any known spelling of a venue to its canonical entry."""

    def __init__(self, venues: list[Account]):
        self._by_key: dict[str, Account] = {}
        for v in venues:
            for name in [v.name, v.name_ko, v.handle, *v.aliases]:
                if name:
                    self._by_key[_norm(name)] = v

    def lookup(self, *names: str) -> Account | None:
        for n in names:
            if not n:
                continue
            key = _norm(n)
            if key in self._by_key:
                return self._by_key[key]
        # loose contains-match as a fallback ("Club FF Hongdae 2F")
        for n in names:
            key = _norm(n or "")
            if len(key) < 3:
                continue
            for k, v in self._by_key.items():
                if len(k) >= 3 and (k in key or key in k):
                    return v
        return None
