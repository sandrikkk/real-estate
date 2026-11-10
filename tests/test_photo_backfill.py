import asyncio
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from core.database import DatabaseEngine
from core.dedup import (
    NEW_MYHOME_PHOTO_HOST,
    OLD_MYHOME_PHOTO_HOST,
    _download_and_hash,
    current_photo_url,
)
from core.models import PropertyListing
from scripts.backfill_photo_hashes import run_backfill


def _make_listing(
    id: str, images: list, scraped_at: str = "2026-11-10T10:00:00Z"
) -> PropertyListing:
    return PropertyListing(
        id=id,
        source="myhome" if "myhome" in id else "ss_ge",
        source_id=id,
        title=f"Title {id}",
        description="Desc",
        price_usd=50000.0,
        area_m2=50.0,
        city="Tbilisi",
        district="Vake",
        url=f"https://example.com/{id}",
        images=images,
        deal_type="sale",
    )


class TestPhotoBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.tmp_dir.name, "test.db")
        self.db = DatabaseEngine(db_path=db_path, use_d1=False)
        self.db.init_db()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_current_photo_url_moved(self):
        """1. current_photo_url moved to core.dedup; accessible via core.dedup and core.xray_review."""
        from core.xray_review import (
            NEW_MYHOME_PHOTO_HOST as REVIEW_NEW,
            OLD_MYHOME_PHOTO_HOST as REVIEW_OLD,
            current_photo_url as review_current_photo_url,
        )

        old_url = f"https:{OLD_MYHOME_PHOTO_HOST}statement/123/img.jpg"
        expected = f"https:{NEW_MYHOME_PHOTO_HOST}statement/123/img.jpg"

        # From core.dedup
        self.assertEqual(current_photo_url(old_url), expected)
        # From core.xray_review
        self.assertEqual(review_current_photo_url(old_url), expected)
        self.assertEqual(OLD_MYHOME_PHOTO_HOST, REVIEW_OLD)
        self.assertEqual(NEW_MYHOME_PHOTO_HOST, REVIEW_NEW)

        # SS.ge and empty URLs remain untouched
        ss_url = "https://static.ss.ge/photos/123.jpg"
        self.assertEqual(current_photo_url(ss_url), ss_url)
        self.assertEqual(current_photo_url(""), "")

    def test_download_and_hash_requests_new_host(self):
        """2. _download_and_hash requests new host when given an old-host URL."""

        class FakeResponse:
            def __init__(self, content=b"fake_image_content_" * 100, status_code=200):
                self.content = content
                self.status_code = status_code

        class FakeSession:
            def __init__(self):
                self.requested_urls = []

            async def get(self, url, timeout=10):
                self.requested_urls.append(url)
                return FakeResponse()

        fake_session = FakeSession()
        old_url = "http://static-statements.tnet.ge/photos/apartment.jpg"
        expected_new_url = "http://static-api-statements.tnet.ge/photos/apartment.jpg"

        with patch("core.dedup._decode_dhash_sync", return_value="0123456789abcdef"):
            res = asyncio.run(_download_and_hash(old_url, fake_session))

        self.assertEqual(res, "0123456789abcdef")
        self.assertEqual(fake_session.requested_urls, [expected_new_url])

    def test_backfill_selection_and_writes(self):
        """3. Backfill on SQLite with 5 listings:

        - 2 already hashed, 1 in photo_hash_attempts, 1 whose fake download fails, 1 that succeeds.
        - only the last 2 are selected.
        - success writes 3 image_hashes rows; failure writes 1 photo_hash_attempts row.
        - --limit 1 processes 1.
        - --dry-run writes nothing.
        - stdout contains no 'http'.
        """
        # Populate 5 listings
        l1 = _make_listing("l1_hashed_a", ["https://ss.ge/1.jpg", "https://ss.ge/2.jpg"])
        l2 = _make_listing("l2_hashed_b", ["https://ss.ge/3.jpg"])
        l3 = _make_listing("l3_attempted", ["http://static-statements.tnet.ge/att.jpg"])
        l4 = _make_listing("l4_fail", ["http://static-statements.tnet.ge/fail.jpg"])
        l5 = _make_listing(
            "l5_success",
            [
                "http://static-statements.tnet.ge/s1.jpg",
                "http://static-statements.tnet.ge/s2.jpg",
                "http://static-statements.tnet.ge/s3.jpg",
            ],
        )

        self.db.save_listings_batch([l1, l2, l3, l4, l5])

        # Manually seed l1 and l2 into image_hashes
        with self.db._connection() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO image_hashes (listing_id, image_index, phash) VALUES (?, ?, ?)",
                ("l1_hashed_a", 0, "h" * 16),
            )
            c.execute(
                "INSERT INTO image_hashes (listing_id, image_index, phash) VALUES (?, ?, ?)",
                ("l2_hashed_b", 0, "h" * 16),
            )
            # Seed l3 into photo_hash_attempts
            c.execute(
                "INSERT INTO photo_hash_attempts (listing_id, tried_at) VALUES (?, ?)",
                ("l3_attempted", "2026-11-10T09:00:00Z"),
            )
            conn.commit()

        # Mock compute_hashes
        async def fake_compute_hashes(urls, max_images=3):
            if any("fail" in u for u in urls):
                return []
            return [f"hash_{i}" for i in range(len(urls[:max_images]))]

        # Case A: --limit 1 processes only 1 listing
        with patch(
            "scripts.backfill_photo_hashes._compute_hashes", side_effect=fake_compute_hashes
        ):
            stats_limit1 = asyncio.run(run_backfill(self.db, limit=1, dry_run=True))
        self.assertEqual(stats_limit1["selected"], 1)

        # Case B: --dry-run writes nothing
        with patch(
            "scripts.backfill_photo_hashes._compute_hashes", side_effect=fake_compute_hashes
        ):
            stats_dry = asyncio.run(run_backfill(self.db, limit=10, dry_run=True))

        self.assertEqual(stats_dry["selected"], 2)  # only l4 and l5
        self.assertEqual(stats_dry["hashed"], 1)  # l5
        self.assertEqual(stats_dry["failed"], 1)  # l4
        self.assertEqual(stats_dry["failed_by_host"]["old MyHome"], 1)

        # Check DB was NOT written to by dry-run
        with self.db._connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT COUNT(*) FROM image_hashes WHERE listing_id IN ('l4_fail', 'l5_success')"
            )
            self.assertEqual(c.fetchone()[0], 0)
            c.execute("SELECT COUNT(*) FROM photo_hash_attempts WHERE listing_id = 'l4_fail'")
            self.assertEqual(c.fetchone()[0], 0)

        # Case C: Real run writes to DB and stdout contains NO 'http'
        stdout_trap = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = stdout_trap
            with patch(
                "scripts.backfill_photo_hashes._compute_hashes", side_effect=fake_compute_hashes
            ):
                stats_real = asyncio.run(run_backfill(self.db, limit=10, dry_run=False))
        finally:
            sys.stdout = old_stdout

        captured_output = stdout_trap.getvalue()
        self.assertNotIn("http", captured_output.lower(), "stdout must not contain 'http'")

        # Assert selection and write counts
        self.assertEqual(stats_real["selected"], 2)
        self.assertEqual(stats_real["hashed"], 1)
        self.assertEqual(stats_real["failed"], 1)

        with self.db._connection() as conn:
            c = conn.cursor()
            # Success writes 3 image_hashes rows for l5
            c.execute(
                "SELECT phash FROM image_hashes WHERE listing_id = 'l5_success' ORDER BY image_index"
            )
            rows = c.fetchall()
            self.assertEqual(len(rows), 3)

            # Failure writes 1 photo_hash_attempts row for l4
            c.execute("SELECT listing_id FROM photo_hash_attempts WHERE listing_id = 'l4_fail'")
            attempts = c.fetchall()
            self.assertEqual(len(attempts), 1)

            # Remaining count is now 0
            c.execute("""
                SELECT COUNT(*) FROM properties p
                WHERE p.images_json IS NOT NULL AND p.images_json NOT IN ('', '[]')
                  AND NOT EXISTS (SELECT 1 FROM image_hashes h WHERE h.listing_id = p.id)
                  AND NOT EXISTS (SELECT 1 FROM photo_hash_attempts a WHERE a.listing_id = p.id)
            """)
            self.assertEqual(c.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
