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
            title="იყიდება 2 ოთახიანი ისანში",
            price_usd=62000.0,
            area_m2=50.0,
            city="თბილისი",
            district="ისანი",
            street="ნავთლუღის ქ.",
            floor="4",
            total_floors=9,
            rooms=2,
            bedrooms=1,
            url="https://www.myhome.ge/ka/pr/9999",
            metro_station_id=10,
            metro_station_name="ისანი",
            condition_id=1,
            condition_name="ახალი გარემონტებული",
            is_owner=True,
            is_hot_deal=True,
            phone_number="+995 599 12 34 56",
        )

        msg = self.notifier.format_message(listing)

        # 1. Mobile Header with HOT DEAL
        self.assertIn("🚨 <b>HOT DEAL | $62,000 | 50.0 მ² | $1,240/მ²</b>", msg)

        # 2. Location line with Metro
        self.assertIn("📍 <b>ისანი</b> | ნავთლუღის ქ. | 🚇 <b>მ. ისანი</b>", msg)

        # 3. Details line with Owner, Floor, and Renovation
        self.assertIn("👤 <b>მესაკუთრე (Owner)</b>", msg)
        self.assertIn("🏢 სართული: <b>4/9</b>", msg)
        self.assertIn("🛠 <b>ახალი გარემონტებული</b>", msg)

        # 4. Direct link
        self.assertIn('🔗 <a href="https://www.myhome.ge/ka/pr/9999">განცხადების ლინკი (MyHome.ge)</a>', msg)

        # 5. One-tap call link
        self.assertIn('📞 <a href="tel:+995599123456">+995 599 12 34 56</a>', msg)

    def test_agent_and_masked_phone(self):
        listing = PropertyListing(
            id="myhome_8888",
            source="myhome",
            source_id="8888",
            title="იყიდება 2 ოთახიანი დიდუბეში",
            price_usd=70000.0,
            area_m2=50.0,
            city="თბილისი",
            district="დიდუბე",
            floor="2",
            url="https://www.myhome.ge/ka/pr/8888",
            is_owner=False,
            is_hot_deal=False,
            phone_number="555112***",
        )

        msg = self.notifier.format_message(listing)

        # 1. Standard Header
        self.assertIn("🏠 <b>$70,000 | 50.0 მ² | $1,400/მ²</b>", msg)

        # 2. Agent designation
        self.assertIn("👤 <b>სააგენტო (Agent)</b>", msg)

        # 3. Masked phone prompt to view on site
        self.assertIn("<code>555112***</code>", msg)
        self.assertIn("ნომრის სანახავად გადადით ლინკზე", msg)


if __name__ == "__main__":
    unittest.main()
