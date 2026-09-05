import unittest
from core.models import PropertyListing, SearchFilters
from core.filters import ListingFilter


def make_sample_listing(**kwargs):
    defaults = {
        "id": "myhome_12345",
        "source": "myhome",
        "source_id": "12345",
        "title": "იყიდება 2 ოთახიანი ბინა ისანში",
        "description": "კარგი ბინა, გარემონტებული, ავეჯით",
        "price_usd": 60000.0,
        "area_m2": 50.0,
        "rooms": 2,
        "bedrooms": 1,
        "city": "თბილისი",
        "district": "ისანი",
        "street": "ნავთლუღის ქ.",
        "url": "https://www.myhome.ge/ka/pr/12345",
        "status_id": 2,          # New built
        "condition_id": 1,       # Newly renovated
        "condition_name": "ახალი გარემონტებული",
        "metro_station_id": 16,  # Isani
        "metro_station_name": "ისანი",
        "user_type": "physical",
        "is_owner": True,
        "phone_number": "+995 599 12 34 56",
    }
    defaults.update(kwargs)
    return PropertyListing(**defaults)


class TestFiltersValidation(unittest.TestCase):
    def setUp(self):
        self.filters = SearchFilters()
        self.filter_engine = ListingFilter(self.filters)

    def test_valid_listing_passes_and_tags_owner(self):
        listing = make_sample_listing()
        self.assertTrue(self.filter_engine.matches(listing))
        self.assertTrue(listing.is_owner)
        self.assertEqual(listing.price_per_m2, 1200.0)
        self.assertTrue(listing.is_hot_deal)
        self.assertEqual(listing.deal_tag, "🚨 HOT DEAL (RENOVATED)")

    def test_under_construction_status_excluded(self):
        # status_id 3 is "მშენებარე"
        listing1 = make_sample_listing(status_id=3)
        self.assertFalse(self.filter_engine.matches(listing1))

        # "მშენებარე" in description
        listing2 = make_sample_listing(status_id=2, description="კომპლექსი არის მშენებარე")
        self.assertFalse(self.filter_engine.matches(listing2))

    def test_black_frame_excluded(self):
        # condition_id 6 is "შავი კარკასი"
        listing1 = make_sample_listing(condition_id=6, condition_name="შავი კარკასი")
        self.assertFalse(self.filter_engine.matches(listing1))

        # Mentioned in description
        listing2 = make_sample_listing(condition_id=1, description="ბინა ბარდება შავი კარკასი კონდიციით")
        self.assertFalse(self.filter_engine.matches(listing2))

    def test_frame_price_limit_and_tag(self):
        # White frame at $53,000 (<= $54,000) -> Allowed
        listing_white = make_sample_listing(
            condition_id=5,
            condition_name="თეთრი კარკასი",
            price_usd=53000.0,
            area_m2=52.0
        )
        self.assertTrue(self.filter_engine.matches(listing_white))

        # Green frame at $50,000, 50m² -> $1000/m² <= 1050 -> Tagged as VALUE FRAME (<$54k)
        listing_green_val = make_sample_listing(
            condition_id=7,
            condition_name="მწვანე კარკასი",
            price_usd=50000.0,
            area_m2=50.0
        )
        self.assertTrue(self.filter_engine.matches(listing_green_val))
        self.assertEqual(listing_green_val.deal_tag, "🔥 VALUE FRAME (<$54k)")

        # Frame at $55,000 (> $54,000) -> Rejected
        listing_rejected = make_sample_listing(
            condition_id=5,
            condition_name="თეთრი კარკასი",
            price_usd=55000.0,
            area_m2=50.0
        )
        self.assertFalse(self.filter_engine.matches(listing_rejected))

    def test_stop_words_in_text(self):
        stop_words_to_test = [
            "ჩაბარდება 2026 წლის ბოლოს",
            "პროექტი ბარდება მალე",
            "ჩაბარების თარიღი: 2027",
            "2028 წელს",
            "სართული დაშენება ნებართვით",
            "დაშენების პერსპექტივით",
            "იტალიური ეზო",
            "ნახევარსარდაფი",
            "სარდაფი შედის ფასში",
        ]

        for sw in stop_words_to_test:
            listing = make_sample_listing(description=f"იყიდება ბინა. {sw}")
            self.assertFalse(self.filter_engine.matches(listing), f"Failed to reject stop-word: {sw}")

    def test_minimum_two_rooms_rule(self):
        # 1 room studio -> Rejected
        studio = make_sample_listing(rooms=1, bedrooms=0)
        self.assertFalse(self.filter_engine.matches(studio))

        # 2 rooms (1 bedroom + living) -> Allowed
        two_rooms = make_sample_listing(rooms=2, bedrooms=1)
        self.assertTrue(self.filter_engine.matches(two_rooms))

    def test_strict_location_blacklist(self):
        blacklisted_samples = [
            {"district": "დიდი დიღომი"},
            {"district": "მუხიანი"},
            {"district": "აფრიკა"},
            {"district": "დამპალო"},
            {"street": "ზემო პლატო მე-3"},
            {"description": "ბინა მდებარეობს ორთაჭალის ზემოთ"},
            {"district": "ისანი", "street": "ბერი გაბრიელ სალოსის გამზირი"},
            {"district": "ისანი", "street": "ბოგდან ხმელნიცკის ქ."},
            {"district": "ლილო"},
            {"street": "დიდი ლილო"},
        ]

        for item in blacklisted_samples:
            listing = make_sample_listing(**item)
            self.assertFalse(self.filter_engine.matches(listing), f"Failed to reject blacklisted location: {item}")

    def test_gldani_microdistricts_rule(self):
        # Gldani Microdistrict 1 & 2 -> Allowed
        gldani_1 = make_sample_listing(district="გლდანი", street="გლდანის I მ/რ")
        self.assertTrue(self.filter_engine.matches(gldani_1))

        gldani_2 = make_sample_listing(district="გლდანი", street="გლდანი - მე-2 მ/რ")
        self.assertTrue(self.filter_engine.matches(gldani_2))

        # Gldani Microdistricts 3 to 8 -> Rejected
        for mr in ["3 მ/რ", "4-ე მ/რ", "5 მ/რ", "7 მ/რ", "VIII მ/რ"]:
            gldani_deep = make_sample_listing(district="გლდანი", street=f"გლდანის {mr}")
            self.assertFalse(self.filter_engine.matches(gldani_deep), f"Should reject {mr}")

    def test_target_metro_stations(self):
        # Target metro IDs: [1, 7, 9, 10, 16, 17, 18, 19, 21, 22, 24, 25]
        for mid in [1, 7, 9, 10, 16, 17, 18, 19, 21, 22, 24, 25]:
            listing = make_sample_listing(metro_station_id=mid, district="სხვა უბანი")
            self.assertTrue(self.filter_engine.matches(listing), f"Target metro {mid} should be accepted")

        # Non-target metro: Varketili (23) -> Should be rejected
        listing_varketili = make_sample_listing(metro_station_id=23, district="ვარკეთილი")
        self.assertFalse(self.filter_engine.matches(listing_varketili))

    def test_district_whitelist_fallback(self):
        # No metro station, but district is Didube -> Accepted
        listing_didube = make_sample_listing(metro_station_id=None, district="დიდუბე")
        self.assertTrue(self.filter_engine.matches(listing_didube))

        # No metro station, and district is unrelated (e.g. Saburtalo center, not in whitelist) -> Rejected
        listing_other = make_sample_listing(metro_station_id=None, district="ვაკე", street="ჭავჭავაძე")
        self.assertFalse(self.filter_engine.matches(listing_other))

    def test_hot_deal_flagging(self):
        # Price $65,000, 50m² -> $1300/m² <= 1350, Renovated -> HOT DEAL (RENOVATED)
        listing_hot = make_sample_listing(price_usd=65000.0, area_m2=50.0, condition_id=1)
        self.assertTrue(self.filter_engine.matches(listing_hot))
        self.assertTrue(listing_hot.is_hot_deal)
        self.assertEqual(listing_hot.deal_tag, "🚨 HOT DEAL (RENOVATED)")

        # Price $71,000, 50m² -> $1420/m² > 1350, Renovated -> Passes filter but no deal tag
        listing_normal = make_sample_listing(price_usd=71000.0, area_m2=50.0, condition_id=1)
        self.assertTrue(self.filter_engine.matches(listing_normal))
        self.assertFalse(listing_normal.is_hot_deal)
        self.assertIsNone(listing_normal.deal_tag)


if __name__ == "__main__":
    unittest.main()
