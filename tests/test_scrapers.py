import asyncio
import unittest
from core.models import SearchFilters
from scrapers.myhome import MyHomeScraper
from scrapers.ss_ge import SSGeScraper
from scrapers.area_ge import AreaGeScraper


class TestScrapers(unittest.TestCase):
    def setUp(self):
        self.filters = SearchFilters(
            price_min_usd=50000, price_max_usd=100000, area_min_m2=40, area_max_m2=120
        )

    def test_myhome_scraper_parsing(self):
        scraper = MyHomeScraper()
        sample_raw = {
            "id": 8881234,
            "dynamic_title": "იყიდება 2 ოთახიანი ბინა საბურთალოზე",
            "comment": "კარგი რემონტით",
            "price": {"2": {"price_total": 75000}},
            "area": 55,
            "urban_name": "საბურთალო",
            "district_name": "ვაკე-საბურთალო",
            "city_name": "თბილისი",
            "address": "ვაჟა-ფშაველას გამზ. 10",
            "floor": 4,
            "total_floors": 12,
            "bedroom": "1",
            "room": "2",
            "images": [{"large": "https://example.com/img1.jpg"}],
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
            "price": {"priceUsd": 120000, "priceGeo": 324000},
            "totalArea": 80,
            "address": {
                "cityTitle": "თბილისი",
                "districtTitle": "ვაკე-საბურთალო",
                "subdistrictTitle": "ვაკე",
                "streetTitle": "ჭავჭავაძის გამზ.",
                "streetNumber": "50",
            },
            "floorNumber": "6",
            "totalAmountOfFloor": 10,
            "numberOfBedrooms": 2,
            "appImages": [{"fileName": "https://example.com/ss_img.jpg"}],
            "detailUrl": "iyideba-3-otaxiani-bina-vakeshi-7779876",
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
        self.assertEqual(
            normalized.url,
            "https://home.ss.ge/ka/udzravi-qoneba/iyideba-3-otaxiani-bina-vakeshi-7779876",
        )

    def test_area_ge_resilience(self):
        scraper = AreaGeScraper()

        # Area.ge fetch should gracefully return empty list without crashing
        async def run_fetch():
            return await scraper.fetch_listings(self.filters)

        results = asyncio.run(run_fetch())
        self.assertIsInstance(results, list)

    def test_myhome_api_url_with_owner_filters(self):
        scraper = MyHomeScraper()
        f_owner = SearchFilters(owner_type="owner")
        url_owner = scraper._build_api_url(f_owner, page=1)
        self.assertIn("owner_type=physical", url_owner)

        f_agent = SearchFilters(owner_type="agent")
        url_agent = scraper._build_api_url(f_agent, page=1)
        self.assertIn("owner_type=agent", url_agent)

        f_all = SearchFilters(owner_type="all")
        url_all = scraper._build_api_url(f_all, page=1)
        self.assertNotIn("owner_type=", url_all)

    def test_ss_ge_url_with_owner_filters(self):
        scraper = SSGeScraper()
        f_owner = SearchFilters(owner_type="owner")
        url_owner = scraper._build_search_url(f_owner, page=1)
        self.assertIn("individualType=1", url_owner)

        f_agent = SearchFilters(owner_type="agent")
        url_agent = scraper._build_search_url(f_agent, page=1)
        self.assertIn("individualType=2", url_agent)

        f_all = SearchFilters(owner_type="all")
        url_all = scraper._build_search_url(f_all, page=1)
        self.assertNotIn("individualType=", url_all)

    def test_ss_ge_search_url_generation(self):
        scraper = SSGeScraper()
        f_owner = SearchFilters(
            deal_type="sale",
            price_min_usd=50000,
            price_max_usd=100000,
            owner_type="owner",
            area_min_m2=40,
            area_max_m2=120,
        )
        url_owner = scraper._build_search_url(f_owner, page=1)
        self.assertIn("/iyideba", url_owner)
        self.assertIn("priceFrom=50000", url_owner)
        self.assertIn("areaFrom=40", url_owner)
        self.assertIn("areaTo=120", url_owner)
        self.assertIn("individualType=1", url_owner)

        f_rent = SearchFilters(
            deal_type="rent", rent_price_min_usd=400, rent_price_max_usd=1200, owner_type="agent"
        )
        url_rent = scraper._build_search_url(f_rent, page=2)
        self.assertIn("/qiravdeba", url_rent)
        self.assertIn("priceFrom=400", url_rent)
        self.assertIn("priceTo=1200", url_rent)
        self.assertIn("individualType=2", url_rent)
        self.assertIn("page=2", url_rent)

    def test_ss_ge_non_apartment_rejected(self):
        scraper = SSGeScraper()
        house_item = {
            "id": 26168401,
            "real_estate_type_id": 2,  # Private house
            "dynamic_title": "იყიდება 2 ოთახიანი კერძო სახლი",
            "price": {"2": {"price_total": 65000}},
            "area": 62,
        }
        self.assertIsNone(scraper._normalize_item(house_item))

    def test_ss_ge_url_duplicate_path_prevention(self):
        scraper = SSGeScraper()
        item_with_prefix = {
            "id": 26173851,
            "real_estate_type_id": 1,
            "detailUrl": "ka/udzravi-qoneba/iyideba-1-otaxiani-bina-26173851",
            "price": {"2": {"price_total": 67000}},
            "area": 35,
        }
        listing = scraper._normalize_item(item_with_prefix)
        self.assertIsNotNone(listing)
        self.assertEqual(
            listing.url, "https://home.ss.ge/ka/udzravi-qoneba/iyideba-1-otaxiani-bina-26173851"
        )
        self.assertNotIn("udzravi-qoneba/ka/udzravi-qoneba", listing.url)

    def test_ss_ge_api_response_normalization(self):
        scraper = SSGeScraper()
        api_raw = {
            "id": 26173851,
            "dynamic_title": "იყიდება 1 ოთახიანი ბინა დიდ დიღომში",
            "dynamic_slug": "iyideba-1-otaxiani-bina-did-dighomshi",
            "comment": "სასწრაფოდ იყიდება ბინა. 595 167976 ვარ მეპატრონე",
            "area": 31.5,
            "price": {"1": {"price_total": 174736}, "2": {"price_total": 67000}},
            "city_name": "თბილისი",
            "urban_name": "დიდი დიღომი",
            "district_name": "ვაკე-საბურთალო",
            "address": "აღმაშენებლის ხეივ. 188",
            "floor": 7,
            "total_floors": 13,
            "room": "1",
            "bedroom": "1",
            "user_type": {"type": "physical"},
            "user_phone_number": "595167***",
            "condition_id": 1,
            "status_id": 2,
            "images": [{"large": "https://static-statements.tnet.ge/uploads/img1.webp"}],
            "last_updated": "2026-09-27 10:02:27",
        }
        listing = scraper._normalize_item(api_raw)
        self.assertIsNotNone(listing)
        self.assertEqual(listing.id, "ss_ge_26173851")
        self.assertEqual(listing.source, "ss_ge")
        self.assertEqual(listing.deal_type, "sale")
        self.assertEqual(listing.price_usd, 67000.0)
        self.assertEqual(listing.price_gel, 174736.0)
        self.assertEqual(listing.area_m2, 31.5)
        self.assertEqual(listing.district, "დიდი დიღომი")
        self.assertIsNone(listing.subdistrict)
        self.assertEqual(
            listing.url,
            "https://home.ss.ge/ka/udzravi-qoneba/iyideba-1-otaxiani-bina-did-dighomshi-26173851",
        )
        self.assertTrue(listing.is_owner)
        self.assertEqual(listing.phone_number, "+995 595 16 79 76")
        self.assertEqual(listing.condition_id, 1)
        self.assertEqual(listing.condition_name, "ახალი გარემონტებული")
        self.assertEqual(len(listing.images), 1)

    def test_ss_ge_real_listing_url_and_id_fallback(self):
        scraper = SSGeScraper()
        # Item with standard SSR detailUrl
        ssr_item = {
            "applicationId": 36774387,
            "title": "იყიდება 3 ოთახიანი ბინა დიდ დიღომში",
            "price": {"priceUsd": 98000},
            "totalArea": 70,
            "detailUrl": "iyideba-3-otaxiani-bina-did-dighomshi-36774387",
        }
        listing = scraper._normalize_item(ssr_item)
        self.assertIsNotNone(listing)
        self.assertEqual(
            listing.url,
            "https://home.ss.ge/ka/udzravi-qoneba/iyideba-3-otaxiani-bina-did-dighomshi-36774387",
        )
        self.assertEqual(listing.id, "ss_ge_36774387")

        # Item with numeric ID only (no detailUrl)
        fallback_item = {
            "applicationId": 36774387,
            "title": "იყიდება 3 ოთახიანი ბინა დიდ დიღომში",
            "price": {"priceUsd": 98000},
            "totalArea": 70,
        }
        listing_fb = scraper._normalize_item(fallback_item)
        self.assertIsNotNone(listing_fb)
        self.assertEqual(
            listing_fb.url,
            "https://home.ss.ge/ka/udzravi-qoneba/36774387",
        )


if __name__ == "__main__":
    unittest.main()
