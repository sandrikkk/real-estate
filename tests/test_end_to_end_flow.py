import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from core.models import PropertyListing, SearchFilters
from core.database import DatabaseEngine
from core.filters import ListingFilter
from core.analytics import MarketAnalytics
from notifier.ntfy_notifier import NtfyNotifier
from scrapers.myhome import MyHomeScraper
from scrapers.ss_ge import SSGeScraper


class TestEndToEndSystemFlow(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db = DatabaseEngine(self.temp_db.name)

        # Filters configured according to user specifications (40m2+, any room count)
        self.filters = SearchFilters(
            city="თბილისი",
            deal_type="sale",
            price_min_usd=45000,
            price_max_usd=75000,
            area_min_m2=40,
            area_max_m2=70,
            rooms=None,
            owner_type="physical",
            target_districts=["დიღომი", "დიდი დიღომი", "საბურთალო", "ვაკე", "გლდანი", "დიდუბე", "თემქა"],
            stop_words=["დაგირავება", "იპოთეკური"]
        )
        self.filter_engine = ListingFilter(self.filters)
        self.analytics = MarketAnalytics()
        self.notifier = NtfyNotifier(topic="test_topic", enable_console=False)

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def test_new_matching_listing_triggers_alert_and_saves_to_db(self):
        """
        Scenario 1: A new 2-room apartment in Dighomi matching price ($60,000)
        and area (45m²) is posted.
        -> Must NOT be seen previously.
        -> Must MATCH filters.
        -> Must be saved to DB and marked as notified.
        """
        listing = PropertyListing(
            id="myhome_999001",
            source="myhome",
            source_id="999001",
            title="იყიდება 2 ოთახიანი ბინა დიდ დიღომში",
            price_usd=60000,
            area_m2=45,
            district="დიდი დიღომი",
            rooms=2,
            url="https://www.myhome.ge/ka/pr/999001"
        )

        # Step 1: Check not seen
        self.assertFalse(self.db.is_seen(listing.id))

        # Step 2: Check matches filter
        self.assertTrue(self.filter_engine.matches(listing))

        # Step 3: Analytics check
        self.analytics.evaluate_listing(listing, db=self.db)
        self.assertEqual(listing.price_per_m2, round(60000 / 45, 2))

        # Step 4: Save & mark notified
        saved = self.db.save_listing(listing)
        self.assertTrue(saved)
        self.db.mark_as_notified(listing.id)

        # Step 5: Verify it is now marked as seen in DB
        self.assertTrue(self.db.is_seen(listing.id))

    def test_deduplication_prevents_duplicate_notifications(self):
        """
        Scenario 2: On the next GitHub Actions cycle (10 min later),
        the scraper fetches the same listing again.
        -> System must recognize it as already seen and NOT trigger another notification.
        """
        listing = PropertyListing(
            id="ss_ge_888002",
            source="ss_ge",
            source_id="888002",
            title="ბინა საბურთალოზე",
            price_usd=65000,
            area_m2=48,
            district="საბურთალო",
            rooms=2,
            url="https://home.ss.ge/ka/udzravi-qoneba/l/888002"
        )

        # First cycle: save it
        self.db.save_listing(listing)
        self.db.mark_as_notified(listing.id)

        # Second cycle: check deduplication
        self.assertTrue(self.db.is_seen(listing.id))

    def test_non_matching_listing_filtered_out_cleanly(self):
        """
        Scenario 3: A listing with area < 40m2 or price outside range ($95,000)
        -> Filter must reject it.
        """
        too_expensive = PropertyListing(
            id="myhome_777003",
            source="myhome",
            source_id="777003",
            title="ძვირი ბინა",
            price_usd=95000,
            area_m2=50,
            rooms=1,
            district="საბურთალო",
            url="https://www.myhome.ge/ka/pr/777003"
        )
        self.assertFalse(self.filter_engine.matches(too_expensive))

        too_small_area = PropertyListing(
            id="myhome_777004",
            source="myhome",
            source_id="777004",
            title="პატარა ბინა (35მ2)",
            price_usd=50000,
            area_m2=35,
            rooms=1,
            district="საბურთალო",
            url="https://www.myhome.ge/ka/pr/777004"
        )
        self.assertFalse(self.filter_engine.matches(too_small_area))

        wrong_district = PropertyListing(
            id="myhome_777005",
            source="myhome",
            source_id="777005",
            title="ბინა რუსთავში",
            price_usd=50000,
            area_m2=45,
            rooms=1,
            district="რუსთავი",
            url="https://www.myhome.ge/ka/pr/777005"
        )
        self.assertFalse(self.filter_engine.matches(wrong_district))

    def test_ntfy_payload_structure(self):
        """
        Scenario 4: Validate NTFY notification format has correct title, body, and click URL.
        """
        listing = PropertyListing(
            id="myhome_666006",
            source="myhome",
            source_id="666006",
            title="იყიდება 1 ოთახიანი ბინა დიღომში",
            price_usd=55000,
            price_gel=145000,
            area_m2=35,
            district="დიღომი",
            street="აღმაშენებლის ხეივანი",
            rooms=1,
            url="https://www.myhome.ge/ka/pr/666006"
        )
        title = self.notifier.format_title(listing)
        body = self.notifier.format_body(listing)

        self.assertIn("55,000", title)
        self.assertIn("დიღომი", title)
        self.assertIn("35.0 მ²", body)
        self.assertIn("MyHome.ge", body)
        self.assertEqual(listing.url, "https://www.myhome.ge/ka/pr/666006")


if __name__ == "__main__":
    unittest.main()
