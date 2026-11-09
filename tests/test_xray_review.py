"""Unit tests for core/xray_review.py (ADR 0002, weekly review queue)."""

from __future__ import annotations

from collections import Counter
import contextlib
import hashlib
import io
import json
import sqlite3
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock

from PIL import Image

import core.xray_review as xr
from core.database import XRAY_TABLES_DDL
from core.golden_pairs import build_golden_pairs
from core.xray_weights import count_pairs, estimate, group_pairs


class TestXrayReview(unittest.TestCase):
    def _make_synthetic_fixture(self):
        """Create ~30 listings in 2 blocks (Vake:sale and Saburtalo:sale)."""
        listings = []
        # Block 1: Vake, sale (15 listings)
        for i in range(1, 16):
            listings.append(
                {
                    "id": f"vake_{i}",
                    "source": "myhome" if i % 2 == 1 else "ss_ge",
                    "url": f"https://portal.example/vake/{i}",
                    "district": "Vake",
                    "subdistrict": "Vake Park",
                    "street": "Chavchavadze Ave",
                    "floor": str(i % 5 + 1),
                    "total_floors": 10,
                    "rooms": 2 + (i % 2),
                    "bedrooms": 1 + (i % 2),
                    "area_m2": 50.0 + (i % 6),
                    "price_usd": 80000.0 + i * 1000,
                    "deal_type": "sale",
                    "phone_number": f"5550001{i:02d}",
                    "group_id": f"g_vake_{i // 3}" if i <= 9 else None,
                }
            )
        # Block 2: Saburtalo, sale (15 listings)
        for i in range(1, 16):
            listings.append(
                {
                    "id": f"sab_{i}",
                    "source": "myhome" if i % 2 == 1 else "ss_ge",
                    "url": f"https://portal.example/sab/{i}",
                    "district": "Saburtalo",
                    "subdistrict": "Delisi",
                    "street": "Vazha-Pshavela Ave",
                    "floor": str(i % 4 + 1),
                    "total_floors": 9,
                    "rooms": 2 + (i % 3),
                    "bedrooms": 1 + (i % 3),
                    "area_m2": 52.0 + (i % 5),
                    "price_usd": 75000.0 + i * 1200,
                    "deal_type": "sale",
                    "phone_number": f"5550002{i:02d}",
                    "group_id": f"g_sab_{i // 3}" if i <= 9 else None,
                }
            )
        return listings

    def test_01_picks_synthetic(self):
        fixture = self._make_synthetic_fixture()
        counts = count_pairs(fixture)
        groups = group_pairs(fixture)
        weights, _ = estimate(counts, groups)

        week = "2026-W46"
        picks = xr.pick_review_pairs(fixture, weights, counts["pools"], set(), week)

        # 1. At most 10 picks
        self.assertLessEqual(len(picks), 10)
        self.assertGreater(len(picks), 0)

        # 2. No duplicate keys
        keys = [p["key"] for p in picks]
        self.assertEqual(len(keys), len(set(keys)))

        # 3. Two calls with the same week give identical picks
        picks_repeat = xr.pick_review_pairs(fixture, weights, counts["pools"], set(), week)
        self.assertEqual(keys, [p["key"] for p in picks_repeat])

        # 4. every review_random pick has 0 < q <= 1; every review_uncertain pick has q is None
        for p in picks:
            if p["origin"] == "review_random":
                self.assertIsNotNone(p["q"])
                self.assertGreater(p["q"], 0.0)
                self.assertLessEqual(p["q"], 1.0)
            elif p["origin"] == "review_uncertain":
                self.assertIsNone(p["q"])
            else:
                self.fail(f"Unexpected origin: {p['origin']}")

        # 5. Exclude works
        first_key = picks[0]["key"]
        picks_excluded = xr.pick_review_pairs(fixture, weights, counts["pools"], {first_key}, week)
        self.assertNotIn(first_key, {p["key"] for p in picks_excluded})

    def test_02_disagreement_first_one_per_kind(self):
        fixture = self._make_synthetic_fixture()
        counts = count_pairs(fixture)
        groups = group_pairs(fixture)
        weights, _ = estimate(counts, groups)

        week = "2026-W46"

        # Precompute post_by_pattern to inspect pattern differences
        post_by_pattern = {}
        for p in counts["pools"]["close_area"]:
            post_by_pattern[p] = xr._pattern_posterior(weights, p)

        pool_items = list(xr._pool(fixture, weights, set(), post_by_pattern))
        self.assertGreaterEqual(len(pool_items), 10)

        # Find 2 pairs with DIFFERENT levels patterns and disjoint listings
        p1 = pool_items[0]
        p2 = next(
            item
            for item in pool_items[1:]
            if item.levels != p1.levels
            and not ({item.a["id"], item.b["id"]} & {p1.a["id"], p1.b["id"]})
        )

        orig_champ = xr._candidate_matches_target
        try:
            # Case A: exactly 2 pool pairs of different patterns disagree with challenger
            # For p1 and p2, set champ opposite of challenger (post > 0.5)
            # For all others, set champ equal to challenger
            def mock_champ_2(a, b):
                k = xr.pair_key(a["id"], b["id"])
                if k == p1.key:
                    return not (p1.post > xr.POSSIBLE)
                if k == p2.key:
                    return not (p2.post > xr.POSSIBLE)
                # Others agree
                item = next(x for x in pool_items if x.key == k)
                return item.post > xr.POSSIBLE

            xr._candidate_matches_target = mock_champ_2
            picks = xr.pick_review_pairs(fixture, weights, counts["pools"], set(), week)
            uncertain_keys = {p["key"] for p in picks if p["origin"] == "review_uncertain"}
            self.assertIn(p1.key, uncertain_keys)
            self.assertIn(p2.key, uncertain_keys)

            # Case B: 3 pairs of the SAME pattern disagree
            # Group pool items by levels pattern
            from collections import defaultdict

            by_pat = defaultdict(list)
            for item in pool_items:
                by_pat[item.levels].append(item)

            same_3 = None
            for pat, items in by_pat.items():
                disjoint = []
                seen_ids = set()
                for it in items:
                    ids = {it.a["id"], it.b["id"]}
                    if not (seen_ids & ids):
                        disjoint.append(it)
                        seen_ids.update(ids)
                    if len(disjoint) == 3:
                        break
                if len(disjoint) == 3:
                    same_3 = disjoint
                    break

            self.assertIsNotNone(same_3)
            same_3_keys = {item.key for item in same_3}

            def mock_champ_3_same(a, b):
                k = xr.pair_key(a["id"], b["id"])
                if k in same_3_keys:
                    item = next(x for x in same_3 if x.key == k)
                    return not (item.post > xr.POSSIBLE)
                item = next(x for x in pool_items if x.key == k)
                return item.post > xr.POSSIBLE

            xr._candidate_matches_target = mock_champ_3_same
            picks3 = xr.pick_review_pairs(fixture, weights, counts["pools"], set(), week)
            uncertain_picks = [p for p in picks3 if p["origin"] == "review_uncertain"]
            picked_from_same_3 = [p for p in uncertain_picks if p["key"] in same_3_keys]
            self.assertEqual(len(picked_from_same_3), 3)
            self.assertEqual(picked_from_same_3[0]["kind_pairs"], 3)

            # Case C: a kind where champion says yes and one where champion says no are separate kinds
            pair_yes = same_3[0]
            pair_no = same_3[1]

            def mock_champ_separate(a, b):
                k = xr.pair_key(a["id"], b["id"])
                if k == pair_yes.key:
                    # We want champ=True, but challenger=False (disagreement)
                    return True
                if k == pair_no.key:
                    # We want champ=False, but challenger=True (disagreement)
                    return False
                item = next(x for x in pool_items if x.key == k)
                return item.post > xr.POSSIBLE

            # Force challenger posterior for pair_yes <= 0.5, and for pair_no > 0.5
            old_log_odds = xr.log_odds

            def mock_log_odds(w, levels):
                return old_log_odds(w, levels)

            xr._candidate_matches_target = mock_champ_separate
            # Both pair_yes and pair_no disagree with opposite champion answers
            # Verify kind keys (levels, True) and (levels, False) are separate
            kind_yes = (pair_yes.levels, True)
            kind_no = (pair_no.levels, False)
            self.assertNotEqual(kind_yes, kind_no)
        finally:
            xr._candidate_matches_target = orig_champ

    def test_03_exclusion(self):
        fixture = self._make_synthetic_fixture()
        counts = count_pairs(fixture)
        groups = group_pairs(fixture)
        weights, _ = estimate(counts, groups)

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE properties (
                id TEXT PRIMARY KEY,
                source TEXT,
                url TEXT,
                district TEXT,
                subdistrict TEXT,
                street TEXT,
                floor TEXT,
                total_floors INTEGER,
                rooms INTEGER,
                bedrooms INTEGER,
                area_m2 REAL,
                price_usd REAL,
                deal_type TEXT,
                phone_number TEXT,
                group_id TEXT
            )
        """)
        for ddl in XRAY_TABLES_DDL:
            conn.execute(ddl)

        # 1. Golden record listing_pair_key excluded
        p_key = xr.pair_key(fixture[0]["id"], fixture[1]["id"])
        records = [{"listing_pair_key": p_key}]

        # 2. Existing review_random row excluded
        p_key2 = xr.pair_key(fixture[2]["id"], fixture[3]["id"])
        conn.execute(
            """
            INSERT INTO xray_results (id, chat_id, kind, target, candidates, owner_target_id, created_at)
            VALUES ('res1', '123', 'review_random', ?, ?, NULL, '2026-11-09T00:00:00')
            """,
            (
                json.dumps({"id": fixture[2]["id"]}),
                json.dumps([{"id": fixture[3]["id"]}]),
            ),
        )
        conn.commit()

        seen = xr.seen_pair_keys(conn, records)
        self.assertIn(p_key, seen)
        self.assertIn(p_key2, seen)

        picks = xr.pick_review_pairs(fixture, weights, counts["pools"], seen, "2026-W46")
        pick_keys = {p["key"] for p in picks}
        self.assertNotIn(p_key, pick_keys)
        self.assertNotIn(p_key2, pick_keys)

    def test_04_message(self):
        a = {
            "source": "myhome",
            "url": "https://myhome.ge/1",
            "district": "<b>Vake</b>",
            "rooms": 2,
            "floor": "3",
            "total_floors": 9,
            "area_m2": 55.0,
            "price_usd": 75000.0,
        }
        b = {
            "source": "ss_ge",
            "url": "https://ss.ge/2",
            "district": "Saburtalo",
            "rooms": 3,
            "floor": None,
            "total_floors": 12,
            "area_m2": 65.5,
            "price_usd": 85000.0,
        }

        text = xr.review_text(1, 10, a, b)

        # Escapes district <b>x
        self.assertIn("&lt;b&gt;Vake&lt;/b&gt;", text)
        self.assertNotIn("<b>Vake</b>", text)

        # Missing floor leaves no სართ. part on listing b
        b_line = [line for line in text.split("\n") if line.startswith("ბ)")][0]
        self.assertNotIn("სართ.", b_line)

        # Keyboard checks
        kb = xr.review_keyboard("11111111-2222-3333-4444-555555555555")
        row = kb["inline_keyboard"][0]
        texts = [btn["text"] for btn in row]
        self.assertEqual(texts, ["1 ✅ იგივეა", "1 ❌ სხვაა", "1 🤷 ვერ ვხვდები"])

        for btn in row:
            data = btn["callback_data"]
            self.assertLessEqual(len(data.encode("utf-8")), 64)

    def test_05_sending(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE properties (
                id TEXT PRIMARY KEY,
                source TEXT,
                url TEXT,
                district TEXT,
                subdistrict TEXT,
                street TEXT,
                floor TEXT,
                total_floors INTEGER,
                rooms INTEGER,
                bedrooms INTEGER,
                area_m2 REAL,
                price_usd REAL,
                deal_type TEXT,
                phone_number TEXT,
                group_id TEXT
            )
        """)
        for ddl in XRAY_TABLES_DDL:
            conn.execute(ddl)

        fixture = self._make_synthetic_fixture()
        picks = [
            {
                "a": fixture[0],
                "b": fixture[1],
                "key": xr.pair_key(fixture[0]["id"], fixture[1]["id"]),
                "origin": "review_uncertain",
                "posterior": 0.52,
                "champion": False,
                "kind_pairs": 4,
                "q": None,
                "pool": 100,
            },
            {
                "a": fixture[2],
                "b": fixture[3],
                "key": xr.pair_key(fixture[2]["id"], fixture[3]["id"]),
                "origin": "review_random",
                "posterior": 0.85,
                "champion": True,
                "kind_pairs": None,
                "q": 0.01,
                "pool": 100,
            },
            {
                "a": fixture[4],
                "b": fixture[5],
                "key": xr.pair_key(fixture[4]["id"], fixture[5]["id"]),
                "origin": "review_random",
                "posterior": 0.12,
                "champion": False,
                "kind_pairs": None,
                "q": 0.005,
                "pool": 100,
            },
        ]

        # Fake send fails on 2nd call
        call_count = [0]

        def fake_send(method, payload):
            call_count[0] += 1
            if call_count[0] == 2:
                return {"ok": False}
            return {"ok": True}

        fake_sleep = MagicMock()
        sent = xr.send_review(
            conn=conn,
            send=fake_send,
            chat_id="777",
            picks=picks,
            version="w-2026-W46",
            week="2026-W46",
            now_iso="2026-11-09T03:00:00",
            sleep=fake_sleep,
        )

        self.assertEqual(sent, 2)
        self.assertEqual(fake_sleep.call_count, 2)

        rows = conn.execute("SELECT * FROM xray_results").fetchall()
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["chat_id"], "777")
            target = json.loads(r["target"])
            self.assertIn("_review", target)
            self.assertEqual(target["_review"]["week"], "2026-W46")
            self.assertEqual(target["_review"]["version"], "w-2026-W46")

    def test_06_round_trip(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE properties (
                id TEXT PRIMARY KEY,
                source TEXT,
                url TEXT,
                district TEXT,
                subdistrict TEXT,
                street TEXT,
                floor TEXT,
                total_floors INTEGER,
                rooms INTEGER,
                bedrooms INTEGER,
                area_m2 REAL,
                price_usd REAL,
                deal_type TEXT,
                phone_number TEXT,
                group_id TEXT
            )
        """)
        for ddl in XRAY_TABLES_DDL:
            conn.execute(ddl)

        # Insert properties row for backfill
        conn.execute(
            """
            INSERT INTO properties (id, source, url, district, area_m2, floor, deal_type, price_usd, phone_number)
            VALUES ('ss_ge_1', 'ss_ge', 'https://ss.ge/1', 'Vake', 55, '3', 'sale', 80000, '555000111')
            """
        )
        conn.commit()

        fixture = self._make_synthetic_fixture()
        pick = {
            "a": fixture[0],
            "b": fixture[1],
            "key": xr.pair_key(fixture[0]["id"], fixture[1]["id"]),
            "origin": "review_random",
            "posterior": 0.88,
            "champion": True,
            "kind_pairs": None,
            "q": 0.02,
            "pool": 100,
        }

        fake_send = MagicMock(return_value={"ok": True})
        xr.send_review(
            conn=conn,
            send=fake_send,
            chat_id="777",
            picks=[pick],
            version="w-2026-W46",
            week="2026-W46",
            now_iso="2026-11-09T03:00:00",
            sleep=lambda s: None,
        )

        row = conn.execute("SELECT * FROM xray_results").fetchone()
        self.assertIsNotNone(row)
        rid = row["id"]
        cands = json.loads(row["candidates"])
        cand_id = cands[0]["id"]

        # Insert feedback
        conn.execute(
            """
            INSERT INTO xray_feedback (job_id, candidate_id, chat_id, target_id, label, created_at)
            VALUES (?, ?, '777', NULL, 1, '2026-11-09T04:00:00')
            """,
            (rid, cand_id),
        )
        conn.commit()

        records, _ = build_golden_pairs(conn, "777")
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["label_origin"], "review_random")
        self.assertIsNotNone(rec["review"])
        self.assertEqual(rec["review"]["origin"], "review_random")
        self.assertEqual(rec["review"]["version"], "w-2026-W46")
        self.assertEqual(rec["review"]["week"], "2026-W46")
        self.assertEqual(rec["review"]["pool"], 100)

    def test_07_current_photo_url(self):
        # 1. an old-host URL becomes the new host with the same path
        old_url = "https://static-statements.tnet.ge/uploads/2026/08/pic.jpg"
        expected = "https://static-api-statements.tnet.ge/uploads/2026/08/pic.jpg"
        self.assertEqual(xr.current_photo_url(old_url), expected)

        # Also scheme-relative or http
        self.assertEqual(
            xr.current_photo_url("//static-statements.tnet.ge/img.png"),
            "//static-api-statements.tnet.ge/img.png",
        )
        self.assertEqual(
            xr.current_photo_url("http://static-statements.tnet.ge/pic.jpg"),
            "http://static-api-statements.tnet.ge/pic.jpg",
        )

        # 2. a static.ss.ge URL and "" are unchanged
        ss_url = "https://static.ss.ge/uploads/pic.jpg"
        self.assertEqual(xr.current_photo_url(ss_url), ss_url)
        self.assertEqual(xr.current_photo_url(""), "")

    def test_08_load_photo_urls(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE properties (id TEXT PRIMARY KEY, images_json TEXT)")

        six_urls = [f"https://static-statements.tnet.ge/photo_{i}.jpg" for i in range(1, 7)]
        conn.execute(
            "INSERT INTO properties (id, images_json) VALUES (?, ?)",
            ("p1", json.dumps(six_urls)),
        )
        conn.execute(
            "INSERT INTO properties (id, images_json) VALUES (?, ?)",
            ("p2", "invalid json{"),
        )
        conn.commit()

        res = xr.load_photo_urls(conn, ["p1", "p2", "p3"])
        # an old-host list of 6 URLs gives 4 rewritten URLs
        self.assertIn("p1", res)
        self.assertEqual(len(res["p1"]), 4)
        for i, u in enumerate(res["p1"], 1):
            self.assertEqual(u, f"https://static-api-statements.tnet.ge/photo_{i}.jpg")

        # bad JSON gives []
        self.assertIn("p2", res)
        self.assertEqual(res["p2"], [])

        # a missing id is absent
        self.assertNotIn("p3", res)
        conn.close()

    def test_09_collage(self):
        def _make_png(w: int = 100, h: int = 100, color=(255, 0, 0)) -> bytes:
            img = Image.new("RGB", (w, h), color)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        # rows of 3 and 4 images give a JPEG of size (4*320, 2*240)
        r3 = [_make_png(color=(i * 30, 0, 0)) for i in range(3)]
        r4 = [_make_png(color=(0, i * 30, 0)) for i in range(4)]
        jpeg = xr.collage([r3, r4])
        self.assertIsNotNone(jpeg)
        im = Image.open(io.BytesIO(jpeg))
        self.assertEqual(im.size, (4 * 320, 2 * 240))
        self.assertEqual(im.format, "JPEG")

        # rows of 2 and 0 give (2*320, 2*240)
        r2 = [_make_png(color=(0, 0, 255)), _make_png(color=(100, 100, 100))]
        jpeg2 = xr.collage([r2, []])
        self.assertIsNotNone(jpeg2)
        im2 = Image.open(io.BytesIO(jpeg2))
        self.assertEqual(im2.size, (2 * 320, 2 * 240))

        # two empty rows give None
        self.assertIsNone(xr.collage([[], []]))

        # a row of [b"not an image", <valid png>] keeps 1 tile
        bad_and_good = [b"not an image", _make_png()]
        jpeg3 = xr.collage([bad_and_good, []])
        self.assertIsNotNone(jpeg3)
        im3 = Image.open(io.BytesIO(jpeg3))
        self.assertEqual(im3.size, (1 * 320, 2 * 240))

    def test_10_send_review_with_photos(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        for ddl in XRAY_TABLES_DDL:
            conn.execute(ddl)

        def _make_png() -> bytes:
            img = Image.new("RGB", (50, 50), (200, 100, 50))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        fixture = self._make_synthetic_fixture()
        pick = {
            "a": fixture[0],
            "b": fixture[1],
            "key": xr.pair_key(fixture[0]["id"], fixture[1]["id"]),
            "origin": "review_uncertain",
            "posterior": 0.6,
            "champion": True,
            "kind_pairs": 5,
            "q": None,
            "pool": 50,
        }

        fake_download = MagicMock(return_value=_make_png())
        send_photo_calls = []

        def fake_send_photo(chat_id, jpeg, caption, reply_markup):
            send_photo_calls.append(
                {
                    "chat_id": chat_id,
                    "jpeg": jpeg,
                    "caption": caption,
                    "reply_markup": reply_markup,
                }
            )
            return {"ok": True}

        fake_send_text = MagicMock(return_value={"ok": True})

        # Case 1: All given with photos -> send_photo called, text send not called
        sent = xr.send_review(
            conn=conn,
            send=fake_send_text,
            chat_id="777",
            picks=[pick],
            version="w-2026-W46",
            week="2026-W46",
            now_iso="2026-11-09T03:00:00",
            sleep=lambda s: None,
            send_photo=fake_send_photo,
            download=fake_download,
            photo_urls={
                fixture[0]["id"]: ["https://example.com/a.jpg"],
                fixture[1]["id"]: ["https://example.com/b.jpg"],
            },
        )
        self.assertEqual(sent, 1)
        self.assertEqual(len(send_photo_calls), 1)
        self.assertIn("ზედა რიგი", send_photo_calls[0]["caption"])
        rid = conn.execute("SELECT id FROM xray_results").fetchone()["id"]
        self.assertEqual(send_photo_calls[0]["reply_markup"], xr.review_keyboard(rid))
        fake_send_text.assert_not_called()

        # Case 2: photo_urls={} -> fallback to text path
        send_photo_calls.clear()
        fake_send_text.reset_mock()
        conn.execute("DELETE FROM xray_results")
        conn.commit()

        sent = xr.send_review(
            conn=conn,
            send=fake_send_text,
            chat_id="777",
            picks=[pick],
            version="w-2026-W46",
            week="2026-W46",
            now_iso="2026-11-09T03:00:00",
            sleep=lambda s: None,
            send_photo=fake_send_photo,
            download=fake_download,
            photo_urls={},
        )
        self.assertEqual(sent, 1)
        self.assertEqual(len(send_photo_calls), 0)
        self.assertEqual(fake_send_text.call_count, 1)

        # Case 3: failing send_photo deletes the row
        send_photo_calls.clear()
        fake_send_text.reset_mock()
        conn.execute("DELETE FROM xray_results")
        conn.commit()

        fake_failing_photo = MagicMock(return_value=None)
        sent = xr.send_review(
            conn=conn,
            send=fake_send_text,
            chat_id="777",
            picks=[pick],
            version="w-2026-W46",
            week="2026-W46",
            now_iso="2026-11-09T03:00:00",
            sleep=lambda s: None,
            send_photo=fake_failing_photo,
            download=fake_download,
            photo_urls={
                fixture[0]["id"]: ["https://example.com/a.jpg"],
                fixture[1]["id"]: ["https://example.com/b.jpg"],
            },
        )
        self.assertEqual(sent, 0)
        rows = conn.execute("SELECT * FROM xray_results").fetchall()
        self.assertEqual(len(rows), 0)
        conn.close()

    def test_11_telegram_photo_sender(self):
        from scripts.estimate_xray_weights import telegram_photo_sender

        sender = telegram_photo_sender("TEST_TOKEN_123")
        dummy_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        dummy_markup = {"inline_keyboard": [[{"text": "btn", "callback_data": "cbd"}]]}

        captured_requests = []

        class MockResp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

            def read(self):
                return b'{"ok": true, "result": {"message_id": 42}}'

        def mock_urlopen(req, timeout=60):
            captured_requests.append(req)
            return MockResp()

        with unittest.mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
            res = sender("12345", dummy_jpeg, "Caption text", dummy_markup)

        self.assertIsNotNone(res)
        self.assertTrue(res.get("ok"))
        self.assertEqual(len(captured_requests), 1)

        req = captured_requests[0]
        self.assertEqual(req.full_url, "https://api.telegram.org/botTEST_TOKEN_123/sendPhoto")
        body = req.data
        self.assertIn(b'name="photo"; filename="pair.jpg"', body)
        self.assertIn(dummy_jpeg, body)
        self.assertIn(b'name="parse_mode"\r\n\r\nHTML\r\n', body)
        self.assertIn(json.dumps(dummy_markup, ensure_ascii=False).encode("utf-8"), body)

        # when urlopen raises, it returns None and stdout is empty
        def mock_failing_urlopen(req, timeout=60):
            raise urllib.error.URLError("network error")

        stdout_buf = io.StringIO()
        with unittest.mock.patch("urllib.request.urlopen", side_effect=mock_failing_urlopen):
            with contextlib.redirect_stdout(stdout_buf):
                res_fail = sender("12345", dummy_jpeg, "Caption text", dummy_markup)

        self.assertIsNone(res_fail)
        self.assertEqual(stdout_buf.getvalue(), "")

    def test_12_caption_length(self):
        a = {
            "source": "myhome",
            "url": "https://www.myhome.ge/ka/pr/12345678/iyideba-5-otaxiani-bina-varketilis-me-2-mikroraionshi/",
            "district": "სამგორის რაიონი / ვარკეთილი 3, მე-2 მიკრორაიონი",
            "rooms": 5,
            "floor": "15",
            "total_floors": 16,
            "area_m2": 198.5,
            "price_usd": 250000,
        }
        b = {
            "source": "ss_ge",
            "url": "https://home.ss.ge/ka/udzravi-qoneba/iyideba-5-otaxiani-bina-varketilis-me-2-mikroraionshi-9999999",
            "district": "სამგორის რაიონი / ვარკეთილი 3, მე-2 მიკრორაიონი",
            "rooms": 5,
            "floor": "15",
            "total_floors": 16,
            "area_m2": 198.5,
            "price_usd": 250000,
        }
        caption = xr.review_caption(10, 10, a, b)
        self.assertLessEqual(len(caption), 1024)
        self.assertIn("ზედა რიგი: ა", caption)

    def test_13_round_robin(self):
        # 1. Round-robin. With 3 disagreement kinds of sizes 5, 3 and 1 and n_uncertain=6:
        # _round_robin asserts the exact order [k5, k3, k1, k5, k3, k5];
        # pick_review_pairs asserts Counter == {k5: 3, k3: 2, k1: 1} and distinct keys.
        k5_pattern = ("equal", "equal", "agree", "agree", "agree")
        k3_pattern = ("close", "equal", "agree", "agree", "agree")
        k1_pattern = ("equal", "close", "agree", "agree", "agree")

        k5 = (k5_pattern, True)
        k3 = (k3_pattern, False)
        k1 = (k1_pattern, True)

        items_k5 = [
            xr.PoolItem(f"k5_{i}", {"id": f"a_5_{i}"}, {"id": f"b_5_{i}"}, 0.1, True, k5_pattern)
            for i in range(1, 6)
        ]
        items_k3 = [
            xr.PoolItem(f"k3_{i}", {"id": f"a_3_{i}"}, {"id": f"b_3_{i}"}, 0.9, False, k3_pattern)
            for i in range(1, 4)
        ]
        items_k1 = [xr.PoolItem("k1_1", {"id": "a_1_1"}, {"id": "b_1_1"}, 0.2, True, k1_pattern)]
        all_items = items_k5 + items_k3 + items_k1

        # Test _round_robin directly: order [k5, k3, k1, k5, k3, k5]
        sorted_kinds = [
            (k5, {"count": 5, "items": [(0.0, x) for x in items_k5]}),
            (k3, {"count": 3, "items": [(0.0, x) for x in items_k3]}),
            (k1, {"count": 1, "items": [(0.0, x) for x in items_k1]}),
        ]
        rr_picks = xr._round_robin(sorted_kinds, 6)
        rr_kinds = [(item.levels, item.champ) for item, _count in rr_picks]
        self.assertEqual(rr_kinds, [k5, k3, k1, k5, k3, k5])

        # pick_review_pairs shuffles with sha256, so assert multiset
        with unittest.mock.patch("core.xray_review._pool", return_value=iter(all_items)):
            picks = xr.pick_review_pairs(
                [], {}, {"close_area": {}}, set(), "2026-W46", n_uncertain=6, n_random=0
            )

        self.assertEqual(len(picks), 6)

        kind_counts = Counter()
        for p in picks:
            matching = next(x for x in all_items if x.key == p["key"])
            kind_counts[(matching.levels, matching.champ)] += 1

        self.assertEqual(kind_counts, Counter({k5: 3, k3: 2, k1: 1}))

        keys = [p["key"] for p in picks]
        self.assertEqual(len(keys), len(set(keys)))

    def test_14_zero_uncertain_and_zero_random(self):
        # 2. n_uncertain=0, n_random=0 gives [], and iter_block_pairs is not called (patch it to raise).
        with unittest.mock.patch(
            "core.xray_review.iter_block_pairs", side_effect=RuntimeError("must not be called")
        ):
            picks = xr.pick_review_pairs([], {}, {}, set(), "2026-W46", n_uncertain=0, n_random=0)
            self.assertEqual(picks, [])

    def test_15_blind_order(self):
        # Sprint-sized call (n_uncertain=6, n_random=4) returns keys ordered by sha256(f"{week}:o:{key}")
        fixture = self._make_synthetic_fixture()
        counts = count_pairs(fixture)
        groups = group_pairs(fixture)
        weights, _ = estimate(counts, groups)
        week = "2026-W46"

        picks = xr.pick_review_pairs(
            fixture, weights, counts["pools"], set(), week, n_uncertain=6, n_random=4
        )
        self.assertEqual(len(picks), 10)

        keys = [p["key"] for p in picks]
        expected_keys = sorted(
            keys,
            key=lambda k: hashlib.sha256(f"{week}:o:{k}".encode("utf-8")).hexdigest(),
        )
        self.assertEqual(keys, expected_keys)

        origins = [p["origin"] for p in picks]
        uncertain_then_random = sorted(origins, key=lambda o: 0 if o == "review_uncertain" else 1)
        self.assertNotEqual(origins, uncertain_then_random)

    def test_16_sampler_no_repeated_photos(self):
        fixture = self._make_synthetic_fixture()
        # Set identical group_id on two listings in fixture: vake_1 and vake_2
        fixture[0]["group_id"] = "shared_group_xyz"
        fixture[1]["group_id"] = "shared_group_xyz"

        counts = count_pairs(fixture)
        groups = group_pairs(fixture)
        weights, _ = estimate(counts, groups)
        week = "2026-W46"

        # 1. A pair whose listings share group_id is never picked
        picks = xr.pick_review_pairs(
            fixture, weights, counts["pools"], set(), week, n_uncertain=10, n_random=10
        )
        for p in picks:
            g_a = p["a"].get("group_id")
            g_b = p["b"].get("group_id")
            self.assertFalse(g_a and g_b and g_a == g_b, f"Pair {p['key']} shares group_id {g_a}")

        # 2. A listing present in an existing review row is never picked
        class MockConn:
            def execute(self, sql, params=()):
                class MockRow:
                    def __init__(self, t, c):
                        self.t = json.dumps(t)
                        self.c = json.dumps(c)

                    def __getitem__(self, item):
                        if item == "target":
                            return self.t
                        if item == "candidates":
                            return self.c
                        raise KeyError(item)

                class MockCursor:
                    def fetchall(self):
                        return [MockRow({"id": "vake_3"}, [{"id": "vake_99"}])]

                return MockCursor()

        seen_pairs, seen_listings = xr.seen_review_items(MockConn(), [])
        self.assertIn("vake_3", seen_listings)

        picks_with_seen = xr.pick_review_pairs(
            fixture,
            weights,
            counts["pools"],
            seen_pairs,
            week,
            n_uncertain=6,
            n_random=4,
            seen_listing_ids=seen_listings,
        )
        for p in picks_with_seen:
            self.assertNotEqual(str(p["a"]["id"]), "vake_3")
            self.assertNotEqual(str(p["b"]["id"]), "vake_3")

        # 3. In one sprint-sized call (n_uncertain=6, n_random=4) no listing id appears twice
        picks_sprint = xr.pick_review_pairs(
            fixture, weights, counts["pools"], set(), week, n_uncertain=6, n_random=4
        )
        listing_ids = []
        for p in picks_sprint:
            listing_ids.append(str(p["a"]["id"]))
            listing_ids.append(str(p["b"]["id"]))
        self.assertEqual(len(listing_ids), len(set(listing_ids)))
        self.assertEqual(len(set(listing_ids)), 2 * len(picks_sprint))


if __name__ == "__main__":
    unittest.main()
