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
        self.assertIn('🔗 <a href="https://www.myhome.ge/ka/pr/9999">განცხადების ლინკი (MyHome.ge)</a>', msg)

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


if __name__ == "__main__":
    unittest.main()
