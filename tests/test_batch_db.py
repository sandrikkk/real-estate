import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from core.database import DatabaseEngine, D1Client
from core.models import PropertyListing


class TestBatchDatabaseOperations(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        self.db.init_db()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _create_sample_listing(self, lid: str, price: float = 60000.0) -> PropertyListing:
        return PropertyListing(
            id=lid,
            source="myhome",
            source_id=lid.replace("myhome_", ""),
            title=f"Apartment {lid}",
            price_usd=price,
            area_m2=55.0,
            price_per_m2=price / 55.0,
            district="საბურთალო",
            rooms=2,
            url=f"https://myhome.ge/ka/pr/{lid}",
        )

    def test_filter_unnotified_listings_sqlite(self):
        """Should filter out listings already notified to the chat_id in 1 query."""
        chat_id = "12345"
        # Seed user notification
        self.db.mark_user_notified(chat_id, "listing_1")

        all_ids = ["listing_1", "listing_2", "listing_3"]
        unnotified = self.db.filter_unnotified_listings(chat_id, all_ids)

        self.assertEqual(unnotified, ["listing_2", "listing_3"])

    def test_filter_unnotified_listings_empty_input(self):
        """Should handle empty input gracefully."""
        self.assertEqual(self.db.filter_unnotified_listings("123", []), [])

    def test_save_listings_batch_sqlite(self):
        """Should insert multiple listings in batch and update in-memory seen_ids."""
        l1 = self._create_sample_listing("l1", 50000.0)
        l2 = self._create_sample_listing("l2", 60000.0)
        l3 = self._create_sample_listing("l3", 70000.0)

        saved_count = self.db.save_listings_batch([l1, l2, l3])
        self.assertEqual(saved_count, 3)

        # Confirm they are seen
        self.assertTrue(self.db.is_seen("l1"))
        self.assertTrue(self.db.is_seen("l2"))
        self.assertTrue(self.db.is_seen("l3"))

        # Re-saving same listings with INSERT OR IGNORE should return 0 new inserts
        re_saved = self.db.save_listings_batch([l1, l2])
        self.assertEqual(re_saved, 0)

    def test_d1_batch_operations_use_single_http_call(self):
        """D1 mode should send ONE batch query rather than N individual queries."""
        mock_client = MagicMock(spec=D1Client)
        # Mock D1 response for batch insert
        mock_client.batch.return_value = [{"meta": {"changes": 1}}, {"meta": {"changes": 1}}]
        # Mock D1 response for SELECT query
        mock_client.query.return_value = {
            "success": True,
            "results": [{"listing_id": "item_1"}],
            "meta": {},
        }

        d1_db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        d1_db.use_d1 = True
        d1_db.d1_client = mock_client

        # 1. Test filter_unnotified_listings sends 1 query
        unnotified = d1_db.filter_unnotified_listings("chat_99", ["item_1", "item_2", "item_3"])
        self.assertEqual(unnotified, ["item_2", "item_3"])
        self.assertEqual(mock_client.query.call_count, 1)

        # 2. Test save_listings_batch sends 1 batch call
        l1 = self._create_sample_listing("item_1")
        l2 = self._create_sample_listing("item_2")
        d1_db.save_listings_batch([l1, l2])
        self.assertEqual(mock_client.batch.call_count, 1)

    def test_filter_unnotified_chunks_at_50(self):
        """Should split inputs larger than 50 items into multiple safe chunks <= 50 params."""
        mock_client = MagicMock(spec=D1Client)
        mock_client.query.return_value = {"success": True, "results": [], "meta": {}}

        d1_db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        d1_db.use_d1 = True
        d1_db.d1_client = mock_client

        # 125 items -> chunks of 50, 50, 25 -> exactly 3 queries
        items = [f"item_{i}" for i in range(125)]
        d1_db.filter_unnotified_listings("chat_1", items)

        self.assertEqual(mock_client.query.call_count, 3)
        # Verify param length never exceeds 51 (1 for chat_id + 50 items)
        for call_args in mock_client.query.call_args_list:
            params = call_args[0][1]
            self.assertLessEqual(len(params), 51)

    def test_get_hashes_batch_chunks_at_50(self):
        """Should split dedup candidate hash lookups into safe chunks <= 50."""
        from core.dedup import DuplicateDetector

        mock_client = MagicMock(spec=D1Client)
        mock_client.query.return_value = {"success": True, "results": [], "meta": {}}

        d1_db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        d1_db.use_d1 = True
        d1_db.d1_client = mock_client

        detector = DuplicateDetector(db=d1_db)
        items = [f"item_{i}" for i in range(130)]
        res = detector._get_hashes_batch(items)

        self.assertEqual(len(res), 130)
        # 130 items / 50 = 3 queries (50, 50, 30)
        self.assertEqual(mock_client.query.call_count, 3)
        for call_args in mock_client.query.call_args_list:
            params = call_args[0][1]
            self.assertLessEqual(len(params), 50)

    def test_filter_new_listings_sqlite_and_d1_chunks(self):
        """Should filter out IDs already present in properties table and chunk at <= 50 params."""
        l1 = self._create_sample_listing("l1", 50000.0)
        self.db.save_listing(l1)
        # Simulate cold start in stateless CI runner
        self.db._seen_ids.clear()

        new_ids = self.db.filter_new_listings(["l1", "l2", "l3"])
        self.assertEqual(new_ids, ["l2", "l3"])
        self.assertIn("l1", self.db._seen_ids)

        # D1 chunking test (120 items -> 3 chunks of 50, 50, 20)
        mock_client = MagicMock(spec=D1Client)
        mock_client.query.return_value = {
            "success": True,
            "results": [{"id": "item_0"}],
            "meta": {},
        }
        d1_db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        d1_db.use_d1 = True
        d1_db.d1_client = mock_client

        items = [f"item_{i}" for i in range(120)]
        res = d1_db.filter_new_listings(items)
        self.assertNotIn("item_0", res)
        self.assertEqual(len(res), 119)
        self.assertEqual(mock_client.query.call_count, 3)
        for call_args in mock_client.query.call_args_list:
            params = call_args[0][1]
            self.assertLessEqual(len(params), 50)

    def test_save_listing_upserts_enriched_fields(self):
        """save_listing should update enriched fields if the listing already exists in properties."""
        listing = self._create_sample_listing("myhome_999", 46981.0)
        listing.is_owner = True
        listing.condition_id = None
        listing.phone_number = None

        self.db.save_listings_batch([listing])

        # Enrich the listing (e.g. after fetch_statement_details)
        listing.price_usd = 122100.0
        listing.price_per_m2 = round(122100.0 / listing.area_m2, 2)
        listing.is_owner = False
        listing.condition_id = 1
        listing.phone_number = "+995 555 12 34 56"
        listing.is_bargain = True

        self.db.save_listing(listing)

        with self.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT price_usd, is_owner, condition_id, phone_number, is_bargain FROM properties WHERE id = ?",
                ("myhome_999",),
            )
            rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(float(row["price_usd"]), 122100.0)
        self.assertEqual(int(row["is_owner"]), 0)
        self.assertEqual(int(row["condition_id"]), 1)
        self.assertEqual(row["phone_number"], "+995 555 12 34 56")
        self.assertEqual(int(row["is_bargain"]), 1)

    def test_classify_scraped_listings_detects_new_and_price_drops_only(self):
        l1 = PropertyListing(
            id="myhome_1",
            source="myhome",
            source_id="1",
            title="Apt 1",
            price_usd=70000.0,
            area_m2=55.0,
            url="https://myhome.ge/1",
        )
        l2 = PropertyListing(
            id="myhome_2",
            source="myhome",
            source_id="2",
            title="Apt 2",
            price_usd=65000.0,
            area_m2=50.0,
            url="https://myhome.ge/2",
        )
        self.db.save_listings_batch([l1, l2])

        # Cold-start simulation (clear in-memory caches as in stateless CI)
        self.db._seen_ids.clear()
        self.db._seen_prices.clear()

        l1_dropped = PropertyListing(
            id="myhome_1",
            source="myhome",
            source_id="1",
            title="Apt 1",
            price_usd=69999.0,  # $1 drop
            area_m2=55.0,
            url="https://myhome.ge/1",
        )
        l2_same = PropertyListing(
            id="myhome_2",
            source="myhome",
            source_id="2",
            title="Apt 2",
            price_usd=65000.0,  # unchanged
            area_m2=50.0,
            url="https://myhome.ge/2",
        )
        l3_new = PropertyListing(
            id="myhome_3",
            source="myhome",
            source_id="3",
            title="Apt 3",
            price_usd=60000.0,  # brand new
            area_m2=48.0,
            url="https://myhome.ge/3",
        )

        candidates, price_drops = self.db.classify_scraped_listings([l1_dropped, l2_same, l3_new])
        self.assertEqual([c.id for c in candidates], ["myhome_1", "myhome_3"])
        self.assertEqual(price_drops, {"myhome_1": 70000.0})
        self.assertEqual(l1_dropped.previous_price_usd, 70000.0)
        self.assertEqual(l1_dropped.price_drop_usd, 1.0)
        self.assertIsNone(l3_new.previous_price_usd)

    def test_initial_price_preserved_across_price_drop_and_increase(self):
        l1 = self._create_sample_listing("myhome_init_1", price=95000.0)
        l1.author_id = "user_777"
        l1.author_listings_count = 4
        self.db.save_listings_batch([l1])

        # Drop price to 88000
        l1_drop = self._create_sample_listing("myhome_init_1", price=88000.0)
        self.db.save_listings_batch([l1_drop])

        with self.db._connection() as conn:
            row = (
                conn.cursor()
                .execute(
                    "SELECT price_usd, initial_price_usd, author_id, author_listings_count FROM properties WHERE id = ?",
                    ("myhome_init_1",),
                )
                .fetchone()
            )
            self.assertEqual(float(row["price_usd"]), 88000.0)
            self.assertEqual(float(row["initial_price_usd"]), 95000.0)
            self.assertEqual(row["author_id"], "user_777")
            self.assertEqual(int(row["author_listings_count"]), 4)

        # Increase price to 99000 -> price_usd updates, initial_price_usd stays 95000, no sniper drop alert
        l1_inc = self._create_sample_listing("myhome_init_1", price=99000.0)
        candidates, drops = self.db.classify_scraped_listings([l1_inc])
        self.assertEqual(len(candidates), 0)
        self.assertEqual(drops, {})
        self.db.save_listings_batch([l1_inc])

        with self.db._connection() as conn:
            row = (
                conn.cursor()
                .execute(
                    "SELECT price_usd, initial_price_usd FROM properties WHERE id = ?",
                    ("myhome_init_1",),
                )
                .fetchone()
            )
            self.assertEqual(float(row["price_usd"]), 99000.0)
            self.assertEqual(float(row["initial_price_usd"]), 95000.0)

    def test_legacy_null_initial_price_backfilled_on_update(self):
        l1 = self._create_sample_listing("myhome_legacy_1", price=100000.0)
        self.db.save_listing(l1)
        # Simulate legacy row with initial_price_usd = NULL
        with self.db._connection() as conn:
            conn.cursor().execute(
                "UPDATE properties SET initial_price_usd = NULL WHERE id = ?",
                ("myhome_legacy_1",),
            )
            conn.commit()

        l1_updated = self._create_sample_listing("myhome_legacy_1", price=92000.0)
        self.db.save_listing(l1_updated)

        with self.db._connection() as conn:
            row = (
                conn.cursor()
                .execute(
                    "SELECT price_usd, initial_price_usd FROM properties WHERE id = ?",
                    ("myhome_legacy_1",),
                )
                .fetchone()
            )
            self.assertEqual(float(row["price_usd"]), 92000.0)
            self.assertEqual(float(row["initial_price_usd"]), 100000.0)

    def test_d1_client_adds_up_rows_read_and_written(self):
        responses = [
            {"success": True, "results": [], "meta": {"rows_read": 7, "rows_written": 0}},
            {
                "success": True,
                "batch_results": [
                    {"results": [], "meta": {"rows_read": 1, "rows_written": 2}},
                    {"results": [], "meta": {"rows_read": 0, "rows_written": 3}},
                ],
            },
            {"success": True, "results": [], "meta": {}},  # fields may be absent
        ]

        def fake_urlopen(req, timeout=None):
            resp = MagicMock()
            resp.read.return_value = json.dumps(responses.pop(0)).encode("utf-8")
            cm = MagicMock()
            cm.__enter__.return_value = resp
            cm.__exit__.return_value = False
            return cm

        client = D1Client("https://worker.example", "key")
        with patch("core.database.urllib.request.urlopen", side_effect=fake_urlopen):
            client.query("SELECT 1")
            client.batch([{"sql": "SELECT 1", "params": []}] * 2)
            client.query("SELECT 2")
        self.assertEqual((client.requests, client.rows_read, client.rows_written), (3, 8, 5))
        self.assertEqual(client.usage_line(), "[D1 usage]: 3 requests, 8 rows read, 5 rows written")

    def test_save_hashes_batch_d1_chunks_at_50(self):
        from core.dedup import DuplicateDetector

        mock_client = MagicMock(spec=D1Client)
        mock_client.batch.return_value = []
        d1_db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        d1_db.use_d1 = True
        d1_db.d1_client = mock_client

        detector = DuplicateDetector(d1_db)
        by_listing = {f"listing_{i}": [f"hash_{i}_{j}" for j in range(3)] for i in range(20)}
        detector.save_hashes_batch(by_listing)

        self.assertEqual(mock_client.query.call_count, 0)
        self.assertEqual(mock_client.batch.call_count, 2)
        for call_args in mock_client.batch.call_args_list:
            statements = call_args[0][0]
            self.assertLessEqual(len(statements), 50)
        self.assertEqual(len(mock_client.batch.call_args_list[0][0][0]), 50)
        self.assertEqual(len(mock_client.batch.call_args_list[1][0][0]), 10)


if __name__ == "__main__":
    unittest.main()
