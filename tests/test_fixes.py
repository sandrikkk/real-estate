import unittest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from core.models import PropertyListing, UserSubscription
from core.database import DatabaseEngine
from core.analytics import MarketAnalytics
from core.dedup import DuplicateDetector
from core.district_prices import parse_price_table
from notifier.telegram_bot import TelegramNotifier
from scrapers.myhome import MyHomeScraper


import tempfile
import os


def myhome_price_table():
    """MyHome's real 2-bedroom price table (2026-10-07): the benchmark source for alerts."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "myhome_district_prices_bed2.json")
    with open(path, encoding="utf-8") as f:
        return parse_price_table(json.load(f))


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
        # 3 rooms = 2 bedrooms: the fallback below compares against MyHome's 2-bedroom table
        analytics = MarketAnalytics(price_tables={2: myhome_price_table()})
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
        self.assertEqual(info2.get("previous_best_price_usd"), 70000.0)
        self.assertEqual(info2.get("price_drop_usd"), 5000.0)
        # Owner phone should be listing 2's phone (+995 577 22 22 22) because is_owner=True
        self.assertEqual(info2.get("probable_owner_phone"), "+995 577 22 22 22")

        notifier = TelegramNotifier()
        msg2 = notifier.format_message(l2, dup_info=info2)
        self.assertIn("ახალი ყველაზე დაბალი ფასი!", msg2)
        self.assertIn("წინა ფასი: <b>$70,000</b>", msg2)
        self.assertIn("დაკლებულია: <b>-$5,000</b>", msg2)

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

        # Listing 4 arrives at $64,999 ($1 difference from $65,000)
        l4 = PropertyListing(
            id="prop_4",
            source="ss_ge",
            source_id="4",
            title="Apt 4",
            price_usd=64999.0,
            area_m2=50.0,
            price_per_m2=1299.98,
            district="საბურთალო",
            rooms=2,
            url="https://ss.ge/4",
            phone_number="+995 599 44 44 44",
            is_owner=False,
            user_type="agent",
        )
        self.db.save_listing(l4)
        info4 = await detector.check_listing(l4)
        self.assertIsNotNone(info4)
        self.assertTrue(info4.get("is_strictly_lower"))
        self.assertEqual(info4.get("previous_best_price_usd"), 65000.0)
        self.assertEqual(info4.get("price_drop_usd"), 1.0)
        msg4 = notifier.format_message(l4, dup_info=info4)
        self.assertIn("ახალი ყველაზე დაბალი ფასი!", msg4)
        self.assertIn("წინა ფასი: <b>$65,000</b>", msg4)
        self.assertIn("დაკლებულია: <b>-$1</b>", msg4)

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
        orch.dedup.index_new_listings = AsyncMock(return_value=0)
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
                html = await scraper.fetch_html(
                    "https://home.ss.ge/ka/udzravi-qoneba/l/bina/iyideba"
                )
                self.assertEqual(html, "<html>SS.ge direct ok</html>")
                self.assertEqual(mock_session.get.call_count, 2)
        finally:
            settings.CLOUDFLARE_PROXY_URL = old_proxy

    async def test_proxy_requests_send_sync_key_and_fall_back_on_401(self):
        """Worker proxy requires X-Sync-Key; a 401 from it must fall back to the direct URL."""
        from config import settings
        from scrapers.myhome import MyHomeScraper
        from scrapers.ss_ge import SSGeScraper

        old_proxy, old_key = settings.CLOUDFLARE_PROXY_URL, settings.CLOUDFLARE_SYNC_KEY
        settings.CLOUDFLARE_PROXY_URL = "https://proxy.example.workers.dev"
        settings.CLOUDFLARE_SYNC_KEY = "secret-key"
        try:
            proxy_resp = MagicMock(status_code=401)
            direct_resp = MagicMock(status_code=200, text="<html>direct ok</html>")
            mock_session = AsyncMock()
            mock_session.get = AsyncMock(side_effect=[proxy_resp, direct_resp])
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            with patch("scrapers.base.AsyncSession", return_value=mock_session):
                html = await SSGeScraper().fetch_html("https://home.ss.ge/ka/udzravi-qoneba")
            self.assertEqual(html, "<html>direct ok</html>")
            proxy_call = mock_session.get.call_args_list[0]
            self.assertEqual(proxy_call.kwargs["headers"].get("X-Sync-Key"), "secret-key")

            api_resp = MagicMock(status_code=200)
            api_resp.json.return_value = {"data": {"data": []}}
            api_session = AsyncMock()
            api_session.get = AsyncMock(return_value=api_resp)
            api_session.__aenter__ = AsyncMock(return_value=api_session)
            api_session.__aexit__ = AsyncMock(return_value=False)

            with patch("scrapers.myhome.AsyncSession", return_value=api_session):
                await MyHomeScraper()._fetch_api_page(
                    "https://api-statements.tnet.ge/v1/statements?page=1"
                )
            self.assertEqual(
                api_session.get.call_args.kwargs["headers"].get("X-Sync-Key"), "secret-key"
            )
        finally:
            settings.CLOUDFLARE_PROXY_URL, settings.CLOUDFLARE_SYNC_KEY = old_proxy, old_key

    async def test_run_cycle_alerts_on_in_place_price_drop_once_and_skips_false_drop(self):
        """Verify that an existing listing dropping price in-place alerts subscribers
        (even if previously notified at higher price), updates DB so next cycle is silent,
        and ignores false price drops when live detail price did not actually drop."""
        from main import RealEstateOrchestrator

        # A real orchestrator opens settings.DATABASE_PATH, which is production D1 whenever a
        # .env is present: it ran init_db there on every local test run until 2026-11-08.
        with patch("main.DatabaseEngine", return_value=self.db):
            orch = RealEstateOrchestrator()
        orch.db = self.db
        orch.telegram_notifier = MagicMock()
        orch.telegram_notifier.send_notification = AsyncMock(return_value=True)

        user = UserSubscription(
            chat_id="u_1",
            price_min_usd=50000,
            price_max_usd=72000,
            area_min_m2=45,
            area_max_m2=65,
            rooms_min=2,
            districts=["ისანი"],
            owner_type="all",
        )

        # Cycle 1: Listing 1 at $70,000 (notified to u_1) and Listing 2 at $75,000 (above u_1 max price, saved unmatched)
        l1_v1 = PropertyListing(
            id="myhome_901",
            source="myhome",
            source_id="901",
            title="იყიდება ბინა ისანში",
            price_usd=70000.0,
            area_m2=55.0,
            rooms=2,
            district="ისანი",
            condition_id=1,
            condition_name="ახალი გარემონტებული",
            is_owner=True,
            url="https://myhome.ge/901",
        )
        l2_v1 = PropertyListing(
            id="myhome_902",
            source="myhome",
            source_id="902",
            title="იყიდება ბინა ისანში 2",
            price_usd=75000.0,
            area_m2=55.0,
            rooms=2,
            district="ისანი",
            condition_id=1,
            condition_name="ახალი გარემონტებული",
            is_owner=True,
            url="https://myhome.ge/902",
        )

        mock_scraper = MagicMock()
        mock_scraper.name = "MyHome.ge"
        mock_scraper.fetch_listings = AsyncMock(return_value=[l1_v1, l2_v1])
        mock_scraper.fetch_statement_details = AsyncMock(return_value=None)
        orch.scrapers = [mock_scraper]

        with patch("main.fetch_active_users", return_value=[user]):
            stats1 = await orch.run_cycle()
        self.assertEqual(stats1["notifications_sent"], 1)

        # Cycle 2: l1 drops to $65,000, l2 drops to $68,000 (now inside budget!)
        l1_v2 = l1_v1.model_copy()
        l1_v2.price_usd = 65000.0
        l2_v2 = l2_v1.model_copy()
        l2_v2.price_usd = 68000.0
        mock_scraper.fetch_listings = AsyncMock(return_value=[l1_v2, l2_v2])

        with patch("main.fetch_active_users", return_value=[user]):
            stats2 = await orch.run_cycle()
        self.assertEqual(stats2["notifications_sent"], 2)

        # Verify l1_v2 had previous_price_usd=70000.0 and price_drop_usd=5000.0
        sent_listings = [
            call.args[0] for call in orch.telegram_notifier.send_notification.call_args_list[-2:]
        ]
        by_id = {item.id: item for item in sent_listings}
        self.assertEqual(by_id["myhome_901"].previous_price_usd, 70000.0)
        self.assertEqual(by_id["myhome_901"].price_drop_usd, 5000.0)
        self.assertEqual(by_id["myhome_902"].previous_price_usd, 75000.0)
        self.assertEqual(by_id["myhome_902"].price_drop_usd, 7000.0)

        # Cycle 3: Same dropped prices ($65,000 and $68,000) -> 0 notifications, 0 detail calls
        mock_scraper.fetch_statement_details.reset_mock()
        l1_v3 = l1_v2.model_copy()
        l2_v3 = l2_v2.model_copy()
        mock_scraper.fetch_listings = AsyncMock(return_value=[l1_v3, l2_v3])
        with patch("main.fetch_active_users", return_value=[user]):
            stats3 = await orch.run_cycle()
        self.assertEqual(stats3["notifications_sent"], 0)
        mock_scraper.fetch_statement_details.assert_not_called()

        # Cycle 4: False price drop on search page ($64,000), but live detail page says $65,000 (unchanged)
        l1_false = l1_v2.model_copy()
        l1_false.price_usd = 64000.0
        mock_scraper.fetch_listings = AsyncMock(return_value=[l1_false])
        mock_scraper.fetch_statement_details = AsyncMock(return_value={"price_usd": 65000.0})
        with patch("main.fetch_active_users", return_value=[user]):
            stats4 = await orch.run_cycle()
        self.assertEqual(stats4["notifications_sent"], 0)

    async def test_run_cycle_saves_unmatched_before_enrichment_and_enriches_concurrently(self):
        """R-02: Unmatched listings must be batch-saved BEFORE slow detail enrichment starts,
        and detail enrichment across multiple matched listings must run concurrently."""
        import asyncio
        from core.filters import ListingFilter
        from core.models import SearchFilters
        from main import RealEstateOrchestrator

        orch = RealEstateOrchestrator.__new__(RealEstateOrchestrator)
        orch.db = self.db
        orch.filter_engine = ListingFilter(SearchFilters())
        orch.analytics = MarketAnalytics()
        orch.dedup = MagicMock()
        orch.dedup.index_new_listings = AsyncMock(return_value=0)
        orch.dedup.check_listing = AsyncMock(return_value=None)
        orch.telegram_notifier = MagicMock()
        orch.telegram_notifier.send_notification = AsyncMock(return_value=True)

        user = UserSubscription(
            chat_id="u_fast",
            deal_type="sale",
            price_min_usd=45000,
            price_max_usd=75000,
            area_min_m2=40,
            area_max_m2=65,
            rooms_min=1,
            districts=["საბურთალო"],
            owner_type="all",
            is_active=True,
        )

        unmatched_item = PropertyListing(
            id="myhome_unmatched_1",
            source="myhome",
            source_id="unmatched_1",
            title="ძვირი ბინა",
            price_usd=120000.0,
            area_m2=50.0,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/unmatched_1",
        )
        matched_1 = PropertyListing(
            id="myhome_match_1",
            source="myhome",
            source_id="match_1",
            title="ბინა 1",
            price_usd=60000.0,
            area_m2=50.0,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/match_1",
        )
        matched_2 = PropertyListing(
            id="myhome_match_2",
            source="myhome",
            source_id="match_2",
            title="ბინა 2",
            price_usd=62000.0,
            area_m2=52.0,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/match_2",
        )

        unmatched_saved_before_enrich = False
        active_enrichments = 0
        max_concurrent_enrichments = 0

        async def mock_fetch_details(source_id):
            nonlocal unmatched_saved_before_enrich, active_enrichments, max_concurrent_enrichments
            if self.db.is_seen("myhome_unmatched_1"):
                unmatched_saved_before_enrich = True
            active_enrichments += 1
            max_concurrent_enrichments = max(max_concurrent_enrichments, active_enrichments)
            await asyncio.sleep(0.03)
            active_enrichments -= 1
            return {"price_usd": 60000.0, "is_owner": True, "condition_id": 1}

        mock_scraper = MagicMock()
        mock_scraper.name = "MyHome.ge"
        mock_scraper.fetch_listings = AsyncMock(return_value=[unmatched_item, matched_1, matched_2])
        mock_scraper.fetch_statement_details = AsyncMock(side_effect=mock_fetch_details)
        orch.scrapers = [mock_scraper]

        with patch("main.fetch_active_users", return_value=[user]):
            await orch.run_cycle()

        self.assertTrue(
            unmatched_saved_before_enrich,
            "unmatched_to_save must be saved before detail enrichment starts",
        )
        self.assertGreaterEqual(
            max_concurrent_enrichments,
            2,
            f"Expected concurrent detail enrichments (>=2), got {max_concurrent_enrichments}",
        )

    def test_mark_user_notified_propagates_d1_unavailable_error(self):
        """R-03: mark_user_notified must propagate DatabaseUnavailableError (Fail-Closed) instead of swallowing it."""
        from core.database import D1Client, DatabaseUnavailableError

        mock_client = MagicMock(spec=D1Client)
        mock_client.query.side_effect = DatabaseUnavailableError("D1 HTTP 503: Service Unavailable")

        d1_db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        d1_db.use_d1 = True
        d1_db.d1_client = mock_client

        with self.assertRaises(DatabaseUnavailableError):
            d1_db.mark_user_notified("chat_r03", "listing_r03")
        self.assertNotIn("listing_r03", d1_db._user_seen["chat_r03"])

    async def test_failed_telegram_send_rolls_back_listing_for_next_cycle_retry(self):
        """R-03: If Telegram send_notification returns False on Cycle 1, the listing must not be
        permanently marked seen at the new price and must be retried and delivered on Cycle 2."""
        from core.filters import ListingFilter
        from core.models import SearchFilters
        from main import RealEstateOrchestrator

        orch = RealEstateOrchestrator.__new__(RealEstateOrchestrator)
        orch.db = self.db
        orch.filter_engine = ListingFilter(SearchFilters())
        orch.analytics = MarketAnalytics()
        orch.dedup = MagicMock()
        orch.dedup.index_new_listings = AsyncMock(return_value=0)
        orch.dedup.check_listing = AsyncMock(return_value=None)
        orch.telegram_notifier = MagicMock()

        user = UserSubscription(
            chat_id="u_retry",
            deal_type="sale",
            price_min_usd=45000,
            price_max_usd=75000,
            area_min_m2=40,
            area_max_m2=65,
            rooms_min=1,
            districts=["საბურთალო"],
            owner_type="all",
            is_active=True,
        )

        item = PropertyListing(
            id="myhome_retry_1",
            source="myhome",
            source_id="retry_1",
            title="ბინა საბურთალოზე",
            price_usd=60000.0,
            area_m2=50.0,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/retry_1",
        )

        mock_scraper = MagicMock()
        mock_scraper.name = "MyHome.ge"
        mock_scraper.fetch_listings = AsyncMock(side_effect=lambda f: [item.model_copy()])
        mock_scraper.fetch_statement_details = AsyncMock(
            return_value={"price_usd": 60000.0, "is_owner": True, "condition_id": 1}
        )
        orch.scrapers = [mock_scraper]

        # Cycle 1: Telegram fails (returns False)
        orch.telegram_notifier.send_notification = AsyncMock(return_value=False)
        with patch("main.fetch_active_users", return_value=[user]):
            stats1 = await orch.run_cycle()
        self.assertEqual(stats1["notifications_sent"], 0)
        self.assertFalse(self.db.is_seen("myhome_retry_1"))

        # Cycle 2: Telegram recovers (returns True) -> listing is retried and sent!
        orch.telegram_notifier.send_notification = AsyncMock(return_value=True)
        with patch("main.fetch_active_users", return_value=[user]):
            stats2 = await orch.run_cycle()
        self.assertEqual(stats2["notifications_sent"], 1)
        self.assertTrue(self.db.is_seen("myhome_retry_1"))

    async def test_myhome_detail_is_owner_flag_does_not_drop_owner_alerts(self):
        """MyHome's statement `is_owner` is false on every page an anonymous visitor gets,
        owners' included (36 of 36 checked, 2026-11-08). It must not turn a private seller
        into an agent and hide them from owner-only subscribers; a real agency still does."""
        from core.filters import ListingFilter
        from core.models import SearchFilters
        from main import RealEstateOrchestrator

        user = UserSubscription(
            chat_id="u_owner_only",
            deal_type="sale",
            price_min_usd=45000,
            price_max_usd=75000,
            area_min_m2=40,
            area_max_m2=65,
            rooms_min=1,
            districts=["საბურთალო"],
            owner_type="owner",
            is_active=True,
        )

        async def cycle(listing_id, details):
            orch = RealEstateOrchestrator.__new__(RealEstateOrchestrator)
            orch.db = self.db
            orch.filter_engine = ListingFilter(SearchFilters())
            orch.analytics = MarketAnalytics()
            orch.dedup = MagicMock()
            orch.dedup.index_new_listings = AsyncMock(return_value=0)
            orch.dedup.check_listing = AsyncMock(return_value=None)
            orch.telegram_notifier = MagicMock()
            orch.telegram_notifier.send_notification = AsyncMock(return_value=True)
            item = PropertyListing(
                id=f"myhome_{listing_id}",
                source="myhome",
                source_id=listing_id,
                title="ბინა საბურთალოზე",
                price_usd=60000.0,
                area_m2=50.0,
                district="საბურთალო",
                rooms=2,
                user_type="physical",
                is_owner=True,
                url=f"https://myhome.ge/{listing_id}",
            )
            scraper = MagicMock()
            scraper.name = "MyHome.ge"
            scraper.fetch_listings = AsyncMock(side_effect=lambda f: [item.model_copy()])
            scraper.fetch_statement_details = AsyncMock(return_value=details)
            orch.scrapers = [scraper]
            with patch("main.fetch_active_users", return_value=[user]):
                return await orch.run_cycle()

        owner = await cycle("owner_1", {"price_usd": 60000.0, "is_owner": False, "condition_id": 1})
        self.assertEqual(owner["notifications_sent"], 1, "the MyHome flag hid a private seller")

        for name, signal in (
            ("agency_1", {"agency_name": "X"}),
            ("agent_1", {"user_type": {"type": "agent"}}),
            ("busy_1", {"user_type": {"type": "physical"}, "user_statements_count": 7}),
        ):
            details = {"price_usd": 60000.0, "is_owner": False, "condition_id": 1, **signal}
            agent = await cycle(name, details)
            self.assertEqual(agent["notifications_sent"], 0, f"{signal} must still mark an agent")

    def test_sync_key_not_in_url_or_error_logs(self):
        """Sync key must be sent only via X-Sync-Key header, never in URL query params or warning logs."""
        import urllib.error
        from core.database import D1Client, DatabaseUnavailableError
        from core.user_sync import fetch_active_users

        captured_urls = []
        captured_headers = []

        def fake_urlopen(req, timeout=10):
            captured_urls.append(req.full_url)
            captured_headers.append(dict(req.header_items()))
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)

        with (
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            self.assertLogs("core.user_sync", level="WARNING") as cm,
        ):
            fetch_active_users(worker_url="https://worker.dev", sync_key="super_secret_sync_key_99")

        self.assertEqual(len(captured_urls), 1)
        self.assertNotIn("super_secret_sync_key_99", captured_urls[0])
        self.assertNotIn("key=", captured_urls[0])
        log_output = "\n".join(cm.output)
        self.assertNotIn("super_secret_sync_key_99", log_output)
        self.assertNotIn("https://worker.dev", log_output)

        # Same-class check: D1Client._send must also omit ?key= from URL
        captured_urls.clear()
        d1 = D1Client("https://worker.dev", "super_secret_sync_key_99")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(DatabaseUnavailableError):
                d1.query("SELECT 1")
        self.assertEqual(len(captured_urls), 1)
        self.assertNotIn("super_secret_sync_key_99", captured_urls[0])
        self.assertNotIn("key=", captured_urls[0])

    async def test_telegram_caption_truncation_and_retry_after_429(self):
        """Telegram send_photo must truncate captions > 1024 chars and retry once after RetryAfter (429)."""
        notifier = TelegramNotifier(bot_token="fake:token", chat_id="12345", enable_console=False)
        mock_bot = MagicMock()

        class FakeRetryAfter(Exception):
            def __init__(self, retry_after):
                super().__init__(f"Flood control exceeded. Retry in {retry_after} seconds")
                self.retry_after = retry_after

        call_count = 0
        sent_captions = []

        async def fake_send_photo(**kwargs):
            nonlocal call_count
            call_count += 1
            sent_captions.append(kwargs.get("caption", ""))
            if call_count == 1:
                raise FakeRetryAfter(0.01)
            return True

        mock_bot.send_photo = AsyncMock(side_effect=fake_send_photo)
        mock_bot.send_message = AsyncMock(return_value=True)
        notifier.bot = mock_bot

        long_street = "ჭავჭავაძის გამზირი " * 60
        listing = PropertyListing(
            id="myhome_caption_test",
            source="myhome",
            source_id="caption_test",
            title="იყიდება ბინა ვაკეში",
            price_usd=65000.0,
            area_m2=55.0,
            district="ვაკე",
            street=long_street,
            rooms=2,
            images=["https://example.com/photo1.jpg"],
            url="https://myhome.ge/pr/1",
        )
        full_msg = notifier.format_message(listing)
        self.assertGreater(len(full_msg), 1024)

        ok = await notifier.send_notification(listing, target_chat_id="12345")
        self.assertTrue(ok)
        self.assertEqual(call_count, 2, "send_photo should be retried once after RetryAfter")
        self.assertLessEqual(len(sent_captions[-1]), 1024)
        mock_bot.send_message.assert_not_called()

    def test_rollback_unsent_listing_removes_id_from_seen_ids_file_on_disk(self):
        """rollback_unsent_listing must also remove the ID from seen_ids.txt on disk so restarts do not suppress it."""
        l1 = PropertyListing(
            id="myhome_rollback_disk_1",
            source="myhome",
            source_id="rollback_disk_1",
            title="Apt Rollback Disk",
            price_usd=60000.0,
            area_m2=55.0,
            price_per_m2=1090.91,
            district="საბურთალო",
            rooms=2,
            url="https://myhome.ge/rollback_disk_1",
        )
        self.db.save_listing(l1)
        self.assertTrue(self.db.is_seen("myhome_rollback_disk_1"))
        with open(self.db.seen_ids_path, "r", encoding="utf-8") as f:
            self.assertIn("myhome_rollback_disk_1", f.read().splitlines())

        self.db.rollback_unsent_listing("myhome_rollback_disk_1")
        self.assertFalse(self.db.is_seen("myhome_rollback_disk_1"))

        with open(self.db.seen_ids_path, "r", encoding="utf-8") as f:
            self.assertNotIn("myhome_rollback_disk_1", f.read().splitlines())

        # Simulate process restart reloading seen_ids.txt
        restarted_db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        self.assertFalse(restarted_db.is_seen("myhome_rollback_disk_1"))


if __name__ == "__main__":
    unittest.main()
