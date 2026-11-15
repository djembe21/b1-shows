"""Offline tests. Run: python -m unittest discover tests"""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gigradar.config import KST, Account, VenueIndex  # noqa: E402
from gigradar.extract import ClaudeExtractor, is_valid_event  # noqa: E402
from gigradar.instagram import InstagramClient, InstagramError, Post  # noqa: E402
from gigradar.store import normalize, upsert  # noqa: E402


class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params["fields"])
        return self.responses.pop(0)


BD = {
    "business_discovery": {
        "media": {
            "data": [
                {
                    "id": "1", "caption": "11.21 공연", "media_type": "CAROUSEL_ALBUM",
                    "permalink": "https://www.instagram.com/p/x/", "timestamp": "2026-11-10T03:00:00+0000",
                    "children": {"data": [
                        {"media_type": "IMAGE", "media_url": "https://cdn/a.jpg"},
                        {"media_type": "VIDEO", "thumbnail_url": "https://cdn/b.jpg"},
                    ]},
                },
                {
                    "id": "2", "media_type": "VIDEO", "thumbnail_url": "https://cdn/c.jpg",
                    "permalink": "https://www.instagram.com/reel/y/", "timestamp": "2026-11-11T03:00:00+0000",
                },
            ]
        }
    }
}


class InstagramTests(unittest.TestCase):
    def test_parses_business_discovery(self):
        s = FakeSession([FakeResp(BD)])
        posts = InstagramClient("123", "tok", s).recent_posts("venue", 5)
        self.assertEqual([p.id for p in posts], ["1", "2"])
        self.assertEqual(posts[0].image_urls, ["https://cdn/a.jpg", "https://cdn/b.jpg"])
        self.assertEqual(posts[1].image_urls, ["https://cdn/c.jpg"])
        self.assertEqual(posts[1].caption, "")
        self.assertIn("business_discovery.username(venue)", s.calls[0])
        self.assertIn("media.limit(5)", s.calls[0])

    def test_falls_back_without_children(self):
        err = {"error": {"code": 100, "message": "Tried accessing nonexisting field (children)"}}
        s = FakeSession([FakeResp(err, 400), FakeResp(BD)])
        posts = InstagramClient("123", "tok", s).recent_posts("venue")
        self.assertEqual(len(posts), 2)
        self.assertNotIn("children", s.calls[1])

    def test_personal_account_error_raises(self):
        err = {"error": {"code": 110, "message": "Invalid user id"}}
        with self.assertRaises(InstagramError):
            InstagramClient("123", "tok", FakeSession([FakeResp(err, 400)])).recent_posts("someone")


class ExtractorTests(unittest.TestCase):
    def test_tool_call_is_parsed(self):
        ex = ClaudeExtractor.__new__(ClaudeExtractor)
        ex.model = "m"
        captured = {}

        def create(**kw):
            captured.update(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="record_events",
                                                            input={"events": [{"date": "2026-11-21", "venue": "X", "lineup": ["A"], "confidence": 0.9}]})])

        ex.client = SimpleNamespace(messages=SimpleNamespace(create=create))
        post = Post("1", "venue", "cap", "https://i/p", datetime(2026, 11, 10, tzinfo=KST), [])
        out = ex.extract(post, "venue: X")
        self.assertEqual(out[0]["venue"], "X")
        self.assertEqual(captured["tool_choice"]["name"], "record_events")
        self.assertIn("2026-11-10", captured["messages"][0]["content"][-1]["text"])

    def test_validation(self):
        today = datetime(2026, 11, 14)
        ok = {"date": "2026-11-20", "venue": "X", "confidence": 0.9}
        self.assertTrue(is_valid_event(ok, today))
        self.assertFalse(is_valid_event({**ok, "date": "2026-11-01"}, today))
        self.assertFalse(is_valid_event({**ok, "date": "11/20"}, today))
        self.assertFalse(is_valid_event({**ok, "confidence": 0.2}, today))


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.venue = Account("ff_handle", "venue", "Club FF", "클럽 FF", "Hongdae", ["클럽에프에프"])
        self.band = Account("band", "band", "Band")
        self.idx = VenueIndex([self.venue])
        self.post = lambda h, i: Post(i, h, "", f"https://i/{i}", datetime(2026, 11, 10, tzinfo=KST))

    def test_band_and_venue_posts_merge(self):
        events = []
        a = normalize({"date": "2026-11-21", "start": "19:30", "venue": "Club FF", "lineup": ["A", "B"], "confidence": 0.9},
                      self.post("ff_handle", "1"), self.venue, self.idx)
        b = normalize({"date": "2026-11-21", "venue": "클럽에프에프", "lineup": ["B", "C"], "price": "20,000", "confidence": 0.8},
                      self.post("band", "2"), self.band, self.idx)
        self.assertEqual(upsert(events, a, "t"), "added")
        self.assertEqual(upsert(events, b, "t"), "merged")
        self.assertEqual(events[0]["lineup"], ["A", "B", "C"])
        self.assertEqual(events[0]["price"], "20,000")
        self.assertEqual(len(events[0]["sources"]), 2)

    def test_same_venue_two_shows(self):
        events = []
        for i, (t, l) in enumerate([("15:00", ["A"]), ("20:00", ["Z"])]):
            upsert(events, normalize({"date": "2026-11-21", "start": t, "venue": "Club FF", "lineup": l, "confidence": 0.9},
                                     self.post("ff_handle", str(i)), self.venue, self.idx), "t")
        self.assertEqual(len(events), 2)


class DryRunTest(unittest.TestCase):
    def test_end_to_end(self):
        import os
        env = {**os.environ, "GIGRADAR_TODAY": "2026-11-14"}
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as data:
            for _ in range(2):  # second run must be idempotent
                r = subprocess.run([sys.executable, "-m", "gigradar.run", "--dry-run", "--data-dir", data, "--docs-dir", d],
                                   cwd=ROOT, capture_output=True, text=True, env=env)
                self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(Path(d, "events.json").read_text())
            self.assertEqual(out["count"], 8)
            first = out["events"][0]
            self.assertEqual(len(first["sources"]), 2)  # venue + band post merged
            self.assertTrue(Path(d, "calendar.ics").read_text().startswith("BEGIN:VCALENDAR"))


if __name__ == "__main__":
    unittest.main()
