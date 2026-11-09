"""ADR 0005: a post's photos compared with the photos of the listings in the database
(core/photo_match.py).

Runs against a local SQLite copy of the D1 tables. The photos are seeded noise images, whose
dHashes differ from each other by far more than the duplicate finder's limit; nothing here
touches the network.
"""

import asyncio
import io
import os
import random
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from PIL import Image

from core import photo_match
from core.database import DatabaseEngine
from core.dedup import HASH_THRESHOLD, _decode_dhash_sync, hamming
from core.photo_match import batch_hashes, load_listing_hashes, lookup_photos
from core.xray import SNAPSHOT_FIELDS

OLD = "2026-01-01T00:00:00.000Z"  # older than the album settle time, so no test waits
BATCH = "123:g1"


def noise_png(seed):
    rng = random.Random(seed)
    img = Image.frombytes("L", (64, 64), bytes(rng.randrange(256) for _ in range(64 * 64)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def hash_of(seed):
    return _decode_dhash_sync(noise_png(seed))


class PhotoMatchCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.temp_dir.name, "photos.db")
        DatabaseEngine(db_path=path, use_d1=False).init_db()
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.files = {}
        self.downloads = []
        hashes = [hash_of(s) for s in range(1, 10)]
        for i, a in enumerate(hashes):
            for b in hashes[i + 1 :]:
                self.assertGreater(hamming(a, b), HASH_THRESHOLD, "the test images are too alike")

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    async def download(self, file_id):
        self.downloads.append(file_id)
        return self.files.get(file_id)

    def listing(self, lid, seeds, district="დიდი დიღომი", price=95000, area=58, source="myhome"):
        self.conn.execute(
            "INSERT INTO properties (id, source, source_id, title, price_usd, area_m2,"
            " price_per_m2, district, street, floor, total_floors, rooms, url, scraped_at)"
            " VALUES (?, ?, ?, 'ბინა', ?, ?, 1600, ?, 'ქუჩა', '3', 18, 3, ?, '2026-11-08T09:00:00')",
            (lid, source, lid.split("_")[-1], price, area, district, f"https://x.ge/{lid}"),
        )
        for i, seed in enumerate(seeds):
            self.conn.execute(
                "INSERT INTO image_hashes (listing_id, image_index, phash) VALUES (?, ?, ?)",
                (lid, i, hash_of(seed)),
            )
        self.conn.commit()

    def post(self, seeds, caption=None, created_at=OLD, batch=BATCH):
        for n, seed in enumerate(seeds, 1):
            file_id = f"f{n}"
            self.files[file_id] = noise_png(seed)
            self.conn.execute(
                "INSERT INTO xray_photos (batch_id, message_id, chat_id, file_id, created_at)"
                " VALUES (?, ?, '123', ?, ?)",
                (batch, n, file_id, created_at),
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO xray_photo_batches (batch_id, chat_id, caption, created_at)"
            " VALUES (?, '123', ?, ?)",
            (batch, caption, created_at),
        )
        self.conn.commit()

    def run_lookup(self, batch=BATCH):
        return asyncio.run(lookup_photos(self.conn, batch, self.download))


class TestLookupPhotos(PhotoMatchCase):
    def test_two_equal_photos_list_the_listing_with_the_coverage_line(self):
        self.listing("myhome_1", [1, 2, 3])
        self.listing("ss_ge_2", [4], source="ss_ge", district="საბურთალო")
        self.post([1, 2])
        result = self.run_lookup()
        self.assertTrue(result["ok"])
        self.assertEqual([r["id"] for r in result["rows"]], ["myhome_1"])
        stored = result["result"]
        cand0 = stored["candidates"][0]
        self.assertEqual(cand0["id"], "myhome_1")
        self.assertEqual(cand0["url"], "https://x.ge/myhome_1")
        self.assertGreaterEqual(cand0["photo_matches"], 1)
        self.assertEqual(cand0["area_m2"], 58)
        self.assertTrue(set(cand0.keys()).issubset(set(SNAPSHOT_FIELDS) | {"url", "photo_matches"}))
        text = stored["text"]
        self.assertIn("📸 <b>ფოტოებით შედარება</b> (2 ფოტო)", text)
        self.assertIn("✅ <b>იგივე ფოტოები:</b>", text)
        self.assertIn('1. <a href="https://x.ge/myhome_1">MyHome</a> — <b>$95,000</b>', text)
        self.assertIn("ემთხვევა 2 ფოტო", text)
        self.assertIn("ℹ️ შევადარე ჩვენი ბაზის 2 განცხადების ფოტოებს", text)
        self.assertIn("მონიშნე ქვემოთ: ✅ იგივე ბინაა, ❌ სხვაა, 🤷 ვერ ვხვდები.", text)

    def test_one_equal_photo_without_a_price_and_area_does_not_list_the_listing(self):
        self.listing("myhome_1", [1, 2, 3])
        self.post([1])
        result = self.run_lookup()
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["candidates"], [])
        self.assertIn(
            "ℹ️ ჩვენს ბაზაში ამ ფოტოებით განცხადება ვერ მოიძებნა.", result["result"]["text"]
        )

    def test_one_equal_photo_lists_the_listing_when_the_caption_fits_its_price_and_area(self):
        self.listing("myhome_1", [1, 2, 3])
        self.post([1], caption="იყიდება ბინა დიდ დიღომში, 58 მ², 3 სართული, $95,000")
        result = self.run_lookup()
        self.assertEqual([c["id"] for c in result["result"]["candidates"]], ["myhome_1"])

    def test_one_equal_photo_with_a_caption_far_from_the_listing_does_not_list_it(self):
        self.listing("myhome_1", [1, 2, 3])
        self.post([1], caption="იყიდება ბინა დიდ დიღომში, 90 მ², 3 სართული, $95,000")
        self.assertEqual(self.run_lookup()["result"]["candidates"], [])

    def test_a_photo_found_in_two_districts_is_a_logo_and_is_dropped(self):
        self.listing("myhome_1", [1, 2, 3])
        self.listing("ss_ge_9", [1], source="ss_ge", district="საბურთალო")
        self.post([1, 2, 3])
        result = self.run_lookup()
        text = result["result"]["text"]
        self.assertEqual([c["id"] for c in result["result"]["candidates"]], ["myhome_1"])
        self.assertIn("ემთხვევა 2 ფოტო", text, "the dropped photo must not count")
        self.assertIn(
            "ℹ️ 1 ფოტო სხვადასხვა უბნის განცხადებებშია (ლოგო ან რენდერი), ამიტომ არ ჩავთვალე.", text
        )

    def test_no_match_says_so_and_still_names_the_coverage(self):
        self.listing("myhome_1", [1, 2, 3])
        self.post([8, 9])
        result = self.run_lookup()
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["candidates"], [])
        self.assertIn("ვერ მოიძებნა", result["result"]["text"])
        self.assertIn("ℹ️ შევადარე ჩვენი ბაზის 1 განცხადების ფოტოებს", result["result"]["text"])

    def test_at_most_five_listings_are_shown_best_match_first(self):
        for n in range(1, 8):
            self.listing(f"myhome_{n}", [1, 2] if n != 3 else [1, 2, 3], price=90000 + n)
        self.post([1, 2, 3])
        candidates = self.run_lookup()["result"]["candidates"]
        self.assertEqual(len(candidates), 5)
        self.assertEqual(candidates[0]["id"], "myhome_3", "3 matching photos come before 2")


class TestLoadListingHashes(PhotoMatchCase):
    def test_paging_returns_every_hash_even_when_a_page_splits_one_listing(self):
        self.listing("a_1", [1, 2, 3])
        self.listing("b_2", [4, 5])
        paged = load_listing_hashes(self.conn, page_size=2)
        whole = load_listing_hashes(self.conn, page_size=1000)
        self.assertEqual(paged, whole)
        self.assertEqual({k: len(v) for k, v in paged.items()}, {"a_1": 3, "b_2": 2})

    def test_an_empty_table_gives_an_empty_answer(self):
        self.assertEqual(load_listing_hashes(self.conn), {})


class TestBatchHashes(PhotoMatchCase):
    def test_a_photo_is_downloaded_once_and_its_hash_is_stored(self):
        self.post([1, 2])
        self.conn.execute("UPDATE xray_photos SET dhash = ? WHERE message_id = 2", (hash_of(2),))
        self.conn.commit()
        hashes = asyncio.run(batch_hashes(self.conn, BATCH, self.download))
        self.assertEqual(sorted(hashes), sorted([hash_of(1), hash_of(2)]))
        self.assertEqual(self.downloads, ["f1"], "the stored hash needs no download")
        stored = self.conn.execute("SELECT dhash FROM xray_photos WHERE message_id = 1").fetchone()[
            "dhash"
        ]
        self.assertEqual(stored, hash_of(1))
        asyncio.run(batch_hashes(self.conn, BATCH, self.download))
        self.assertEqual(self.downloads, ["f1"], "a second read must use the stored hashes")

    def test_when_every_download_fails_the_job_fails(self):
        self.post([1, 2])
        self.files.clear()
        result = self.run_lookup()
        self.assertFalse(result["ok"])
        self.assertEqual(result["rows"], [])

    def test_an_unreadable_image_is_skipped(self):
        self.post([1, 2])
        self.files["f2"] = b"not an image at all" * 100
        hashes = asyncio.run(batch_hashes(self.conn, BATCH, self.download))
        self.assertEqual(hashes, [hash_of(1)])

    def test_a_fresh_batch_waits_for_the_rest_of_the_album(self):
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.post([1, 2], created_at=now)
        with mock.patch.object(photo_match, "ALBUM_SETTLE_SECONDS", 0.2):
            slept = []
            real_sleep = asyncio.sleep

            async def spy(seconds):
                slept.append(seconds)
                await real_sleep(0)

            with mock.patch.object(photo_match.asyncio, "sleep", spy):
                hashes = asyncio.run(batch_hashes(self.conn, BATCH, self.download))
        self.assertEqual(len(hashes), 2)
        self.assertEqual(len(slept), 1)
        self.assertGreater(slept[0], 0)

    def test_an_old_batch_does_not_wait(self):
        self.post([1])
        with mock.patch.object(photo_match.asyncio, "sleep", side_effect=AssertionError("waited")):
            asyncio.run(batch_hashes(self.conn, BATCH, self.download))


class TestDuplicateRule(unittest.TestCase):
    """The rule moved out of DuplicateDetector unchanged: two photos, or one photo that the
    price (within 25%) and the area (within 10%) confirm."""

    def test_the_rule(self):
        from core.dedup import is_photo_duplicate

        self.assertTrue(is_photo_duplicate(2, 0, 0, 0, 0))
        self.assertTrue(is_photo_duplicate(3, 100000, 10, 50, 90))
        self.assertTrue(is_photo_duplicate(1, 95000, 95000, 58, 58))
        self.assertTrue(is_photo_duplicate(1, 100000, 124000, 60, 66))
        self.assertFalse(is_photo_duplicate(1, 100000, 140000, 60, 60), "price 29% of the larger")
        self.assertFalse(is_photo_duplicate(1, 100000, 100000, 60, 70), "area 14% of the larger")
        self.assertFalse(is_photo_duplicate(0, 95000, 95000, 58, 58))

    def test_a_missing_value_cannot_confirm_a_single_photo(self):
        from core.dedup import is_photo_duplicate

        self.assertFalse(is_photo_duplicate(1, None, 95000, 58, 58))
        self.assertFalse(is_photo_duplicate(1, 95000, 95000, 0, 58))

    def test_count_image_matches_pairs_each_photo_once(self):
        from core.dedup import count_image_matches

        a, b = hash_of(1), hash_of(2)
        self.assertEqual(count_image_matches([a, a], [a]), 1)
        self.assertEqual(count_image_matches([a, b], [b, a]), 2)
        self.assertEqual(count_image_matches([a], [b]), 0)


if __name__ == "__main__":
    unittest.main()
