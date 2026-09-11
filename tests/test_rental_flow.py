import unittest
from core.models import PropertyListing, SearchFilters, UserSubscription
from core.filters import ListingFilter
from notifier.telegram_bot import TelegramNotifier


class TestRentalFlow(unittest.TestCase):
    def setUp(self):
        self.filters = SearchFilters()
        self.filter_engine = ListingFilter(self.filters)
        self.notifier = TelegramNotifier()

    def test_property_listing_deal_types(self):
        sale_listing = PropertyListing(
            id="test_sale",
            source="myhome",
            source_id="101",
            deal_type="sale",
            title="იყიდება 2 ოთახიანი ბინა საბურთალოზე",
            price_usd=65000,
            area_m2=55,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/pr/101"
        )
        rent_listing = PropertyListing(
            id="test_rent",
            source="myhome",
            source_id="102",
            deal_type="rent",
            title="ქირავდება 2 ოთახიანი ბინა საბურთალოზე",
            price_usd=600,
            area_m2=55,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/pr/102"
        )
        self.assertEqual(sale_listing.deal_type, "sale")
        self.assertEqual(rent_listing.deal_type, "rent")

    def test_user_matches_deal_type_segregation(self):
        buyer = UserSubscription(
            chat_id="buyer_1",
            deal_type="sale",
            price_min_usd=50000,
            price_max_usd=80000,
            districts=["საბურთალო"]
        )
        renter = UserSubscription(
            chat_id="renter_1",
            deal_type="rent",
            price_min_usd=400,
            price_max_usd=800,
            districts=["საბურთალო"]
        )
        both_user = UserSubscription(
            chat_id="both_1",
            deal_type="both",
            price_min_usd=50000,
            price_max_usd=80000,
            rent_price_min_usd=400,
            rent_price_max_usd=800,
            districts=["საბურთალო"]
        )

        sale_apt = PropertyListing(
            id="s1",
            source="myhome",
            source_id="1",
            deal_type="sale",
            title="იყიდება ბინა",
            price_usd=60000,
            area_m2=50,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/pr/1"
        )
        rent_apt = PropertyListing(
            id="r1",
            source="ss_ge",
            source_id="2",
            deal_type="rent",
            title="ქირავდება ბინა",
            price_usd=550,
            area_m2=50,
            district="საბურთალო",
            rooms=2,
            url="https://home.ss.ge/pr/2"
        )

        # Buyer should match sale, not rent
        self.assertTrue(self.filter_engine.matches_user(buyer, sale_apt))
        self.assertFalse(self.filter_engine.matches_user(buyer, rent_apt))

        # Renter should match rent, not sale
        self.assertFalse(self.filter_engine.matches_user(renter, sale_apt))
        self.assertTrue(self.filter_engine.matches_user(renter, rent_apt))

        # Both user should match both!
        self.assertTrue(self.filter_engine.matches_user(both_user, sale_apt))
        self.assertTrue(self.filter_engine.matches_user(both_user, rent_apt))

    def test_telegram_message_formatting_rent_vs_sale(self):
        sale_apt = PropertyListing(
            id="s1",
            source="myhome",
            source_id="1",
            deal_type="sale",
            title="იყიდება ბინა",
            price_usd=60000,
            area_m2=50,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/pr/1"
        )
        rent_apt = PropertyListing(
            id="r1",
            source="ss_ge",
            source_id="2",
            deal_type="rent",
            title="ქირავდება ბინა",
            price_usd=550,
            area_m2=50,
            district="საბურთალო",
            rooms=2,
            url="https://home.ss.ge/pr/2"
        )

        sale_msg = self.notifier.format_message(sale_apt)
        self.assertIn("🏠", sale_msg)
        self.assertIn("$60,000", sale_msg)

        rent_msg = self.notifier.format_message(rent_apt)
        self.assertIn("🔑", rent_msg)
        self.assertIn("ქირავდება", rent_msg)
        self.assertIn("$550/თვე", rent_msg)

    def test_myhome_rent_api_url_and_normalization(self):
        from scrapers.myhome import MyHomeScraper
        scraper = MyHomeScraper()
        f = SearchFilters(deal_type="rent", rent_price_min_usd=500, rent_price_max_usd=650)
        api_url = scraper._build_api_url(f, page=1)
        self.assertIn("deal_types=2", api_url)

        # Test normalization of deal_type_id=2 (MyHome rent)
        raw_rent_item = {
            "id": 999999,
            "deal_type_id": 2,
            "dynamic_title": "ქირავდება 2 ოთახიანი ბინა ისანში",
            "price": {"2": {"price_total": 550}},
            "area": 55,
            "room": 2,
            "user_type": {"type": "physical"}
        }
        listing = scraper._normalize_item(raw_rent_item, filters=f)
        self.assertIsNotNone(listing)
        self.assertEqual(listing.deal_type, "rent")
        self.assertEqual(listing.price_usd, 550.0)
        self.assertTrue(listing.is_owner)

    def test_ss_ge_owner_filter_recognition(self):
        from scrapers.ss_ge import SSGeScraper
        scraper = SSGeScraper()
        f_owner = SearchFilters(deal_type="rent", owner_type="owner")
        url = scraper._build_search_url(f_owner, page=1)
        self.assertIn("individualType=1", url)

        # Even if raw SS item does not have isOwner field, filters.owner_type="owner" sets is_owner=True
        raw_ss_item = {
            "applicationId": 888888,
            "title": "ქირავდება 2 ოთახიანი ბინა სამგორში",
            "price": {"priceUsd": 550},
            "totalArea": 50,
            "numberOfBedrooms": 1,
            "detailUrl": "qiravdeba-2-otaxiani-bina-samgorshi-888888"
        }
        listing = scraper._normalize_item(raw_ss_item, filters=f_owner)
        self.assertIsNotNone(listing)
        self.assertEqual(listing.deal_type, "rent")
        self.assertTrue(listing.is_owner)

    def test_valeri_profile_matches_rent_listing(self):
        valeri = UserSubscription(
            chat_id="646957970",
            username="Q_Aston",
            deal_type="rent",
            price_min_usd=500,
            price_max_usd=650,
            districts=["ისანი", "სამგორი"],
            owner_type="owner"
        )
        rent_apt = PropertyListing(
            id="test_valeri",
            source="ss_ge",
            source_id="888888",
            deal_type="rent",
            title="ქირავდება 2 ოთახიანი ბინა სამგორში",
            price_usd=550,
            area_m2=50,
            district="სამგორი",
            rooms=2,
            is_owner=True,
            url="https://home.ss.ge/pr/888888"
        )
        self.assertTrue(self.filter_engine.matches_user(valeri, rent_apt))


if __name__ == "__main__":
    unittest.main()
