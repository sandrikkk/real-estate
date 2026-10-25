import unittest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from core.models import PropertyListing, UserSubscription
from core.database import DatabaseEngine
from core.analytics import MarketAnalytics
from core.dedup import DuplicateDetector
from notifier.telegram_bot import TelegramNotifier
from scrapers.myhome import MyHomeScraper


import tempfile
import os


class TestFixes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        self.db.init_db()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_myhome_ssr_extract_statement_details(self):
        """Issue 1: Test extracting statement details from __NEXT_DATA__ SSR HTML."""
        scraper = MyHomeScraper()
        mock_statement = {
            "id": 26345700,
            "condition_id": 1,
            "is_owner": False,
            "user_phone_number": "599068***",
            "price_label": {"range_label": "top_price_1", "display_label": "top_price"},
            "user_type": {"type": "agent"},
        }
        mock_html = f"""
        <html>
        <head>
        <script id="__NEXT_DATA__" type="application/json">
        {{
            "props": {{
                "pageProps": {{
                    "dehydratedState": {{
                        "queries": [
                            {{
                                "state": {{
                                    "data": {{
                                        "data": {{
                                            "statement": {json.dumps(mock_statement)}
                                        }}
                                    }}
                                }}
                            }}
                        ]
                    }}
                }}
            }}
        }}
        </script>
        </head>
        </html>
        """
        st = scraper.extract_statement_from_html(mock_html)
        self.assertIsNotNone(st)
        self.assertEqual(st.get("id"), 26345700)
        self.assertEqual(st.get("condition_id"), 1)
        self.assertEqual(st.get("is_owner"), False)
        self.assertEqual(st.get("price_label", {}).get("display_label"), "top_price")

    def test_myhome_valuation_scale_labeling(self):
        """Issue 1: Official MyHome label maps top_price to 'დაბალი ფასი'."""
        analytics = MarketAnalytics()
        listing = PropertyListing(
            id="test_1",
            source="myhome",
            source_id="26345700",
            title="Nadzaladevi apt",
            price_usd=63700.0,
            area_m2=55.0,
            price_per_m2=1158.0,
            district="ნაძალადევი",
            rooms=3,
            url="https://www.myhome.ge/ka/pr/26345700",
        )
        # With official MyHome label:
        price_label = {"range_label": "top_price_1", "display_label": "top_price"}
        analytics.evaluate_listing(listing, myhome_price_label=price_label)
        self.assertEqual(listing.valuation_scale_label, "დაბალი ფასი")
        self.assertEqual(listing.valuation_scale_tier, 1)
        self.assertEqual(listing.valuation_scale_source, "myhome")

        # In telegram message, official should say 'MyHome შეფასება:'
        notifier = TelegramNotifier()
        msg = notifier.format_message(listing)
        self.assertIn("MyHome შეფასება:", msg)
        self.assertIn("დაბალი ფასი", msg)

        # Without official MyHome label (fallback to market benchmark):
        listing2 = PropertyListing(
            id="test_2",
            source="ss_ge",
            source_id="12345",
            title="Nadzaladevi apt 2",
            price_usd=63700.0,
            area_m2=55.0,
            price_per_m2=1158.0,
            district="ნაძალადევი",
            rooms=3,
            url="https://home.ss.ge/ka/12345",
        )
        analytics.evaluate_listing(listing2, myhome_price_label=None)
        self.assertEqual(listing2.valuation_scale_source, "market")
        msg2 = notifier.format_message(listing2)
        # Should NOT claim to be MyHome's evaluation!
        self.assertNotIn("MyHome შეფასება:", msg2)
        self.assertIn("საბაზრო შეფასება:", msg2)

    def test_user_sync_cache_on_429_error(self):
        """Issue 2: On HTTP 429 error, user_sync must use cached active users instead of resetting filters."""
        from core.user_sync import fetch_active_users, save_cached_users

        # Save an active user with custom filter ($45k - $75k) into cache
        cached_user = UserSubscription(
            chat_id="1105321687",
            username="sandro",
            price_min_usd=45000.0,
            price_max_usd=75000.0,
            districts=["დიდუბე", "ნაძალადევი"],
            is_active=True,
        )
        save_cached_users([cached_user])

        # Simulate HTTP 429 when fetching from worker
        with patch("urllib.request.urlopen") as mock_url:
            import urllib.error

            mock_url.side_effect = urllib.error.HTTPError(
                "https://worker.dev/api/users", 429, "Too Many Requests", {}, None
            )
            users = fetch_active_users(worker_url="https://worker.dev", sync_key="test")
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].price_min_usd, 45000.0)
            self.assertEqual(users[0].price_max_usd, 75000.0)
            self.assertEqual(users[0].districts, ["დიდუბე", "ნაძალადევი"])

    async def test_duplicate_strict_lowest_price_logic(self):
        """Issue 3: Strict lowest price vs equal price vs higher price."""
        detector = DuplicateDetector(db=self.db, hash_threshold=10)

        # Insert listing 1 ($70,000, Agent)
        l1 = PropertyListing(
            id="prop_1",
            source="myhome",
            source_id="1",
            title="Apt 1",
            price_usd=70000.0,
            area_m2=50.0,
            price_per_m2=1400.0,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/1",
            phone_number="+995 555 11 11 11",
            is_owner=False,
            user_type="agent",
        )
        self.db.save_listing(l1)

        # Insert listing 2 ($65,000, Owner) in same group
        l2 = PropertyListing(
            id="prop_2",
            source="ss_ge",
            source_id="2",
            title="Apt 2",
            price_usd=65000.0,
            area_m2=50.0,
            price_per_m2=1300.0,
            district="საბურთალო",
            rooms=2,
            url="https://ss.ge/2",
            phone_number="+995 577 22 22 22",
            is_owner=True,
            user_type="physical",
        )
        self.db.save_listing(l2)

        # Mock image hashes to match
        detector._get_hashes = MagicMock(return_value=["0000000000000000"])
        detector._ensure_hashes = AsyncMock(return_value=["0000000000000000"])

        # Check listing 2: it drops price from $70k to $65k
        info2 = await detector.check_listing(l2)
        self.assertIsNotNone(info2)
        self.assertTrue(info2.get("is_strictly_lower"))
        self.assertEqual(info2.get("best_price_usd"), 65000.0)
        # Owner phone should be listing 2's phone (+995 577 22 22 22) because is_owner=True
        self.assertEqual(info2.get("probable_owner_phone"), "+995 577 22 22 22")

        # Now listing 3 arrives: also $65,000, from another Agent
        l3 = PropertyListing(
            id="prop_3",
            source="myhome",
            source_id="3",
            title="Apt 3",
            price_usd=65000.0,
            area_m2=50.0,
            price_per_m2=1300.0,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/3",
            phone_number="+995 599 33 33 33",
            is_owner=False,
            user_type="agent",
        )
        self.db.save_listing(l3)

        info3 = await detector.check_listing(l3)
        self.assertIsNotNone(info3)
        # It is EQUAL, not strictly lower!
        self.assertFalse(info3.get("is_strictly_lower"))

        # In telegram notification formatting for l3 (price=65k, best=65k):
        notifier = TelegramNotifier()
        msg3 = notifier.format_message(l3, dup_info=info3)
        # Header should NOT say "ახალი ყველაზე დაბალი ფასი", should say "ნაპოვნია დუბლიკატი"
        self.assertNotIn("ახალი ყველაზე დაბალი ფასი", msg3)
        self.assertIn("ნაპოვნია დუბლიკატი", msg3)
        # Should NOT say "ყველაზე იაფი: $65,000 (ლინკი)" because l3 is already $65,000
        self.assertNotIn("ყველაზე იაფი: <b>$65,000</b> (<a", msg3)
        # Should display probable owner phone as +995 577 22 22 22 (from l2), NOT +995 599 33 33 33
        self.assertIn("+995 577 22 22 22", msg3)

    async def test_duplicate_first_owner_higher_price_shows_cheaper_link(self):
        """Verify that when a verified owner posts at a higher price than an agent's listing,
        it flags is_first_owner=True, displays 'მესაკუთრის განცხადება', and includes
        'ყველაზე იაფი: $XX,XXX (ლინკი)' pointing to the cheaper agent listing."""
        detector = DuplicateDetector(db=self.db, hash_threshold=10)

        # Listing 1: Agent @ $70,000
        l1 = PropertyListing(
            id="agent_1",
            source="myhome",
            source_id="201",
            title="Agent 1",
            price_usd=70000.0,
            area_m2=50.0,
            price_per_m2=1400.0,
            district="ისანი",
            rooms=2,
            url="https://myhome.ge/201",
            phone_number="+995 595 11 11 11",
            is_owner=False,
            user_type="agent",
        )
        self.db.save_listing(l1)

        # Listing 2: Agent @ $65,000 (cheapest)
        l2 = PropertyListing(
            id="agent_2",
            source="ss_ge",
            source_id="202",
            title="Agent 2",
            price_usd=65000.0,
            area_m2=50.0,
            price_per_m2=1300.0,
            district="ისანი",
            rooms=2,
            url="https://ss.ge/202",
            phone_number="+995 595 22 22 22",
            is_owner=False,
            user_type="agent",
        )
        self.db.save_listing(l2)

        # Listing 3: Verified Owner @ $68,000 (higher than $65,000 agent, but first time owner posts)
        l3 = PropertyListing(
            id="owner_1",
            source="myhome",
            source_id="203",
            title="Owner Apt",
            price_usd=68000.0,
            area_m2=50.0,
            price_per_m2=1360.0,
            district="ისანი",
            rooms=2,
            url="https://myhome.ge/203",
            phone_number="+995 577 88 88 88",
            is_owner=True,
            user_type="physical",
        )
        self.db.save_listing(l3)

        # Mock image hashes to match across all 3
        detector._get_hashes_batch = MagicMock(
            return_value={"agent_1": ["0000000000000000"], "agent_2": ["0000000000000000"]}
        )
        detector._ensure_hashes = AsyncMock(return_value=["0000000000000000"])

        info3 = await detector.check_listing(l3)
        self.assertIsNotNone(info3)
        self.assertTrue(info3["is_first_owner"])
        self.assertTrue(info3["is_new_best_price"])
        self.assertFalse(info3["is_strictly_lower"])
        self.assertEqual(info3["best_price_usd"], 65000.0)
        self.assertEqual(info3["best_price_url"], "https://ss.ge/202")
        self.assertEqual(info3["probable_owner_phone"], "+995 577 88 88 88")

        notifier = TelegramNotifier()
        msg3 = notifier.format_message(l3, dup_info=info3)

        # Must have "მესაკუთრის განცხადება!"
        self.assertIn("მესაკუთრის განცხადება!", msg3)
        self.assertNotIn("ახალი ყველაზე დაბალი ფასი!", msg3)
        # MUST have "ყველაზე იაფი: $65,000 (ლინკი)" pointing to agent_2!
        self.assertIn("ყველაზე იაფი: <b>$65,000</b>", msg3)
        self.assertIn('href="https://ss.ge/202"', msg3)
        # Must show owner's phone
        self.assertIn("+995 577 88 88 88", msg3)

    async def test_no_owner_phone_if_all_agents(self):
        """Issue 3: If all duplicate listings are from agents, never label an agent as owner."""
        detector = DuplicateDetector(db=self.db, hash_threshold=10)

        # Listing 1 (Agent 1)
        l1 = PropertyListing(
            id="a_1",
            source="myhome",
            source_id="11",
            title="Agent Apt 1",
            price_usd=80000.0,
            area_m2=60.0,
            price_per_m2=1333.0,
            district="ვაკე",
            rooms=2,
            url="https://myhome.ge/11",
            phone_number="+995 595 25 97 97",
            is_owner=False,
            user_type="agent",
        )
        self.db.save_listing(l1)

        # Listing 2 (Agent 2)
        l2 = PropertyListing(
            id="a_2",
            source="ss_ge",
            source_id="22",
            title="Agent Apt 2",
            price_usd=78000.0,
            area_m2=60.0,
            price_per_m2=1300.0,
            district="ვაკე",
            rooms=2,
            url="https://ss.ge/22",
            phone_number="+995 599 00 00 00",
            is_owner=False,
            user_type="agency",
        )
        self.db.save_listing(l2)

        detector._get_hashes = MagicMock(return_value=["0000000000000000"])
        detector._ensure_hashes = AsyncMock(return_value=["0000000000000000"])

        info2 = await detector.check_listing(l2)
        self.assertIsNotNone(info2)
        # No listing has is_owner=True or user_type='physical', so probable_owner_phone MUST be None
        self.assertIsNone(info2.get("probable_owner_phone"))

        notifier = TelegramNotifier()
        msg = notifier.format_message(l2, dup_info=info2)
        self.assertNotIn("სავარაუდო მესაკუთრე", msg)

    async def test_private_broker_physical_user_type_is_owner_zero(self):
        """Issue 2: Private brokers on MyHome have user_type='physical' but is_owner=0.
        They must NEVER be marked as owner. is_owner=1 takes strict priority."""
        detector = DuplicateDetector(db=self.db, hash_threshold=10)

        # Listing 1: Private broker (user_type='physical', but is_owner=False)
        l1 = PropertyListing(
            id="broker_1",
            source="myhome",
            source_id="101",
            title="Broker Apt",
            price_usd=70000.0,
            area_m2=50.0,
            price_per_m2=1400.0,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/101",
            phone_number="+995 595 11 11 11",
            is_owner=False,
            user_type="physical",  # Private broker!
        )
        self.db.save_listing(l1)

        # Listing 2: Another agent
        l2 = PropertyListing(
            id="broker_2",
            source="ss_ge",
            source_id="102",
            title="Agent Apt",
            price_usd=70000.0,
            area_m2=50.0,
            price_per_m2=1400.0,
            district="საბურთალო",
            rooms=2,
            url="https://ss.ge/102",
            phone_number="+995 595 22 22 22",
            is_owner=False,
            user_type="agent",
        )
        self.db.save_listing(l2)

        detector._get_hashes = MagicMock(return_value=["0000000000000000"])
        detector._ensure_hashes = AsyncMock(return_value=["0000000000000000"])

        info2 = await detector.check_listing(l2)
        self.assertIsNotNone(info2)
        # Broker 1 has is_owner=0, so even with user_type='physical', must NOT be owner!
        self.assertIsNone(info2.get("probable_owner_phone"))

        # Now Listing 3 arrives: true verified owner (is_owner=True)
        l3 = PropertyListing(
            id="owner_real",
            source="myhome",
            source_id="103",
            title="Real Owner Apt",
            price_usd=69000.0,
            area_m2=50.0,
            price_per_m2=1380.0,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/103",
            phone_number="+995 577 99 99 99",
            is_owner=True,
            user_type="physical",
        )
        self.db.save_listing(l3)

        info3 = await detector.check_listing(l3)
        self.assertIsNotNone(info3)
        # Must strictly be +995 577 99 99 99
        self.assertEqual(info3.get("probable_owner_phone"), "+995 577 99 99 99")

    def test_d1_circuit_breaker_fail_closed(self):
        """Issue 1: When D1 returns 429/5xx, DatabaseEngine must raise DatabaseUnavailableError (Circuit Breaker).
        It must NOT silently fall back to an empty SQLite database."""
        from core.database import DatabaseUnavailableError

        with patch("core.database.D1Client.query") as mock_query:
            mock_query.side_effect = RuntimeError("D1 HTTP 429: Rate limited")
            with self.assertRaises(DatabaseUnavailableError):
                DatabaseEngine(use_d1=True)

    async def test_extract_statement_details_only_called_for_filtered_and_unnotified_listings(self):
        """Issue 3: Detail HTML/API enrichment must ONLY happen for listings that pass filters
        and are unnotified, NOT on all search results."""
        from core.filters import ListingFilter
        from core.models import SearchFilters

        fe = ListingFilter(
            SearchFilters(deal_type="sale", price_min_usd=40000, price_max_usd=80000)
        )
        user = UserSubscription(
            chat_id="111",
            deal_type="sale",
            price_min_usd=40000.0,
            price_max_usd=80000.0,
            is_active=True,
        )

        scraper = MyHomeScraper()
        scraper.fetch_statement_details = AsyncMock(
            return_value={"condition_id": 1, "is_owner": True}
        )

        # Listing 1: Fails hygiene (under construction stop word)
        l1 = PropertyListing(
            id="myhome_1",
            source="myhome",
            source_id="1",
            title="Apt 1",
            description="ჩაბარდება 2028 წელს",
            price_usd=50000.0,
            area_m2=50.0,
            price_per_m2=1000.0,
            district="ვაკე",
            rooms=2,
            url="https://myhome.ge/1",
        )
        # Listing 2: Fails user filter (price $120,000 exceeds $80,000)
        l2 = PropertyListing(
            id="myhome_2",
            source="myhome",
            source_id="2",
            title="Apt 2",
            price_usd=120000.0,
            area_m2=50.0,
            price_per_m2=2400.0,
            district="ვაკე",
            rooms=2,
            url="https://myhome.ge/2",
        )
        # Listing 3: Already notified to user 111
        l3 = PropertyListing(
            id="myhome_3",
            source="myhome",
            source_id="3",
            title="Apt 3",
            price_usd=50000.0,
            area_m2=50.0,
            price_per_m2=1000.0,
            district="ვაკე",
            rooms=2,
            url="https://myhome.ge/3",
        )
        self.db.mark_user_notified(user.chat_id, l3.id)

        # Listing 4: Valid, matching, unnotified
        l4 = PropertyListing(
            id="myhome_4",
            source="myhome",
            source_id="4",
            title="Apt 4",
            price_usd=50000.0,
            area_m2=50.0,
            price_per_m2=1000.0,
            district="ვაკე",
            rooms=2,
            url="https://myhome.ge/4",
        )

        all_listings = [l1, l2, l3, l4]

        # Simulate main.py loop logic
        details_fetched = []
        for listing in all_listings:
            if not fe.matches_hygiene(listing):
                self.db.save_listing(listing)
                continue
            matching_users = [
                u
                for u in [user]
                if not self.db.is_user_notified(u.chat_id, listing.id)
                and fe.matches_user(u, listing, tentative=True)
            ]
            if not matching_users:
                self.db.save_listing(listing)
                continue

            # Step 3: Enrichment only reached for matching_users
            d = await scraper.fetch_statement_details(listing.source_id)
            details_fetched.append((listing.id, d))

        # Only listing 4 should have fetched statement details!
        self.assertEqual(len(details_fetched), 1)
        self.assertEqual(details_fetched[0][0], "myhome_4")
        scraper.fetch_statement_details.assert_called_once_with("4")

    def test_dedup_find_candidates_filters_by_30_days_price_and_limit_50(self):
        """_find_candidates must filter out >30d old rows, >50% price diff, and cap at LIMIT 50."""
        from core.dedup import DuplicateDetector

        detector = DuplicateDetector(self.db)

        # 1. Insert an old candidate (45 days ago)
        old_item = PropertyListing(
            id="old_1",
            source="myhome",
            source_id="old_1",
            title="Old Apt",
            price_usd=70000.0,
            area_m2=60.0,
            price_per_m2=1166.0,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/old_1",
        )
        self.db.save_listing(old_item)
        with self.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE properties SET scraped_at = datetime('now', '-45 days') WHERE id = ?",
                ("old_1",),
            )
            conn.commit()

        # 2. Insert an out-of-price-range candidate ($180,000 vs $70,000)
        expensive_item = PropertyListing(
            id="exp_1",
            source="myhome",
            source_id="exp_1",
            title="Expensive Apt",
            price_usd=180000.0,
            area_m2=60.0,
            price_per_m2=3000.0,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/exp_1",
        )
        self.db.save_listing(expensive_item)

        # 3. Insert 60 recent matching candidates
        for i in range(60):
            item = PropertyListing(
                id=f"cand_{i}",
                source="myhome",
                source_id=f"cand_{i}",
                title=f"Candidate {i}",
                price_usd=70000.0,
                area_m2=60.0,
                price_per_m2=1166.0,
                district="საბურთალო",
                rooms=2,
                url=f"https://myhome.ge/cand_{i}",
            )
            self.db.save_listing(item)

        target = PropertyListing(
            id="target_new",
            source="ss_ge",
            source_id="target_new",
            title="Target Apt",
            price_usd=70000.0,
            area_m2=60.0,
            price_per_m2=1166.0,
            district="საბურთალო",
            rooms=2,
            url="https://home.ss.ge/target_new",
        )
        candidates = detector._find_candidates(target)
        candidate_ids = {c["id"] for c in candidates}

        self.assertNotIn("old_1", candidate_ids)
        self.assertNotIn("exp_1", candidate_ids)
        self.assertEqual(len(candidates), 50)

    async def test_run_cycle_skips_already_seen_properties_on_next_cycle(self):
        """
        Listings rejected post-enrichment (e.g. hidden brokers) or pre-enrichment
        must be saved to properties and skipped on subsequent stateless CI cycles
        without re-enriching or re-running dedup.
        """
        from core.filters import ListingFilter
        from core.models import SearchFilters
        from main import RealEstateOrchestrator

        orch = RealEstateOrchestrator.__new__(RealEstateOrchestrator)
        orch.db = self.db
        orch.filter_engine = ListingFilter(SearchFilters())
        orch.analytics = MagicMock()
        orch.analytics.evaluate_deal = MagicMock(side_effect=lambda x: x)
        orch.dedup = MagicMock()
        orch.telegram_notifier = MagicMock()
        orch.telegram_notifier.send_alert = AsyncMock(return_value=True)

        user_vake = UserSubscription(
            chat_id="user_vake",
            deal_type="sale",
            price_max_usd=100000.0,
            districts=["ვაკე"],
            owner_type="owner",
            onboarding_step="completed",
            is_active=True,
        )

        # Listing 1: Appears to match Vake & owner tentatively, but enrichment reveals broker
        l_broker = PropertyListing(
            id="myhome_broker_1",
            source="myhome",
            source_id="broker_1",
            title="Apt in Vake",
            price_usd=80000.0,
            area_m2=65.0,
            price_per_m2=1230.0,
            district="ვაკე",
            rooms=2,
            is_owner=True,
            url="https://myhome.ge/broker_1",
        )
        # Listing 2: Unmatched district (Gldani)
        l_unmatched = PropertyListing(
            id="myhome_gldani_1",
            source="myhome",
            source_id="gldani_1",
            title="Apt in Gldani",
            price_usd=60000.0,
            area_m2=60.0,
            price_per_m2=1000.0,
            district="გლდანი",
            rooms=2,
            is_owner=True,
            url="https://myhome.ge/gldani_1",
        )

        mock_scraper = MagicMock()
        mock_scraper.name = "MyHome.ge"
        mock_scraper.fetch_listings = AsyncMock(
            side_effect=lambda f: [l_broker.model_copy(), l_unmatched.model_copy()]
        )
        mock_scraper.fetch_statement_details = AsyncMock(
            return_value={
                "user_type": "agent",
                "is_owner": False,
                "condition_id": 1,
                "price_usd": 80000.0,
            }
        )
        orch.scrapers = [mock_scraper]

        with patch("main.fetch_active_users", return_value=[user_vake]):
            # Cycle 1: l_broker is enriched once and rejected as broker; both saved in properties
            await orch.run_cycle()
            self.assertEqual(mock_scraper.fetch_statement_details.call_count, 1)
            self.assertEqual(orch.dedup.check_listing.call_count, 0)

            # Simulate stateless CI runner cold start on Cycle 2 (empty in-memory _seen_ids)
            self.db._seen_ids.clear()
            mock_scraper.fetch_statement_details.reset_mock()
            orch.dedup.check_listing.reset_mock()

            # Cycle 2: Same listings scraped -> must be skipped via filter_new_listings!
            await orch.run_cycle()
            self.assertEqual(mock_scraper.fetch_statement_details.call_count, 0)
            self.assertEqual(orch.dedup.check_listing.call_count, 0)

    async def test_base_scraper_fetch_html_falls_back_on_403(self):
        """When Cloudflare proxy returns 403, BaseScraper.fetch_html must fall back to direct URL."""
        from config import settings
        from scrapers.ss_ge import SSGeScraper

        scraper = SSGeScraper()
        old_proxy = settings.CLOUDFLARE_PROXY_URL
        settings.CLOUDFLARE_PROXY_URL = "https://proxy.example.workers.dev"
        try:
            proxy_resp = MagicMock()
            proxy_resp.status_code = 403

            direct_resp = MagicMock()
            direct_resp.status_code = 200
            direct_resp.text = "<html>SS.ge direct ok</html>"

            mock_session = AsyncMock()
            mock_session.get = AsyncMock(side_effect=[proxy_resp, direct_resp])
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            with patch("scrapers.base.AsyncSession", return_value=mock_session):
                html = await scraper.fetch_html("https://home.ss.ge/ka/udzravi-qoneba/l/bina/iyideba")
                self.assertEqual(html, "<html>SS.ge direct ok</html>")
                self.assertEqual(mock_session.get.call_count, 2)
        finally:
            settings.CLOUDFLARE_PROXY_URL = old_proxy


if __name__ == "__main__":
    unittest.main()
