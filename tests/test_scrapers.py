import asyncio
import unittest
from core.models import SearchFilters
from scrapers.myhome import MyHomeScraper
from scrapers.ss_ge import SSGeScraper
from scrapers.area_ge import AreaGeScraper


class TestScrapers(unittest.TestCase):
    def setUp(self):
        self.filters = SearchFilters(
            price_min_usd=50000,
            price_max_usd=100000,
            area_min_m2=40,
            area_max_m2=120
        )

    def test_myhome_scraper_parsing(self):
        scraper = MyHomeScraper()
        sample_raw = {
            "id": 8881234,
            "dynamic_title": "იყიდება 2 ოთახიანი ბინა საბურთალოზე",
            "comment": "კარგი რემონტით",
            "price": {
                "2": {"price_total": 75000}
            },
            "area": 55,
            "urban_name": "საბურთალო",
            "district_name": "ვაკე-საბურთალო",
            "city_name": "თბილისი",
            "address": "ვაჟა-ფშაველას გამზ. 10",
            "floor": 4,
            "total_floors": 12,
            "bedroom": "1",
            "room": "2",
            "images": [
                {"large": "https://example.com/img1.jpg"}
            ]
        }
        normalized = scraper._normalize_item(sample_raw)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized.id, "myhome_8881234")
        self.assertEqual(normalized.price_usd, 75000)
        self.assertEqual(normalized.area_m2, 55)
        self.assertEqual(normalized.price_per_m2, round(75000 / 55, 2))
        self.assertEqual(normalized.district, "საბურთალო")
        self.assertEqual(len(normalized.images), 1)

    def test_myhome_scraper_build_search_url_custom_url(self):
        scraper = MyHomeScraper()
        custom_url = "https://www.myhome.ge/udzravi-qoneba/iyideba/bina/tbilisi/?price_from=45000&price_to=75000&page=1"
        filters = SearchFilters(myhome_url=custom_url)
        built_url_p1 = scraper._build_search_url(filters, page=1)
        built_url_p2 = scraper._build_search_url(filters, page=2)
        self.assertIn("page=1", built_url_p1)
        self.assertIn("page=2", built_url_p2)
        self.assertIn("price_from=45000", built_url_p2)

    def test_ss_ge_scraper_parsing(self):
        scraper = SSGeScraper()
        sample_raw = {
            "applicationId": 7779876,
            "title": "იყიდება 3 ოთახიანი ბინა ვაკეში",
            "description": "ახალი გარემონტებული",
            "price": {
                "priceUsd": 120000,
                "priceGeo": 324000
            },
            "totalArea": 80,
            "address": {
                "cityTitle": "თბილისი",
                "districtTitle": "ვაკე-საბურთალო",
                "subdistrictTitle": "ვაკე",
                "streetTitle": "ჭავჭავაძის გამზ.",
                "streetNumber": "50"
            },
            "floorNumber": "6",
            "totalAmountOfFloor": 10,
            "numberOfBedrooms": 2,
            "appImages": [
                {"fileName": "https://example.com/ss_img.jpg"}
            ],
            "detailUrl": "iyideba-3-otaxiani-bina-vakeshi-7779876"
        }
        normalized = scraper._normalize_item(sample_raw)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized.id, "ss_ge_7779876")
        self.assertEqual(normalized.price_usd, 120000)
        self.assertEqual(normalized.price_gel, 324000)
        self.assertEqual(normalized.area_m2, 80)
        self.assertEqual(normalized.price_per_m2, 1500.0)
        self.assertEqual(normalized.district, "ვაკე")
        self.assertEqual(normalized.street, "ჭავჭავაძის გამზ. 50")
        self.assertEqual(normalized.url, "https://home.ss.ge/ka/udzravi-qoneba/iyideba-3-otaxiani-bina-vakeshi-7779876")

    def test_area_ge_resilience(self):
        scraper = AreaGeScraper()
        # Area.ge fetch should gracefully return empty list without crashing
        async def run_fetch():
            return await scraper.fetch_listings(self.filters)
        results = asyncio.run(run_fetch())
        self.assertIsInstance(results, list)


if __name__ == "__main__":
    unittest.main()
