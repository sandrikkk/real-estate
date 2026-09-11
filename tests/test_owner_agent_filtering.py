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

    def test_ss_ge_broker_listing_6395424_rejected_for_owner_only_subscriber(self):
        """
        Tests the exact reported incident:
        SS.ge listing 6395424 ($536, 62m2, Varketili).
        1. From search results it arrives with is_owner=None.
        2. Candidate matching with tentative=True allows it through for enrichment.
        3. Detail enrichment reveals userEntityType='Broker', agencyId=1310 ('ემჯი გრუპი').
        4. is_owner is set to False.
        5. Re-verification with tentative=False STRICTLY DROPS it for owner-only subscribers.
        """
        valeri = UserSubscription(
            chat_id="valeri_owner_only",
            deal_type="rent",
            price_min_usd=500,
            price_max_usd=700,
            districts=["ვარკეთილი", "სამგორი"],
            owner_type="owner",
            is_active=True
        )

        listing = PropertyListing(
            id="ss_ge_6395424",
            source="ss_ge",
            source_id="6395424",
            deal_type="rent",
            title="ქირავდება 2 ოთახიანი ბინა ვარკეთილში",
            price_usd=536,
            area_m2=62,
            district="ვარკეთილი",
            rooms=2,
            is_owner=None,  # Unverified on initial search list
            url="https://home.ss.ge/ka/udzravi-qoneba/qiravdeba-2-otaxiani-bina-varketilshi-6395424"
        )

        # Step 2: Tentative candidate matching permits enrichment
        self.assertTrue(self.filter_engine.matches_user(valeri, listing, tentative=True))

        # Step 3: Simulation of SS.ge statement details enrichment
        ss_details = {
            "is_owner": False,
            "agency_id": 1310,
            "agency_name": "ემჯი გრუპი",
            "user_entity_type": "broker",
            "phone_number": "511153164"
        }
        listing.is_owner = ss_details["is_owner"]
        listing.phone_number = ss_details["phone_number"]

        # Step 4: Strict re-verification drops the broker listing!
        self.assertFalse(self.filter_engine.matches_user(valeri, listing, tentative=False))

    def test_ss_ge_real_individual_owner_accepted(self):
        """
        A genuine owner on SS.ge (userEntityType: Individual, no company/broker tags).
        """
        valeri = UserSubscription(
            chat_id="valeri_owner_only",
            deal_type="rent",
            price_min_usd=500,
            price_max_usd=700,
            districts=["საბურთალო"],
            owner_type="owner",
            is_active=True
        )

        listing = PropertyListing(
            id="ss_ge_real_owner",
            source="ss_ge",
            source_id="36275470",
            deal_type="rent",
            title="ქირავდება 2 ოთახიანი ბინა საბურთალოზე",
            price_usd=600,
            area_m2=55,
            district="საბურთალო",
            rooms=2,
            is_owner=None,
            url="https://home.ss.ge/ka/udzravi-qoneba/36275470"
        )

        # Candidate matching passes
        self.assertTrue(self.filter_engine.matches_user(valeri, listing, tentative=True))

        # Detail enrichment verifies owner
        ss_details = {
            "is_owner": True,
            "agency_id": None,
            "agency_name": None,
            "user_entity_type": "individual",
            "phone_number": "598424040"
        }
        listing.is_owner = ss_details["is_owner"]

        # Re-verification passes
        self.assertTrue(self.filter_engine.matches_user(valeri, listing, tentative=False))

    def test_ss_ge_unverified_listing_dropped_on_strict_check(self):
        """
        If SS.ge statement details could not be resolved (is_owner remains None),
        owner-only subscribers must NOT receive it.
        """
        valeri = UserSubscription(
            chat_id="valeri_owner_only",
            deal_type="rent",
            price_min_usd=500,
            price_max_usd=700,
            districts=["ვარკეთილი"],
            owner_type="owner",
            is_active=True
        )

        unverified = PropertyListing(
            id="ss_ge_unverified",
            source="ss_ge",
            source_id="999999",
            deal_type="rent",
            title="ქირავდება 2 ოთახიანი ბინა ვარკეთილში",
            price_usd=550,
            area_m2=55,
            district="ვარკეთილი",
            rooms=2,
            is_owner=None,
            url="https://home.ss.ge/ka/udzravi-qoneba/999999"
        )

        # Tentative passes
        self.assertTrue(self.filter_engine.matches_user(valeri, unverified, tentative=True))

        # Strict check drops it!
        self.assertFalse(self.filter_engine.matches_user(valeri, unverified, tentative=False))

    def test_ss_ge_broker_message_shows_agent_badge_for_all_subscribers(self):
        """
        If a user has owner_type='all', broker listings are delivered with
        '👤 სააგენტო (Agent)' badge, NOT '👤 მესაკუთრე (Owner)'.
        """
        all_user = UserSubscription(
            chat_id="user_all",
            deal_type="rent",
            price_min_usd=500,
            price_max_usd=700,
            districts=["ვარკეთილი"],
            owner_type="all",
            is_active=True
        )

        listing = PropertyListing(
            id="ss_ge_6395424",
            source="ss_ge",
            source_id="6395424",
            deal_type="rent",
            title="ქირავდება 2 ოთახიანი ბინა ვარკეთილში",
            price_usd=536,
            area_m2=62,
            district="ვარკეთილი",
            rooms=2,
            is_owner=False,  # Enriched as broker
            url="https://home.ss.ge/ka/udzravi-qoneba/qiravdeba-2-otaxiani-bina-varketilshi-6395424"
        )

        self.assertTrue(self.filter_engine.matches_user(all_user, listing, tentative=False))
        msg = self.notifier.format_message(listing)
        self.assertIn("სააგენტო (Agent)", msg)
        self.assertNotIn("მესაკუთრე (Owner)", msg)


if __name__ == "__main__":
    unittest.main()

