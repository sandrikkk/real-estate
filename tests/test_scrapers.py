import asyncio
import unittest
import urllib.parse
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

    def test_ss_ge_deal_type_detection(self):
        scraper = SSGeScraper()
        # Item with dealType=1 (rent)
        rent_item = {
            "applicationId": 36774390,
            "dealType": 1,
            "title": "ბინა საბურთალოზე",
            "price": {"priceUsd": 600},
            "totalArea": 50,
        }
        res_rent = scraper._normalize_item(rent_item)
        self.assertIsNotNone(res_rent)
        self.assertEqual(res_rent.deal_type, "rent")

        # Item with Georgian "ქირავდება" in title
        rent_geo_title = {
            "applicationId": 36774391,
            "title": "ქირავდება 2 ოთახიანი ბინა ვაკეში",
            "price": {"priceUsd": 700},
            "totalArea": 55,
        }
        res_geo = scraper._normalize_item(rent_geo_title)
        self.assertIsNotNone(res_geo)
        self.assertEqual(res_geo.deal_type, "rent")

        # Item with dealType=4 (sale)
        sale_item = {
            "applicationId": 36774392,
            "dealType": 4,
            "title": "ბინა დიდუბეში",
            "price": {"priceUsd": 60000},
            "totalArea": 50,
        }
        res_sale = scraper._normalize_item(sale_item)
        self.assertIsNotNone(res_sale)
        self.assertEqual(res_sale.deal_type, "sale")

    def test_myhome_price_extraction_and_currency_handling(self):
        scraper = MyHomeScraper()
        # Case 1: Standard price dict with GEL (1) and USD (2)
        item1 = {
            "price": {
                "1": {"price_total": 317301, "price_square": 5717},
                "2": {"price_total": 122100, "price_square": 2200},
            }
        }
        usd, gel = scraper._extract_prices(item1)
        self.assertEqual(usd, 122100.0)
        self.assertEqual(gel, 317301.0)

        # Case 2: total_price with currency_id=1 (GEL) must not be treated as USD
        item2 = {"total_price": 122100, "currency_id": 1}
        usd2, gel2 = scraper._extract_prices(item2)
        self.assertEqual(gel2, 122100.0)
        self.assertAlmostEqual(usd2, round(122100 / 2.65, 2))

        # Case 3: total_price with currency_id=2 (USD)
        item3 = {"total_price": 122100, "currency_id": 2}
        usd3, gel3 = scraper._extract_prices(item3)
        self.assertEqual(usd3, 122100.0)

    def test_ss_ge_search_url_rooms_price_type_and_subdistricts(self):
        scraper = SSGeScraper()
        # Case 1: rooms_min=1 must NOT restrict to rooms=1 (1-room only)
        f_one_plus = SearchFilters(
            deal_type="sale",
            price_min_usd=45000,
            price_max_usd=75000,
            area_min_m2=40,
            area_max_m2=65,
            rooms_min=1,
            whitelist_districts=["დიდუბე", "ნაძალადევი"],
        )
        url_1 = scraper._build_search_url(f_one_plus, page=1)
        self.assertIn("priceType=1", url_1)
        self.assertIn("priceFrom=45000", url_1)
        self.assertIn("priceTo=75000", url_1)
        self.assertNotIn("rooms=", url_1)
        self.assertIn("subdistrictIds=1,37,39,41,42", url_1)

        # Case 2: rooms_min=2 must pass rooms=2,3,4,5
        f_two_plus = SearchFilters(
            deal_type="sale",
            price_min_usd=90000,
            price_max_usd=115000,
            rooms_min=2,
            whitelist_districts=["ნაძალადევი"],
        )
        url_2 = scraper._build_search_url(f_two_plus, page=1)
        self.assertIn("priceType=1", url_2)
        self.assertIn("rooms=2,3,4,5", url_2)
        self.assertIn("subdistrictIds=37,39,41,42", url_2)

    def test_orchestrator_builds_separate_per_user_envelopes(self):
        from core.filters import ListingFilter
        from core.models import UserSubscription
        from main import RealEstateOrchestrator

        orch = RealEstateOrchestrator.__new__(RealEstateOrchestrator)
        orch.filter_engine = ListingFilter(SearchFilters())

        u1 = UserSubscription(
            chat_id="1105321687",
            deal_type="sale",
            price_min_usd=45000,
            price_max_usd=75000,
            area_min_m2=40,
            area_max_m2=65,
            rooms_min=1,
            districts=["დიდუბე", "საბურთალო"],
            owner_type="all",
        )
        u2 = UserSubscription(
            chat_id="646957970",
            deal_type="sale",
            price_min_usd=90000,
            price_max_usd=115000,
            area_min_m2=40,
            area_max_m2=120,
            rooms_min=2,
            districts=["ნაძალადევი"],
            owner_type="all",
        )

        envelopes = orch._build_search_envelopes("sale", [u1, u2])
        self.assertEqual(len(envelopes), 2)
        self.assertEqual((envelopes[0].price_min_usd, envelopes[0].price_max_usd), (45000, 75000))
        self.assertEqual((envelopes[0].area_min_m2, envelopes[0].area_max_m2), (40, 65))
        self.assertEqual((envelopes[1].price_min_usd, envelopes[1].price_max_usd), (90000, 115000))
        self.assertEqual((envelopes[1].area_min_m2, envelopes[1].area_max_m2), (40, 120))

    def test_scrapers_extract_author_id_and_listings_count(self):
        mh = MyHomeScraper()
        mh_item = mh._normalize_item(
            {
                "id": 990011,
                "dynamic_title": "იყიდება ბინა საბურთალოზე",
                "price": {"2": {"price_total": 85000}},
                "area": 62,
                "urban_name": "საბურთალო",
                "user_id": 12345,
                "user_statements_count": 7,
            }
        )
        self.assertIsNotNone(mh_item)
        self.assertEqual(mh_item.author_id, "12345")
        self.assertEqual(mh_item.author_listings_count, 7)

        ss = SSGeScraper()
        ss_item = ss._normalize_item(
            {
                "applicationId": 554433,
                "title": "იყიდება ბინა ვაკეში",
                "price": {"priceUsd": 110000},
                "totalArea": 70,
                "address": {"districtTitle": "ვაკე"},
                "userId": "u_8899",
                "userApplicationCount": 5,
            }
        )
        self.assertIsNotNone(ss_item)
        self.assertEqual(ss_item.author_id, "u_8899")
        self.assertEqual(ss_item.author_listings_count, 5)

    def test_ss_ge_build_api_payload(self):
        scraper = SSGeScraper()
        f_sale = SearchFilters(
            deal_type="sale",
            price_min_usd=45000,
            price_max_usd=75000,
            area_min_m2=40,
            area_max_m2=65,
            rooms_min=1,
            owner_type="owner",
            whitelist_districts=["დიდუბე", "ნაძალადევი"],
        )
        payload_1 = scraper._build_api_payload(f_sale, page=1)
        self.assertEqual(payload_1["realEstateType"], 5)
        self.assertEqual(payload_1["realEstateDealType"], 4)
        self.assertEqual(payload_1["cityIdList"], [95])
        self.assertEqual(payload_1["currencyId"], 2)
        self.assertEqual(payload_1["priceType"], 1)
        self.assertEqual(payload_1["priceFrom"], 45000)
        self.assertEqual(payload_1["priceTo"], 75000)
        self.assertEqual(payload_1["areaFrom"], 40)
        self.assertEqual(payload_1["areaTo"], 65)
        self.assertNotIn("rooms", payload_1)
        self.assertEqual(payload_1["subdistrictIds"], [1, 37, 39, 41, 42])
        self.assertEqual(payload_1["realEstateStatuses"], [2, 453])
        self.assertEqual(
            payload_1["advancedSearch"],
            {"individualEntityOnly": True, "realEstateStates": [9, 11, 12, 15, 16]},
        )
        self.assertEqual(payload_1["page"], 1)
        self.assertEqual(payload_1["order"], 1)

        f_rent = SearchFilters(
            deal_type="rent",
            rent_price_min_usd=500,
            rent_price_max_usd=1000,
            rooms_min=2,
            owner_type="all",
        )
        payload_2 = scraper._build_api_payload(f_rent, page=2)
        self.assertEqual(payload_2["realEstateDealType"], 1)
        self.assertEqual(payload_2["priceFrom"], 500)
        self.assertEqual(payload_2["priceTo"], 1000)
        self.assertEqual(payload_2["rooms"], [2, 3, 4, 5])
        self.assertEqual(payload_2["realEstateStatuses"], [2, 453])
        self.assertNotIn("advancedSearch", payload_2)
        self.assertEqual(payload_2["page"], 2)

    def test_ss_ge_search_url_mirrors_myhome_status_and_condition_filters(self):
        scraper = SSGeScraper()
        url_sale = scraper._build_search_url(SearchFilters(deal_type="sale"), page=1)
        self.assertIn("realEstateStatuses=2,453", url_sale)
        self.assertIn(
            "advancedSearch=" + urllib.parse.quote('{"realEstateStates":[9,11,12,15,16]}'),
            url_sale,
        )

        url_rent = scraper._build_search_url(SearchFilters(deal_type="rent"), page=1)
        self.assertIn("realEstateStatuses=2,453", url_rent)
        self.assertNotIn("advancedSearch=", url_rent)

    def test_ss_ge_parse_statement_details_data(self):
        scraper = SSGeScraper()
        api_details = {
            "applicationId": 37161305,
            "price": {"priceUsd": 75000, "priceGeo": 204000},
            "applicationPhones": [{"phoneNumber": "571045829"}],
            "userEntityType": "Broker",
            "userId": "u-123",
            "userApplicationCount": 16,
            "state": "ახალი გარემონტებული",
            "realEstateStatusId": 2,
            "priceLevel": "საშუალო ფასი",
            "description": {"ka": "იყიდება ბინა დიდუბეში"},
        }
        parsed = scraper._parse_statement_details_data(api_details)
        self.assertIsNotNone(parsed)
        self.assertFalse(parsed["is_owner"])
        self.assertEqual(parsed["phone_number"], "+995 571 04 58 29")
        self.assertEqual(parsed["condition_id"], 1)
        self.assertEqual(parsed["condition_name"], "ახალი გარემონტებული")
        self.assertEqual(parsed["status_id"], 2)
        self.assertEqual(parsed["price_label"], "საშუალო ფასი")
        self.assertEqual(parsed["comment"], "იყიდება ბინა დიდუბეში")
        self.assertEqual(parsed["price_usd"], 75000.0)
        self.assertEqual(parsed["price_gel"], 204000.0)
        self.assertEqual(parsed["author_id"], "u-123")
        self.assertEqual(parsed["author_listings_count"], 16)

    def test_ss_ge_fetch_listings_uses_api_gateway_first(self):
        scraper = SSGeScraper(max_pages=1)
        html_called = False

        async def mock_fetch_api(filters, page=1):
            return [
                {
                    "applicationId": 37161305,
                    "title": "იყიდება 2 ოთახიანი ბინა დიდუბეში",
                    "price": {"priceUsd": 75000, "priceGeo": 204000},
                    "totalArea": 54,
                    "address": {"cityTitle": "თბილისი", "subdistrictTitle": "დიდუბე"},
                    "type": 5,
                    "dealType": 4,
                    "detailUrl": "iyideba-2-otaxiani-bina-didubeshi-37161305",
                }
            ]

        async def mock_fetch_html(url):
            nonlocal html_called
            html_called = True
            return None

        scraper._fetch_api_listings = mock_fetch_api
        scraper.fetch_html = mock_fetch_html

        results = asyncio.run(scraper.fetch_listings(self.filters))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "ss_ge_37161305")
        self.assertFalse(html_called)

    def test_ss_ge_resolve_origin_ip_bypasses_cloudflare(self):
        scraper = SSGeScraper(max_pages=1)
        origin_ip = scraper._resolve_origin_ip()
        self.assertIsNotNone(origin_ip)
        self.assertFalse(origin_ip.startswith(("104.", "172.6")))
        resolve_list = scraper._build_origin_resolve_list()
        self.assertIn(f"home.ss.ge:443:{origin_ip}", resolve_list)
        self.assertIn(f"api-gateway.ss.ge:443:{origin_ip}", resolve_list)

    def test_myhome_fetch_statement_details_uses_json_api_first(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        scraper = MyHomeScraper()
        scraper.fetch_html = AsyncMock(return_value="<html>should not be called</html>")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "statement": {
                    "id": 26345700,
                    "condition_id": 1,
                    "is_owner": True,
                    "price": {"2": {"price_total": 65000}},
                }
            }
        }
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.myhome.AsyncSession", return_value=mock_session):
            details = asyncio.run(scraper.fetch_statement_details("26345700"))

        self.assertIsNotNone(details)
        self.assertEqual(details["price_usd"], 65000.0)
        self.assertTrue(details["is_owner"])
        scraper.fetch_html.assert_not_called()

    def test_myhome_fetch_listings_empty_page_does_not_disable_api(self):
        from unittest.mock import AsyncMock

        scraper = MyHomeScraper(max_pages=3)
        page_1_item = {
            "id": 8881234,
            "dynamic_title": "იყიდება 2 ოთახიანი ბინა საბურთალოზე",
            "price": {"2": {"price_total": 75000}},
            "area": 55,
            "urban_name": "საბურთალო",
        }
        scraper._fetch_api_page = AsyncMock(side_effect=[[page_1_item], []])
        scraper.fetch_html = AsyncMock(return_value=None)

        results = asyncio.run(scraper.fetch_listings(self.filters))
        self.assertEqual(len(results), 1)
        self.assertFalse(scraper._api_disabled)
        scraper.fetch_html.assert_not_called()


if __name__ == "__main__":
    unittest.main()
