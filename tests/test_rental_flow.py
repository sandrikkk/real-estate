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


if __name__ == "__main__":
    unittest.main()
