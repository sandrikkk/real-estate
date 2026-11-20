import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from core.database import DatabaseEngine
from core.models import PropertyListing
from core.dedup import DuplicateDetector


class FakeNotifier:
    def __init__(self):
        self.sent_updates = []

    async def send_watch_update(self, chat_id, text_html, reply_markup=None):
        self.sent_updates.append(
            {"chat_id": chat_id, "text": text_html, "reply_markup": reply_markup}
        )
        return True


class FakeMyHomeScraper:
    name = "MyHome.ge"

    def __init__(self):
        self.details = {}
        self.htmls = {}
        self.detail_calls = []
        self.html_calls = []

    async def fetch_statement_details(self, source_id: str):
        self.detail_calls.append(source_id)
        return self.details.get(source_id)

    async def fetch_html(self, url: str):
        self.html_calls.append(url)
        return self.htmls.get(url)


class FakeSSGeScraper:
    name = "SS.ge"

    def __init__(self):
        self.details = {}
        self.detail_calls = []

    async def fetch_statement_details(self, source_id_or_url: str):
        self.detail_calls.append(source_id_or_url)
        return self.details.get(source_id_or_url)


class TestWatchlist(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        self.db.init_db()

        self.dedup = DuplicateDetector(self.db)
        self.notifier = FakeNotifier()
        self.myhome = FakeMyHomeScraper()
        self.ss_ge = FakeSSGeScraper()
        self.scrapers = [self.myhome, self.ss_ge]

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    def _insert_prop(
        self,
        lid: str,
        source: str = "myhome",
        source_id: str = "123",
        price: float = 60000.0,
        area: float = 50.0,
        district: str = "საბურთალო",
        url: str = "https://myhome.ge/pr/123",
        images: list = None,
        group_id: str = None,
        delisted_at: str = None,
    ):
        price_per_m2 = (price / area) if area else price
        with self.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO properties (
                    id, source, source_id, title, price_usd, price_per_m2, area_m2, district, url,
                    images_json, group_id, delisted_at, scraped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lid,
                    source,
                    source_id,
                    f"Prop {lid}",
                    price,
                    price_per_m2,
                    area,
                    district,
                    url,
                    json.dumps(images or []),
                    group_id,
                    delisted_at,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def _insert_watch(
        self,
        chat_id: str,
        lid: str,
        status: str = "active",
        last_price: float = 60000.0,
        created_at: str = None,
        last_checked_at: str = None,
        ended_at: str = None,
    ):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO watchlist (
                    chat_id, listing_id, status, last_price_usd, created_at, last_checked_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    lid,
                    status,
                    last_price,
                    created_at or now_iso,
                    last_checked_at,
                    ended_at,
                ),
            )
            conn.commit()

    async def test_price_drop_gives_drop_message(self):
        from core.watchlist import check_watches

        self._insert_prop("myhome_1", price=60000.0)
        self._insert_watch("100", "myhome_1", last_price=60000.0)

        # cycle_prices shows 55000 (drop by 5000)
        await check_watches(
            db=self.db,
            dedup=self.dedup,
            notifier=self.notifier,
            scrapers=self.scrapers,
            cycle_prices={"myhome_1": 55000.0},
            new_listings=[],
            alerted=set(),
        )

        self.assertEqual(len(self.notifier.sent_updates), 1)
        up = self.notifier.sent_updates[0]
        self.assertEqual(up["chat_id"], "100")
        self.assertIn("📉 <b>ფასი დაიკლო</b>", up["text"])
        self.assertIn("$60,000 → <b>$55,000</b> (-$5,000)", up["text"])

        # Check DB updated last_price_usd
        with self.db._connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT last_price_usd FROM watchlist WHERE chat_id='100' AND listing_id='myhome_1'"
            )
            row = c.fetchone()
            self.assertEqual(row[0], 55000.0)

    async def test_price_rise_gives_warning_message(self):
        from core.watchlist import check_watches

        self._insert_prop("myhome_1", price=60000.0)
        self._insert_watch("100", "myhome_1", last_price=60000.0)

        await check_watches(
            db=self.db,
            dedup=self.dedup,
            notifier=self.notifier,
            scrapers=self.scrapers,
            cycle_prices={"myhome_1": 65000.0},
            new_listings=[],
            alerted=set(),
        )

        self.assertEqual(len(self.notifier.sent_updates), 1)
        up = self.notifier.sent_updates[0]
        self.assertIn("⚠️ <b>ფასი გაიზარდა ან შესწორდა</b>", up["text"])
        self.assertIn("$60,000 → <b>$65,000</b> (+$5,000)", up["text"])

    async def test_price_drop_already_alerted_skips_message_but_updates_price(self):
        from core.watchlist import check_watches

        self._insert_prop("myhome_1", price=60000.0)
        self._insert_watch("100", "myhome_1", last_price=60000.0)

        # Already alerted in this cycle
        await check_watches(
            db=self.db,
            dedup=self.dedup,
            notifier=self.notifier,
            scrapers=self.scrapers,
            cycle_prices={"myhome_1": 55000.0},
            new_listings=[],
            alerted={("100", "myhome_1")},
        )

        self.assertEqual(len(self.notifier.sent_updates), 0)
        with self.db._connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT last_price_usd FROM watchlist WHERE chat_id='100' AND listing_id='myhome_1'"
            )
            row = c.fetchone()
            self.assertEqual(row[0], 55000.0)

    async def test_delisted_at_gives_deleted_message_and_status_delisted(self):
        from core.watchlist import check_watches

        delisted_time = datetime.now(timezone.utc).isoformat()
        self._insert_prop("myhome_1", price=60000.0, delisted_at=delisted_time)
        self._insert_watch("100", "myhome_1", last_price=60000.0)

        await check_watches(
            db=self.db,
            dedup=self.dedup,
            notifier=self.notifier,
            scrapers=self.scrapers,
            cycle_prices={},
            new_listings=[],
            alerted=set(),
        )

        self.assertEqual(len(self.notifier.sent_updates), 1)
        up = self.notifier.sent_updates[0]
        self.assertIn("❌ <b>განცხადება წაიშალა</b>", up["text"])
        self.assertIn("ბოლო ფასი: <b>$60,000</b>", up["text"])
        self.assertIn("თვალყურის დევნება დასრულდა.", up["text"])

        with self.db._connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT status, ended_at FROM watchlist WHERE chat_id='100' AND listing_id='myhome_1'"
            )
            row = c.fetchone()
            self.assertEqual(row[0], "delisted")
            self.assertIsNotNone(row[1])

    async def test_unknown_fetch_gives_no_message_and_no_status_change(self):
        from core.watchlist import check_watches

        # Not in cycle_prices, last_checked_at is old
        old_time = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        self._insert_prop("ss_ge_1", source="ss_ge", source_id="1", price=60000.0)
        self._insert_watch("100", "ss_ge_1", last_price=60000.0, last_checked_at=old_time)

        # Scraper returns None
        self.ss_ge.details["1"] = None

        await check_watches(
            db=self.db,
            dedup=self.dedup,
            notifier=self.notifier,
            scrapers=self.scrapers,
            cycle_prices={},
            new_listings=[],
            alerted=set(),
        )

        self.assertEqual(len(self.notifier.sent_updates), 0)
        with self.db._connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT status, last_checked_at FROM watchlist WHERE chat_id='100' AND listing_id='ss_ge_1'"
            )
            row = c.fetchone()
            self.assertEqual(row[0], "active")
            self.assertNotEqual(row[1], old_time)  # last_checked_at updated

    async def test_recheck_cap_15_and_6hour_skip_hold(self):
        from core.watchlist import check_watches

        # Insert 20 active watches with old last_checked_at
        old_time = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        recent_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        # 1 recent watch (should be skipped)
        self._insert_prop("ss_ge_recent", source="ss_ge", source_id="recent")
        self._insert_watch("100", "ss_ge_recent", last_checked_at=recent_time)

        # 20 old watches
        for i in range(20):
            lid = f"ss_ge_{i}"
            self._insert_prop(lid, source="ss_ge", source_id=str(i))
            self._insert_watch("100", lid, last_checked_at=old_time)
            self.ss_ge.details[str(i)] = {"is_active": True, "price_usd": 60000.0}

        await check_watches(
            db=self.db,
            dedup=self.dedup,
            notifier=self.notifier,
            scrapers=self.scrapers,
            cycle_prices={},
            new_listings=[],
            alerted=set(),
        )

        # Cap is 15 checks
        self.assertLessEqual(len(self.ss_ge.detail_calls), 15)
        self.assertNotIn("recent", self.ss_ge.detail_calls)

    async def test_relist_by_photo_hashes_gives_relist_with_button(self):
        from core.watchlist import check_watches

        self._insert_prop("myhome_1", price=60000.0, area=50.0)
        self._insert_watch("100", "myhome_1", last_price=60000.0)

        # Set fake hashes for myhome_1 and new listing ss_ge_2
        # Identical 3 hashes
        hashes = ["0000000000000000", "1111111111111111", "2222222222222222"]
        self.dedup.save_hashes_batch({"myhome_1": hashes, "ss_ge_2": hashes})

        new_listing = PropertyListing(
            id="ss_ge_2",
            source="ss_ge",
            source_id="2",
            title="Relisted Apt",
            price_usd=58000.0,
            area_m2=50.0,
            district="საბურთალო",
            url="https://ss.ge/pr/2",
        )

        await check_watches(
            db=self.db,
            dedup=self.dedup,
            notifier=self.notifier,
            scrapers=self.scrapers,
            cycle_prices={"ss_ge_2": 58000.0},
            new_listings=[new_listing],
            alerted=set(),
        )

        self.assertEqual(len(self.notifier.sent_updates), 1)
        up = self.notifier.sent_updates[0]
        self.assertIn("🔁 <b>იგივე ბინა ხელახლა დაიდო</b>", up["text"])
        self.assertIn("SS.ge: <b>$58,000</b> (ადრე: $60,000)", up["text"])
        self.assertIsNotNone(up["reply_markup"])
        button = up["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.callback_data, "w:ss_ge_2")
        self.assertEqual(button.text, "👁 თვალყური ადევნე")

    async def test_relist_by_group_id_gives_relist(self):
        from core.watchlist import check_watches

        self._insert_prop("myhome_1", price=60000.0, group_id="grp_99")
        self._insert_watch("100", "myhome_1", last_price=60000.0)

        new_listing = PropertyListing(
            id="ss_ge_2",
            source="ss_ge",
            source_id="2",
            title="Relisted Apt Group",
            price_usd=58000.0,
            area_m2=50.0,
            district="საბურთალო",
            url="https://ss.ge/pr/2",
        )
        new_listing.duplicate_group_id = "grp_99"

        await check_watches(
            db=self.db,
            dedup=self.dedup,
            notifier=self.notifier,
            scrapers=self.scrapers,
            cycle_prices={"ss_ge_2": 58000.0},
            new_listings=[new_listing],
            alerted=set(),
        )

        self.assertEqual(len(self.notifier.sent_updates), 1)
        up = self.notifier.sent_updates[0]
        self.assertIn("🔁 <b>იგივე ბინა ხელახლა დაიდო</b>", up["text"])

    async def test_two_chats_watching_one_flat_cause_only_one_fetch(self):
        from core.watchlist import check_watches

        old_time = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        self._insert_prop("ss_ge_1", source="ss_ge", source_id="1", price=60000.0)
        self._insert_watch("100", "ss_ge_1", last_price=60000.0, last_checked_at=old_time)
        self._insert_watch("200", "ss_ge_1", last_price=60000.0, last_checked_at=old_time)

        self.ss_ge.details["1"] = {"is_active": True, "price_usd": 55000.0}

        await check_watches(
            db=self.db,
            dedup=self.dedup,
            notifier=self.notifier,
            scrapers=self.scrapers,
            cycle_prices={},
            new_listings=[],
            alerted=set(),
        )

        # Scraper fetch called only ONCE for source_id="1"
        self.assertEqual(self.ss_ge.detail_calls.count("1"), 1)
        # Both chats received update
        self.assertEqual(len(self.notifier.sent_updates), 2)
        chats = {u["chat_id"] for u in self.notifier.sent_updates}
        self.assertEqual(chats, {"100", "200"})

    async def test_exception_in_checker_does_not_stop_run_cycle(self):
        from main import RealEstateOrchestrator
        from core.filters import ListingFilter
        from core.models import SearchFilters

        # A real orchestrator opens production D1 whenever a .env is present (see test_fixes).
        with patch("main.DatabaseEngine", return_value=self.db):
            orch = RealEstateOrchestrator()
        orch.db = self.db
        orch.filter_engine = ListingFilter(SearchFilters())
        orch.scrapers = []
        orch.dedup = self.dedup
        orch.telegram_notifier = self.notifier

        with (
            patch("main.fetch_active_users", return_value=[]),
            patch(
                "core.watchlist.check_watches", side_effect=RuntimeError("Watchlist check crashed")
            ),
        ):
            # Should not raise exception
            stats = await orch.run_cycle()
            self.assertIn("total_fetched", stats)
            self.assertEqual(stats["watch_updates"], 0)

    async def test_relist_check_gets_only_first_seen_listings(self):
        """A known listing whose price dropped is not a repost: run_cycle must not pass it on."""
        from unittest.mock import AsyncMock

        from core.filters import ListingFilter
        from core.models import SearchFilters
        from main import RealEstateOrchestrator

        self._insert_prop("myhome_1", source_id="1", price=100000.0)

        def listing(lid, sid, price):
            return PropertyListing(
                id=lid,
                source="myhome",
                source_id=sid,
                title=lid,
                price_usd=price,
                area_m2=50.0,
                district="საბურთალო",
                url=f"https://myhome.ge/pr/{sid}",
            )

        class OneShotScraper:
            name = "Fake"

            async def fetch_listings(self, filters):
                return [listing("myhome_1", "1", 90000.0), listing("myhome_2", "2", 70000.0)]

        # A real orchestrator opens production D1 whenever a .env is present (see test_fixes).
        with patch("main.DatabaseEngine", return_value=self.db):
            orch = RealEstateOrchestrator()
        orch.db = self.db
        orch.filter_engine = ListingFilter(SearchFilters())
        orch.scrapers = [OneShotScraper()]
        orch.dedup = self.dedup
        orch.telegram_notifier = self.notifier

        fake_check = AsyncMock(return_value={})
        with (
            patch("main.fetch_active_users", return_value=[]),
            patch("core.watchlist.check_watches", fake_check),
        ):
            await orch.run_cycle()

        passed = [item.id for item in fake_check.call_args.kwargs["new_listings"]]
        self.assertEqual(passed, ["myhome_2"])

    async def test_live_listing_without_a_price_is_not_a_drop_to_zero(self):
        """The scrapers return price_usd 0.0, not None, when a listing shows no price."""
        from core.watchlist import check_watches

        self._insert_prop("myhome_1", source_id="1", price=65000.0)
        self._insert_watch("100", "myhome_1", last_price=65000.0)
        self._insert_prop("ss_ge_2", source="ss_ge", source_id="2", price=70000.0)
        self._insert_watch("100", "ss_ge_2", last_price=70000.0)
        self.myhome.htmls["https://www.myhome.ge/ka/pr/1/"] = None
        self.ss_ge.details["2"] = {"is_active": True, "price_usd": 0.0}

        async def alive(fetch_html, source_id):
            return False

        self.myhome.details["1"] = {"price_usd": 0.0}
        with patch("core.myhome_alive.looks_deleted", alive):
            await check_watches(
                db=self.db,
                dedup=self.dedup,
                notifier=self.notifier,
                scrapers=self.scrapers,
                cycle_prices={},
                new_listings=[],
                alerted=set(),
            )

        self.assertEqual(self.notifier.sent_updates, [])
        with self.db._connection() as conn:
            prices = dict(
                conn.execute("SELECT listing_id, last_price_usd FROM watchlist").fetchall()
            )
        self.assertEqual(prices, {"myhome_1": 65000.0, "ss_ge_2": 70000.0})
