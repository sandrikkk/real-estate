import unittest
from core.models import SearchFilters
from scrapers.myhome import MyHomeScraper, _extract_phone_number


class TestMyHomeScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = MyHomeScraper()
        self.filters = SearchFilters()

    def test_build_api_url_parameters(self):
        url = self.scraper._build_api_url(self.filters, page=1)
        self.assertIn("deal_types=1", url)
        self.assertIn("real_estate_type_id=1", url)
        self.assertIn("currency_id=2", url)
        self.assertIn("price_from=48000", url)
        self.assertIn("price_to=72000", url)
        self.assertIn("area_from=48", url)
        self.assertIn("area_to=62", url)
        self.assertIn("statuses=1,2", url)
        self.assertIn("conditions=1,2,3,5,8", url)
        self.assertIn("page=1", url)

    def test_extract_phone_number_from_comment(self):
        # Unmasked mobile written in seller's comment
        comment = "სასწრაფოდ იყიდება ბინა, დარეკეთ ნომერზე 599180118 ან 577 12 34 56"
        masked_phone = "555112***"
        phone = _extract_phone_number(masked_phone, comment)
        self.assertEqual(phone, "+995 599 18 01 18")

    def test_extract_phone_number_fallback(self):
        # No comment phone, but raw phone is full number
        phone = _extract_phone_number("599123456", "ბინა არის კარგ მდგომარეობაში")
        self.assertEqual(phone, "+995 599 12 34 56")

        # Masked phone with no comment phone
        phone_masked = _extract_phone_number("555112***", "ბინა არის კარგ მდგომარეობაში")
        self.assertEqual(phone_masked, "555112***")

    def test_normalize_item(self):
        raw_item = {
            "id": 25908342,
            "metro_station_id": 10,
            "deal_type_id": 1,
            "real_estate_type_id": 1,
            "status_id": 2,
            "price": {
                "1": {"price_total": 177657, "price_square": 3483},
                "2": {"price_total": 68000, "price_square": 1333}
            },
            "images": [
                {"large": "https://static-statements.tnet.ge/uploads/test.webp", "is_main": True}
            ],
            "address": "ნავთლუღის ქ.",
            "area": 51,
            "bedroom": "1",
            "room": "2",
            "dynamic_title": "იყიდება 2 ოთახიანი ბინა ისანში",
            "floor": 3,
            "total_floors": 12,
            "district_name": "ისანი",
            "comment": "დარეკეთ 599180118",
            "user_type": {"type": "physical"},
            "user_phone_number": "555112***"
        }

        listing = self.scraper._normalize_item(raw_item, filters=self.filters)
        self.assertIsNotNone(listing)
        self.assertEqual(listing.id, "myhome_25908342")
        self.assertEqual(listing.price_usd, 68000.0)
        self.assertEqual(listing.area_m2, 51.0)
        self.assertEqual(listing.price_per_m2, 1333.33)
        self.assertEqual(listing.metro_station_id, 10)
        self.assertEqual(listing.metro_station_name, "ისანი")
        self.assertEqual(listing.rooms, 2)
        self.assertEqual(listing.bedrooms, 1)
        self.assertTrue(listing.is_owner)
        self.assertEqual(listing.phone_number, "+995 599 18 01 18")
        self.assertEqual(len(listing.images), 1)


if __name__ == "__main__":
    unittest.main()
