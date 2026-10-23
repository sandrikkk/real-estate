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
        self.assertIn("real_estate_types=1", url)
        self.assertIn("currency_id=2", url)
        self.assertIn("price_from=48000", url)
        self.assertIn("price_to=72000", url)
        self.assertIn("area_from=48", url)
        self.assertIn("area_to=62", url)
        self.assertIn("statuses=1,2", url)
        self.assertIn("conditions=1,2,3,5,8", url)
        self.assertIn("page=1", url)
        self.assertIn("urbans=", url)

    def test_build_api_url_without_districts(self):
        f = SearchFilters(whitelist_districts=[])
        url = self.scraper._build_api_url(f, page=1)
        self.assertNotIn("urbans=", url)

    def test_build_api_url_with_districts(self):
        f = SearchFilters(whitelist_districts=["საბურთალო", "დიდუბე"])
        url = self.scraper._build_api_url(f, page=1)
        self.assertIn("urbans=", url)
        # Didube is 23, Saburtalo includes 47
        self.assertIn("23", url)
        self.assertIn("47", url)

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
                "2": {"price_total": 68000, "price_square": 1333},
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
            "user_phone_number": "555112***",
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

    def test_fetch_listings_pages_past_promoted_block(self):
        # MyHome returns all Super-VIP/VIP+ listings first; regular new listings
        # only start after them. 3 promoted-only pages must not end pagination.
        def item(i, promoted):
            return {
                "id": i,
                "deal_type_id": 1,
                "real_estate_type_id": 1,
                "area": 50,
                "price": {"2": {"price_total": 60000}},
                "is_super_vip": promoted,
            }

        pages = {p: [item(p * 100 + i, True) for i in range(24)] for p in (1, 2, 3)}
        pages[4] = [item(400 + i, False) for i in range(24)]
        pages[5] = [item(500 + i, False) for i in range(24)]
        requested = []

        async def fake_fetch(url):
            page = int(url.split("page=")[1].split("&")[0])
            requested.append(page)
            return pages.get(page)

        import asyncio

        self.scraper._fetch_api_page = fake_fetch
        listings = asyncio.run(self.scraper.fetch_listings(self.filters))

        self.assertEqual(requested, [1, 2, 3, 4])
        self.assertIn("myhome_400", {item.id for item in listings})

    def test_build_search_url_includes_urbans_when_districts_set(self):
        f = SearchFilters(
            deal_type="sale",
            price_min_usd=45000,
            price_max_usd=75000,
            area_min_m2=40,
            area_max_m2=65,
            whitelist_districts=["საბურთალო", "დიდუბე"],
        )
        url = self.scraper._build_search_url(f, page=2)
        self.assertIn("urbans=", url)
        self.assertIn("23", url)  # დიდუბე
        self.assertIn("47", url)  # საბურთალო
        self.assertIn("page=2", url)

    def test_fetch_listings_skips_api_after_first_page_failure(self):
        import asyncio

        api_calls = []
        html_calls = []

        async def failing_api(url):
            api_calls.append(url)
            return None

        async def fake_html(url):
            html_calls.append(url)
            return "<html></html>"

        self.scraper._fetch_api_page = failing_api
        self.scraper.fetch_html = fake_html
        self.scraper._extract_listings_from_html = lambda html: [
            {
                "id": len(html_calls) * 100 + i,
                "deal_type_id": 1,
                "real_estate_type_id": 1,
                "area": 50,
                "price": {"2": {"price_total": 60000}},
                "is_super_vip": False,
            }
            for i in range(10)
        ]

        listings = asyncio.run(self.scraper.fetch_listings(self.filters))
        self.assertEqual(len(api_calls), 1)
        self.assertGreaterEqual(len(html_calls), 2)
        self.assertGreater(len(listings), 0)


if __name__ == "__main__":
    unittest.main()
