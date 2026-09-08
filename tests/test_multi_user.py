import unittest
from core.models import PropertyListing, SearchFilters, UserSubscription
from core.filters import ListingFilter
from core.user_sync import get_default_fallback_user, fetch_active_users


class TestMultiUserSubscription(unittest.TestCase):
    def setUp(self):
        self.base_filters = SearchFilters()
        self.filter_engine = ListingFilter(self.base_filters)

    def test_user_subscription_model(self):
        user = UserSubscription(
            chat_id="123456",
            username="test_buyer",
            price_min_usd=50000,
            price_max_usd=70000,
            area_min_m2=45,
            area_max_m2=60,
            districts=["დიდუბე", "ისანი"]
        )
        self.assertEqual(user.chat_id, "123456")
        self.assertEqual(user.price_min_usd, 50000)
        self.assertTrue(user.is_active)
        self.assertIn("დიდუბე", user.districts)

    def test_matches_user_criteria(self):
        user1 = UserSubscription(
            chat_id="111",
            price_min_usd=40000,
            price_max_usd=60000,
            area_min_m2=40,
            area_max_m2=65,
            districts=["დიდუბე"],
            is_active=True
        )

        user2 = UserSubscription(
            chat_id="222",
            price_min_usd=70000,
            price_max_usd=100000,
            area_min_m2=60,
            area_max_m2=90,
            districts=["ვაკე", "საბურთალო"],
            is_active=True
        )

        # Listing in Didube, $52,000, 50 m2
        listing_didube = PropertyListing(
            id="test_1",
            source="myhome",
            source_id="1",
            title="იყიდება 2 ოთახიანი ბინა დიდუბეში",
            price_usd=52000,
            area_m2=50,
            district="დიდუბე",
            rooms=2,
            url="https://myhome.ge/pr/1"
        )

        # Listing in Vake, $85,000, 75 m2
        listing_vake = PropertyListing(
            id="test_2",
            source="myhome",
            source_id="2",
            title="იყიდება 3 ოთახიანი ბინა ვაკეში",
            price_usd=85000,
            area_m2=75,
            district="ვაკე",
            rooms=3,
            url="https://myhome.ge/pr/2"
        )

        # User 1 should match Didube, but NOT Vake
        self.assertTrue(self.filter_engine.matches_user(user1, listing_didube))
        self.assertFalse(self.filter_engine.matches_user(user1, listing_vake))

        # User 2 should match Vake, but NOT Didube
        self.assertFalse(self.filter_engine.matches_user(user2, listing_didube))
        self.assertTrue(self.filter_engine.matches_user(user2, listing_vake))

    def test_inactive_user_never_matches(self):
        paused_user = UserSubscription(
            chat_id="333",
            price_min_usd=40000,
            price_max_usd=80000,
            is_active=False
        )
        listing = PropertyListing(
            id="test_3",
            source="myhome",
            source_id="3",
            title="ბინა",
            price_usd=50000,
            area_m2=55,
            district="დიდუბე",
            rooms=2,
            url="https://myhome.ge/pr/3"
        )
        self.assertFalse(self.filter_engine.matches_user(paused_user, listing))

    def test_hygiene_filtering(self):
        # Under construction
        under_construction = PropertyListing(
            id="test_uc",
            source="myhome",
            source_id="4",
            title="მშენებარე ბინა ახალ კორპუსში",
            price_usd=50000,
            area_m2=50,
            district="დიდუბე",
            rooms=2,
            url="https://myhome.ge/pr/4"
        )
        self.assertFalse(self.filter_engine.matches_hygiene(under_construction))

        # Black frame
        black_frame = PropertyListing(
            id="test_bf",
            source="myhome",
            source_id="5",
            title="ბინა შავი კარკასი",
            price_usd=50000,
            area_m2=50,
            condition_id=6,
            district="დიდუბე",
            rooms=2,
            url="https://myhome.ge/pr/5"
        )
        self.assertFalse(self.filter_engine.matches_hygiene(black_frame))

    def test_price_ceiling_strictly_respected(self):
        sandro = UserSubscription(
            chat_id="1105321687",
            price_min_usd=45000,
            price_max_usd=70000,
            area_min_m2=48,
            area_max_m2=65,
            rooms_min=1,
            districts=["დიდუბე", "ნაძალადევი", "ისანი", "გლდანი", "სამგორი"]
        )
        # $75,000 listing in Gldani
        listing_75k = PropertyListing(
            id="test_75k",
            source="myhome",
            source_id="25989830",
            title="იყიდება 2 ოთახიანი ბინა გლდანში",
            price_usd=75000,
            area_m2=52,
            district="გლდანი",
            rooms=2,
            url="https://myhome.ge/pr/25989830"
        )
        self.assertFalse(self.filter_engine.matches_user(sandro, listing_75k))

    def test_user_646957970_georgian_declension_matching(self):
        valeri = UserSubscription(
            chat_id="646957970",
            price_min_usd=50000,
            price_max_usd=100000,
            area_min_m2=40,
            area_max_m2=90,
            rooms_min=2,
            districts=["დიდუბე", "ისანი", "სამგორი"]
        )

        # 1. Listing with locative "ისანში" in title
        listing_isani = PropertyListing(
            id="test_isani",
            source="myhome",
            source_id="101",
            title="იყიდება 2 ოთახიანი ბინა ისანში",
            price_usd=65000,
            area_m2=55,
            district="ისანი",
            rooms=2,
            url="https://myhome.ge/pr/101"
        )
        self.assertTrue(self.filter_engine.matches_user(valeri, listing_isani))

        # 2. Listing with "სამგორში" and subdistrict "ისანი-სამგორი"
        listing_samgori = PropertyListing(
            id="test_samgori",
            source="myhome",
            source_id="102",
            title="იყიდება 2 ოთახიანი ბინა",
            price_usd=60000,
            area_m2=50,
            district="მოსკოვის გამზირი",
            subdistrict="ისანი-სამგორი",
            street="მოსკოვის გამზირი 7",
            rooms=2,
            url="https://myhome.ge/pr/102"
        )
        self.assertTrue(self.filter_engine.matches_user(valeri, listing_samgori))

        # 3. Listing with Didube in street/title when district is None
        listing_didube_title = PropertyListing(
            id="test_didube_title",
            source="myhome",
            source_id="103",
            title="იყიდება ბინა დიდუბეში მეტროსთან",
            price_usd=58000,
            area_m2=48,
            district=None,
            rooms=2,
            url="https://myhome.ge/pr/103"
        )
        self.assertTrue(self.filter_engine.matches_user(valeri, listing_didube_title))

        # 4. Listing in Vake should NOT match Valeri
        listing_vake = PropertyListing(
            id="test_vake",
            source="myhome",
            source_id="104",
            title="იყიდება ბინა ვაკეში",
            price_usd=70000,
            area_m2=50,
            district="ვაკე",
            rooms=2,
            url="https://myhome.ge/pr/104"
        )
        self.assertFalse(self.filter_engine.matches_user(valeri, listing_vake))


if __name__ == "__main__":
    unittest.main()

