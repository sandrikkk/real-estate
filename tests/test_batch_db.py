import os
import tempfile
import unittest
from unittest.mock import MagicMock
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


if __name__ == "__main__":
    unittest.main()
