import unittest
from core.models import PropertyListing
from notifier.telegram_bot import TelegramNotifier


class TestTelegramFormatting(unittest.TestCase):
    def setUp(self):
        self.notifier = TelegramNotifier()

    def test_hot_deal_format_and_tel_link(self):
        listing = PropertyListing(
            id="myhome_9999",
            source="myhome",
            source_id="9999",
            title="იყიდება 2 ოთახიანი ბინა ისანში",
            price_usd=62000,
            area_m2=50.0,
            rooms=2,
            bedrooms=1,
            floor="4",
            total_floors=9,
            district="ისანი",
            street="ნავთლუღის ქ.",
            url="https://www.myhome.ge/ka/pr/9999",
            metro_station_id=16,
            metro_station_name="ისანი",
            condition_id=1,
            condition_name="ახალი გარემონტებული",
            is_owner=True,
            is_hot_deal=True,
            deal_tag="🚨 HOT DEAL (RENOVATED)",
            phone_number="+995 599 12 34 56",
        )

        msg = self.notifier.format_message(listing)

        # 1. Mobile Header with deal tag
        self.assertIn("<b>🚨 HOT DEAL (RENOVATED)</b>", msg)
        self.assertIn("💰 <b>$62,000 | 50.0 მ² | $1,240/მ²</b>", msg)

        # 2. Condition line
        self.assertIn("🛠 <b>მდგომარეობა:</b> ახალი გარემონტებული", msg)

        # 3. Location line with Metro
        self.assertIn("📍 <b>ლოკაცია:</b> <b>ისანი</b> | ნავთლუღის ქ. | 🚇 <b>მ. ისანი</b>", msg)

        # 4. Details line with Owner and Floor
        self.assertIn("👤 <b>მესაკუთრე (Owner)</b>", msg)
        self.assertIn("🏢 სართული: <b>4/9</b>", msg)

        # 5. Direct link
        self.assertIn(
            '🔗 <a href="https://www.myhome.ge/ka/pr/9999">განცხადების ლინკი (MyHome.ge)</a>', msg
        )

        # 6. One-tap call link
        self.assertIn('📞 <a href="tel:+995599123456">+995 599 12 34 56</a>', msg)

    def test_value_frame_deal_tag(self):
        listing = PropertyListing(
            id="myhome_8888",
            source="myhome",
            source_id="8888",
            title="იყიდება 2 ოთახიანი ბინა დიდუბეში",
            price_usd=50000,
            area_m2=50.0,
            rooms=2,
            bedrooms=1,
            district="დიდუბე",
            url="https://www.myhome.ge/ka/pr/8888",
            condition_id=7,
            condition_name="მწვანე კარკასი",
            is_owner=False,
            is_hot_deal=True,
            deal_tag="🔥 VALUE FRAME (<$54k)",
        )

        msg = self.notifier.format_message(listing)
        self.assertIn("<b>🔥 VALUE FRAME (&lt;$54k)</b>", msg)
        self.assertIn("💰 <b>$50,000 | 50.0 მ² | $1,000/მ²</b>", msg)
        self.assertIn("🛠 <b>მდგომარეობა:</b> მწვანე კარკასი", msg)
        self.assertIn("👤 <b>სააგენტო (Agent)</b>", msg)

    def test_agent_and_masked_phone(self):
        listing = PropertyListing(
            id="myhome_7777",
            source="myhome",
            source_id="7777",
            title="იყიდება ბინა",
            price_usd=70000,
            area_m2=55.0,
            rooms=2,
            url="https://www.myhome.ge/ka/pr/7777",
            is_owner=False,
            phone_number="599123***",
        )

        msg = self.notifier.format_message(listing)
        self.assertIn("👤 <b>სააგენტო (Agent)</b>", msg)
        self.assertIn("<code>599123***</code>", msg)

    def test_in_place_price_drop_formatting_and_no_double_line_with_dup_info(self):
        listing = PropertyListing(
            id="myhome_5555",
            source="myhome",
            source_id="5555",
            title="იყიდება ბინა საბურთალოზე",
            price_usd=64999.0,
            previous_price_usd=65000.0,
            price_drop_usd=1.0,
            area_m2=50.0,
            rooms=2,
            district="საბურთალო",
            url="https://www.myhome.ge/ka/pr/5555",
            is_owner=True,
        )
        msg = self.notifier.format_message(listing)
        self.assertIn("🔻 <b>ფასი დააკლდა!</b>", msg)
        self.assertIn("📉 წინა ფასი: <b>$65,000</b> | დაკლებულია: <b>-$1</b>", msg)

        # When dup_info also has is_strictly_lower=True, "📉 წინა ფასი:" must appear only once
        dup_info = {
            "duplicate_count": 2,
            "is_strictly_lower": True,
            "previous_best_price_usd": 65000.0,
            "price_drop_usd": 1.0,
            "best_price_usd": 64999.0,
            "best_price_url": "https://www.myhome.ge/ka/pr/5555",
        }
        msg_with_dup = self.notifier.format_message(listing, dup_info=dup_info)
        self.assertEqual(msg_with_dup.count("📉 წინა ფასი:"), 1)

    def test_send_notification_passes_reply_markup_with_watch_button(self):
        import asyncio
        from unittest.mock import AsyncMock

        async def _test():
            notifier = TelegramNotifier(bot_token="test_token", chat_id="123")
            bot_mock = AsyncMock()
            notifier.bot = bot_mock

            listing_with_photo = PropertyListing(
                id="myhome_12345",
                source="myhome",
                source_id="12345",
                title="Apt",
                price_usd=50000,
                area_m2=45,
                url="https://myhome.ge/pr/12345",
                images=["https://example.com/1.jpg"],
            )
            await notifier.send_notification(listing_with_photo)
            self.assertEqual(bot_mock.send_photo.call_count, 1)
            _, kwargs = bot_mock.send_photo.call_args
            self.assertIn("reply_markup", kwargs)
            markup = kwargs["reply_markup"]
            self.assertIsNotNone(markup)
            self.assertEqual(markup.inline_keyboard[0][0].callback_data, "w:myhome_12345")
            self.assertEqual(markup.inline_keyboard[0][0].text, "👁 თვალყური ადევნე")

            # Text-only send (no images)
            bot_mock.reset_mock()
            listing_no_photo = PropertyListing(
                id="ss_ge_67890",
                source="ss_ge",
                source_id="67890",
                title="Apt",
                price_usd=50000,
                area_m2=45,
                url="https://ss.ge/pr/67890",
                images=[],
            )
            await notifier.send_notification(listing_no_photo)
            self.assertEqual(bot_mock.send_message.call_count, 1)
            _, kwargs = bot_mock.send_message.call_args
            self.assertIn("reply_markup", kwargs)
            markup = kwargs["reply_markup"]
            self.assertIsNotNone(markup)
            self.assertEqual(markup.inline_keyboard[0][0].callback_data, "w:ss_ge_67890")
            self.assertEqual(markup.inline_keyboard[0][0].text, "👁 თვალყური ადევნე")

        asyncio.run(_test())

    def test_history_line_all_parts(self):
        from datetime import datetime, timezone, timedelta

        first_seen = (datetime.now(timezone.utc) - timedelta(days=47)).isoformat()
        listing = PropertyListing(
            id="myhome_123",
            source="myhome",
            source_id="123",
            title="ბინა",
            price_usd=69000.0,
            area_m2=50.0,
            url="https://myhome.ge/pr/123",
            first_seen_at=first_seen,
            initial_price_usd=78000.0,
        )
        dup_info = {
            "duplicate_count": 2,
            "first_seen_at": (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),
            "sources": {"myhome", "ss_ge"},
        }
        msg = self.notifier.format_message(listing, dup_info=dup_info)
        self.assertIn("📅 <b>ისტორია:</b> ", msg)
        self.assertIn("პირველად ვნახეთ 47 დღის წინ", msg)
        self.assertIn("ფასი: $78,000 → $69,000", msg)
        self.assertIn("ასევე იდო SS.ge-ზე", msg)
        self.assertIn(
            "📅 <b>ისტორია:</b> პირველად ვნახეთ 47 დღის წინ · ფასი: $78,000 → $69,000 · ასევე იდო SS.ge-ზე",
            msg,
        )

    def test_history_price_ignores_another_listing_of_the_group(self):
        """A dearer duplicate is another seller's asking price, not this flat's price history."""
        listing = PropertyListing(
            id="myhome_5",
            source="myhome",
            source_id="5",
            title="ბინა",
            price_usd=70000.0,
            area_m2=50.0,
            url="https://myhome.ge/pr/5",
            initial_price_usd=70000.0,
        )
        dup_info = {"duplicate_count": 2, "sources": {"myhome"}, "max_initial_price_usd": 90000.0}
        msg = self.notifier.format_message(listing, dup_info=dup_info)
        self.assertNotIn("ფასი: $90,000", msg)
        self.assertNotIn("📅", msg)

    def test_history_line_missing_parts_and_days_under_one(self):
        from datetime import datetime, timezone, timedelta

        # 1. days < 1 (seen today / 0 days ago) -> first seen omitted
        today = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        listing = PropertyListing(
            id="myhome_124",
            source="myhome",
            source_id="124",
            title="ბინა",
            price_usd=69000.0,
            area_m2=50.0,
            url="https://myhome.ge/pr/124",
            first_seen_at=today,
            initial_price_usd=78000.0,
        )
        dup_info = {
            "duplicate_count": 2,
            "sources": {"myhome"},  # same portal only
        }
        msg = self.notifier.format_message(listing, dup_info=dup_info)
        self.assertIn("📅 <b>ისტორია:</b> ფასი: $78,000 → $69,000", msg)
        self.assertNotIn("პირველად ვნახეთ", msg)
        self.assertNotIn("ასევე იდო", msg)

        # 2. Only another portal
        listing2 = PropertyListing(
            id="ss_ge_125",
            source="ss_ge",
            source_id="125",
            title="ბინა",
            price_usd=70000.0,
            area_m2=50.0,
            url="https://ss.ge/pr/125",
        )
        dup_info2 = {
            "duplicate_count": 2,
            "sources": {"ss_ge", "myhome"},
        }
        msg2 = self.notifier.format_message(listing2, dup_info=dup_info2)
        self.assertIn("📅 <b>ისტორია:</b> ასევე იდო MyHome.ge-ზე", msg2)
        self.assertNotIn("პირველად ვნახეთ", msg2)
        self.assertNotIn("ფასი:", msg2)

        # 3. No history data at all -> no history line
        listing3 = PropertyListing(
            id="myhome_126",
            source="myhome",
            source_id="126",
            title="ბინა",
            price_usd=70000.0,
            area_m2=50.0,
            url="https://myhome.ge/pr/126",
        )
        msg3 = self.notifier.format_message(listing3)
        self.assertNotIn("ისტორია", msg3)

    def test_history_line_is_last_and_truncation_drops_it_first(self):
        from datetime import datetime, timezone, timedelta

        first_seen = (datetime.now(timezone.utc) - timedelta(days=50)).isoformat()
        listing = PropertyListing(
            id="myhome_127",
            source="myhome",
            source_id="127",
            title="ბინა",
            price_usd=60000.0,
            area_m2=50.0,
            url="https://myhome.ge/pr/127",
            first_seen_at=first_seen,
            initial_price_usd=70000.0,
        )
        dup_info = {
            "duplicate_count": 2,
            "sources": {"myhome", "ss_ge"},
        }
        msg = self.notifier.format_message(listing, dup_info=dup_info)
        non_empty_lines = [line for line in msg.split("\n") if line.strip()]
        self.assertTrue(non_empty_lines[-1].startswith("📅 <b>ისტორია:</b>"))

        # Truncation drops the last line first and preserves link
        truncated = TelegramNotifier._truncate_caption(msg, max_len=len(msg) - 20)
        self.assertIn("განცხადების ლინკი", truncated)
        self.assertNotIn("ისტორია", truncated)

    def test_history_line_applies_to_rent(self):
        listing = PropertyListing(
            id="myhome_rent_1",
            source="myhome",
            source_id="rent_1",
            deal_type="rent",
            title="ქირავდება ბინა",
            price_usd=500.0,
            initial_price_usd=600.0,
            area_m2=50.0,
            url="https://myhome.ge/pr/rent_1",
        )
        msg = self.notifier.format_message(listing)
        self.assertIn("📅 <b>ისტორია:</b> ფასი: $600 → $500", msg)


if __name__ == "__main__":
    unittest.main()
