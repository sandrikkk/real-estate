import unittest
from core.models import PropertyListing, SearchFilters, UserSubscription
from core.filters import ListingFilter
from notifier.telegram_bot import TelegramNotifier


class TestOwnerAgentFiltering(unittest.TestCase):
    def setUp(self):
        self.filters = SearchFilters()
        self.filter_engine = ListingFilter(self.filters)
        self.notifier = TelegramNotifier()

    def test_owner_only_user_never_matches_agent(self):
        valeri = UserSubscription(
            chat_id="valeri_owner_only",
            deal_type="rent",
            price_min_usd=500,
            price_max_usd=650,
            districts=["სამგორი"],
            owner_type="owner",
            is_active=True
        )

        agent_listing = PropertyListing(
            id="test_agent",
            source="myhome",
            source_id="26020564",
            deal_type="rent",
            title="ქირავდება 2 ოთახიანი ბინა სამგორში",
            price_usd=550,
            area_m2=50,
            district="სამგორი",
            rooms=2,
            is_owner=False,
            url="https://myhome.ge/pr/26020564"
        )

        self.assertFalse(self.filter_engine.matches_user(valeri, agent_listing))

    def test_owner_only_user_never_matches_unverified_owner(self):
        valeri = UserSubscription(
            chat_id="valeri_owner_only",
            deal_type="rent",
            price_min_usd=500,
            price_max_usd=650,
            districts=["სამგორი"],
            owner_type="owner",
            is_active=True
        )

        unverified_listing = PropertyListing(
            id="test_unverified",
            source="ss_ge",
            source_id="111",
            deal_type="rent",
            title="ქირავდება 2 ოთახიანი ბინა სამგორში",
            price_usd=550,
            area_m2=50,
            district="სამგორი",
            rooms=2,
            is_owner=None,
            url="https://home.ss.ge/pr/111"
        )

        self.assertFalse(self.filter_engine.matches_user(valeri, unverified_listing))

    def test_owner_only_user_matches_verified_owner(self):
        valeri = UserSubscription(
            chat_id="valeri_owner_only",
            deal_type="rent",
            price_min_usd=500,
            price_max_usd=650,
            districts=["სამგორი"],
            owner_type="owner",
            is_active=True
        )

        owner_listing = PropertyListing(
            id="test_owner",
            source="myhome",
            source_id="222",
            deal_type="rent",
            title="ქირავდება 2 ოთახიანი ბინა სამგორში",
            price_usd=550,
            area_m2=50,
            district="სამგორი",
            rooms=2,
            is_owner=True,
            url="https://myhome.ge/pr/222"
        )

        self.assertTrue(self.filter_engine.matches_user(valeri, owner_listing))

    def test_physical_user_type_with_is_owner_false_classified_as_agent(self):
        """
        MyHome listing where user_type is 'physical' (private account),
        but is_owner is False (actual agent like Tamuna ID 26020564).
        """
        listing = PropertyListing(
            id="tamuna_agent",
            source="myhome",
            source_id="26020564",
            deal_type="rent",
            title="ქირავდება ბინა საბურთალოზე",
            price_usd=600,
            area_m2=55,
            district="საბურთალო",
            rooms=2,
            user_type="physical",
            is_owner=False,
            url="https://myhome.ge/pr/26020564"
        )
        self.filter_engine.matches_hygiene(listing)
        self.assertFalse(listing.is_owner)

    def test_agent_text_keywords_override_is_owner_to_false(self):
        listing = PropertyListing(
            id="text_agent",
            source="ss_ge",
            source_id="333",
            deal_type="rent",
            title="ქირავდება ბინა",
            description="ვარ აგენტი, დამიკავშირდით მითითებულ ნომერზე",
            price_usd=600,
            area_m2=55,
            district="საბურთალო",
            rooms=2,
            user_type="physical",
            url="https://home.ss.ge/pr/333"
        )
        self.filter_engine.matches_hygiene(listing)
        self.assertFalse(listing.is_owner)

    def test_enrichment_overrides_initial_provisional_owner_and_drops_user(self):
        """
        End-to-end simulation of main.py:
        1. Item arrives from MyHome search API with user_type='physical' (initial guess is_owner=True).
        2. Client matches initially.
        3. Statement details are fetched, revealing is_owner=False.
        4. Client is re-verified and dropped!
        """
        valeri = UserSubscription(
            chat_id="valeri_owner_only",
            deal_type="rent",
            price_min_usd=500,
            price_max_usd=650,
            districts=["სამგორი"],
            owner_type="owner",
            is_active=True
        )

        listing = PropertyListing(
            id="provisional_myhome",
            source="myhome",
            source_id="26020564",
            deal_type="rent",
            title="ქირავდება 2 ოთახიანი ბინა სამგორში",
            price_usd=550,
            area_m2=50,
            district="სამგორი",
            rooms=2,
            user_type="physical",
            is_owner=True,  # Provisional from search list
            url="https://myhome.ge/pr/26020564"
        )

        # Before detail enrichment: initially matches
        matching_users = [valeri] if self.filter_engine.matches_user(valeri, listing) else []
        self.assertEqual(len(matching_users), 1)

        # Simulation of fetch_statement_details response from MyHome:
        details = {
            "id": 26020564,
            "is_owner": False,  # The real ground truth from statement details!
            "user_type": {"type": "physical"}
        }

        # Apply enrichment logic from main.py
        if "is_owner" in details and details["is_owner"] is not None:
            listing.is_owner = bool(details["is_owner"])

        # Re-verify matching users
        matching_users = [
            u for u in matching_users
            if self.filter_engine.matches_user(u, listing)
        ]

        # The agent listing MUST be dropped for the owner-only client!
        self.assertEqual(len(matching_users), 0)

    def test_telegram_message_owner_vs_agent_tags(self):
        owner_apt = PropertyListing(
            id="o1",
            source="myhome",
            source_id="1",
            deal_type="rent",
            title="ქირავდება ბინა",
            price_usd=600,
            area_m2=50,
            district="საბურთალო",
            is_owner=True,
            url="https://myhome.ge/pr/1"
        )
        agent_apt = PropertyListing(
            id="a1",
            source="myhome",
            source_id="2",
            deal_type="rent",
            title="ქირავდება ბინა",
            price_usd=600,
            area_m2=50,
            district="საბურთალო",
            is_owner=False,
            url="https://myhome.ge/pr/2"
        )

        msg_owner = self.notifier.format_message(owner_apt)
        self.assertIn("მესაკუთრე (Owner)", msg_owner)
        self.assertNotIn("სააგენტო (Agent)", msg_owner)

        msg_agent = self.notifier.format_message(agent_apt)
        self.assertIn("სააგენტო (Agent)", msg_agent)
        self.assertNotIn("მესაკუთრე (Owner)", msg_agent)


if __name__ == "__main__":
    unittest.main()
