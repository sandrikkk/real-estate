import unittest
import os
import tempfile
from core.models import PropertyListing, SearchFilters
from core.database import DatabaseEngine
from core.analytics import MarketAnalytics
from core.filters import ListingFilter
from notifier.ntfy_notifier import NtfyNotifier


class TestCoreComponents(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db = DatabaseEngine(self.temp_db.name)

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def test_property_listing_model(self):
        listing = PropertyListing(
            id="myhome_12345",
            source="myhome",
            source_id="12345",
            title="იყიდება 2 ოთახიანი ბინა",
            price_usd=60000,
            area_m2=50,
            city="თბილისი",
            district="საბურთალო",
            url="https://www.myhome.ge/ka/pr/12345"
        )
        self.assertEqual(listing.price_per_m2, 1200.0)
        self.assertEqual(listing.id, "myhome_12345")

    def test_database_deduplication(self):
        listing = PropertyListing(
            id="ss_ge_99999",
            source="ss_ge",
            source_id="99999",
            title="იყიდება ბინა ვაკეში",
            price_usd=150000,
            area_m2=75,
            city="თბილისი",
            district="ვაკე",
            url="https://home.ss.ge/ka/udzravi-qoneba/l/99999"
        )
        self.assertFalse(self.db.is_seen("ss_ge_99999"))
        saved = self.db.save_listing(listing)
        self.assertTrue(saved)
        self.assertTrue(self.db.is_seen("ss_ge_99999"))
        
        # Second save should be ignored (duplicate)
        duplicate_saved = self.db.save_listing(listing)
        self.assertFalse(duplicate_saved)

    def test_filter_matching_and_stop_words(self):
        filters = SearchFilters(
            price_min_usd=50000,
            price_max_usd=100000,
            area_min_m2=40,
            area_max_m2=100,
            target_districts=["საბურთალო", "ვაკე"],
            stop_words=["დაგირავება", "იპოთეკური"]
        )
        engine = ListingFilter(filters)

        # Valid listing
        valid_listing = PropertyListing(
            id="test_1",
            source="myhome",
            source_id="1",
            title="იყიდება ბინა საბურთალოზე",
            price_usd=75000,
            area_m2=60,
            district="საბურთალო",
            url="http://example.com/1"
        )
        self.assertTrue(engine.matches(valid_listing))

        # Stop-word listing
        stop_listing = PropertyListing(
            id="test_2",
            source="myhome",
            source_id="2",
            title="სასწრაფოდ დაგირავება საბურთალოზე",
            price_usd=60000,
            area_m2=50,
            district="საბურთალო",
            url="http://example.com/2"
        )
        self.assertFalse(engine.matches(stop_listing))

        # Room mismatch test
        room_filters = SearchFilters(rooms=[1])
        room_engine = ListingFilter(room_filters)
        two_room_listing = PropertyListing(
            id="test_3",
            source="myhome",
            source_id="3",
            title="2 ოთახიანი ბინა",
            price_usd=60000,
            area_m2=50,
            rooms=2,
            url="http://example.com/3"
        )
        self.assertFalse(room_engine.matches(two_room_listing))

    def test_market_analytics_bargain_detection(self):
        analytics = MarketAnalytics()

        # Listing in Saburtalo with $1000/m² (benchmark $1650/m²) -> ~39% discount -> Bargain!
        cheap_listing = PropertyListing(
            id="test_bargain",
            source="ss_ge",
            source_id="101",
            title="იაფად საბურთალოზე",
            price_usd=60000,
            area_m2=60, # $1000/m²
            district="საბურთალო",
            url="http://example.com/bargain"
        )
        evaluated = analytics.evaluate_listing(cheap_listing, discount_threshold_pct=15.0)
        self.assertTrue(evaluated.is_bargain)
        self.assertGreaterEqual(evaluated.discount_pct, 15.0)
        self.assertIn("🔥", evaluated.price_status_label)

    def test_database_district_stats_and_iqr(self):
        # Insert sample properties in Didi Dighomi
        sample_prices = [1000, 1100, 1200, 1300, 1400, 1500]
        for idx, p_m2 in enumerate(sample_prices):
            listing = PropertyListing(
                id=f"test_dighomi_{idx}",
                source="myhome",
                source_id=f"dig_{idx}",
                title=f"ბინა დიდ დიღომში {idx}",
                price_usd=p_m2 * 50,
                area_m2=50,
                rooms=2 if idx % 2 == 0 else 3,
                district="დიდი დიღომი",
                url=f"http://example.com/dig_{idx}"
            )
            self.db.save_listing(listing)

        stats = self.db.get_district_stats("დიდი დიღომი")
        self.assertIsNotNone(stats)
        self.assertEqual(stats.sample_count, 6)
        self.assertGreater(stats.median_price_per_m2, 0)
        self.assertIsNotNone(stats.iqr_p25)
        self.assertIsNotNone(stats.iqr_p75)
        self.assertIn(2, stats.room_medians)
        self.assertIn(3, stats.room_medians)

        # Test market overview report
        analytics = MarketAnalytics()
        report = analytics.format_market_report(self.db)
        self.assertIn("თბილისის უძრავი ქონების ბაზრის ანალიტიკა", report)
        self.assertIn("დიდი დიღომი", report)

    def test_ntfy_payload_formatting(self):
        notifier = NtfyNotifier(topic="test_topic")
        listing = PropertyListing(
            id="test_ntfy",
            source="myhome",
            source_id="555",
            title="იყიდება ბინა საბურთალოზე",
            price_usd=80000,
            area_m2=50,
            district="საბურთალო",
            street="ვაჟა-ფშაველას გამზ.",
            url="https://www.myhome.ge/ka/pr/555",
            images=["https://example.com/photo.jpg"]
        )
        title = notifier.format_title(listing)
        body = notifier.format_body(listing)
        self.assertIn("ახალი ბინა", title)
        self.assertIn("80,000", body)
        self.assertIn("საბურთალო", body)


    def test_myhome_price_label_exact_mapping(self):
        analytics = MarketAnalytics()
        test_cases = [
            ({"display_label": "top_price"}, "დაბალი ფასი", 1),
            ({"display_label": "good_price"}, "საშუალოზე იაფი", 2),
            ({"display_label": "middle_price"}, "საშუალო ფასი", 3),
            ({"display_label": "high_middle_price", "range_label": "high_middle_price_2"}, "საშუალოზე მაღალი", 4),
            ({"display_label": "high_price", "range_label": "high_price_1"}, "მაღალი ფასი", 5),
        ]
        for label, expected_name, expected_tier in test_cases:
            l = PropertyListing(
                id="t", source="myhome", source_id="1", title="t", price_usd=50000, area_m2=50, url="u"
            )
            analytics.compute_valuation_scale(l, label)
            self.assertEqual(l.valuation_scale_label, expected_name)
            self.assertEqual(l.valuation_scale_tier, expected_tier)


if __name__ == "__main__":
    unittest.main()
