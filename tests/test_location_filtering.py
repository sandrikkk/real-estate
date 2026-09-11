import unittest
from core.models import UserSubscription, PropertyListing, SearchFilters
from core.filters import ListingFilter
from scrapers.myhome import MyHomeScraper
from scrapers.ss_ge import SSGeScraper


class TestLocationFiltering(unittest.TestCase):
    def setUp(self):
        self.filters = SearchFilters()
        self.engine = ListingFilter(self.filters)
        self.sandro = UserSubscription(
            chat_id="1105321687",
            deal_type="sale",
            price_min_usd=10000,
            price_max_usd=200000,
            districts=["დიდუბე", "ნაძალადევი"],
            is_active=True
        )

    def test_tamuna_api_listing_rejected_for_didube_nadzaladevi(self):
        """
        Tests the exact API payload from user prompt:
        ID: 26020564, urban_name: 'საბურთალო', district_name: 'ვაკე-საბურთალო', address: 'კოსტავა მ. ქ. 80'.
        Must NOT match user Sandro who subscribed to Didube & Nadzaladevi.
        """
        listing = PropertyListing(
            id="myhome_26020564",
            source="myhome",
            source_id="26020564",
            deal_type="sale",
            title="იყიდება 1 ოთახიანი ბინა საბურთალოზე",
            price_usd=160000,
            area_m2=58,
            district="საბურთალო",
            subdistrict=None,
            street="კოსტავა მ. ქ. 80",
            metro_station_id=5,
            metro_station_name="ტექნიკური უნივერსიტეტი",
            url="https://myhome.ge/pr/26020564"
        )
        self.assertFalse(
            self.engine.matches_user(self.sandro, listing),
            "Saburtalo listing must not match Didube/Nadzaladevi subscriber"
        )

    def test_didube_nadzaladevi_does_not_leak_other_districts(self):
        """
        Tests that Chugureti, Gldani, Mukhiani, Saburtalo do NOT leak into Didube/Nadzaladevi,
        while real Didube and Nadzaladevi (and Sanzona) DO match.
        """
        # 1. Real Didube -> Should MATCH
        didube = PropertyListing(
            id="1", source="myhome", source_id="1", url="http://x",
            title="ბინა დიდუბეში", price_usd=50000, area_m2=50, district="დიდუბე"
        )
        self.assertTrue(self.engine.matches_user(self.sandro, didube))

        # 2. Real Nadzaladevi -> Should MATCH
        nadzaladevi = PropertyListing(
            id="2", source="myhome", source_id="2", url="http://x",
            title="ბინა ნაძალადევში", price_usd=48000, area_m2=50, district="ნაძალადევი"
        )
        self.assertTrue(self.engine.matches_user(self.sandro, nadzaladevi))

        # 3. Sanzona (sub-neighborhood of Nadzaladevi) -> Should MATCH
        sanzona = PropertyListing(
            id="3", source="myhome", source_id="3", url="http://x",
            title="ბინა სანზონაში", price_usd=45000, area_m2=50, district="სანზონა"
        )
        self.assertTrue(self.engine.matches_user(self.sandro, sanzona))

        # 4. Chugureti (formerly leaked via დიდუბე-ჩუღურეთი) -> Must NOT match
        chugureti = PropertyListing(
            id="4", source="myhome", source_id="4", url="http://x",
            title="ბინა ჩუღურეთში მარჯანიშვილზე", price_usd=60000, area_m2=50,
            district="ჩუღურეთი", street="მარჯანიშვილის ქ."
        )
        self.assertFalse(self.engine.matches_user(self.sandro, chugureti))

        # 5. Gldani (formerly leaked via გლდანი-ნაძალადევი) -> Must NOT match
        gldani = PropertyListing(
            id="5", source="myhome", source_id="5", url="http://x",
            title="ბინა გლდანში 1 მ/რ", price_usd=45000, area_m2=50,
            district="გლდანი", street="ხიზანიშვილის ქ."
        )
        self.assertFalse(self.engine.matches_user(self.sandro, gldani))

        # 6. Mukhiani (formerly leaked via გლდანი-ნაძალადევი) -> Must NOT match
        mukhiani = PropertyListing(
            id="6", source="myhome", source_id="6", url="http://x",
            title="ბინა მუხიანში", price_usd=42000, area_m2=50,
            district="მუხიანი"
        )
        self.assertFalse(self.engine.matches_user(self.sandro, mukhiani))

    def test_vake_and_saburtalo_strict_isolation(self):
        """
        Vake subscriber must not receive Saburtalo or Didi Dighomi, and vice versa.
        """
        vake_user = UserSubscription(
            chat_id="vake_user", deal_type="sale", price_min_usd=10000, price_max_usd=200000,
            districts=["ვაკე"], is_active=True
        )
        saburtalo_user = UserSubscription(
            chat_id="saburtalo_user", deal_type="sale", price_min_usd=10000, price_max_usd=200000,
            districts=["საბურთალო"], is_active=True
        )

        vake_listing = PropertyListing(
            id="v1", source="myhome", source_id="v1", url="http://x",
            title="ბინა ვაკეში", price_usd=100000, area_m2=60, district="ვაკე"
        )
        saburtalo_listing = PropertyListing(
            id="s1", source="myhome", source_id="s1", url="http://x",
            title="ბინა საბურთალოზე", price_usd=90000, area_m2=60, district="საბურთალო"
        )

        self.assertTrue(self.engine.matches_user(vake_user, vake_listing))
        self.assertFalse(self.engine.matches_user(vake_user, saburtalo_listing))

        self.assertTrue(self.engine.matches_user(saburtalo_user, saburtalo_listing))
        self.assertFalse(self.engine.matches_user(saburtalo_user, vake_listing))

    def test_isani_and_samgori_strict_isolation(self):
        """
        Isani subscriber must not receive Samgori/Varketili, and vice versa.
        """
        isani_user = UserSubscription(
            chat_id="isani_user", deal_type="sale", price_min_usd=10000, price_max_usd=200000,
            districts=["ისანი"], is_active=True
        )
        samgori_user = UserSubscription(
            chat_id="samgori_user", deal_type="sale", price_min_usd=10000, price_max_usd=200000,
            districts=["სამგორი"], is_active=True
        )

        isani_listing = PropertyListing(
            id="i1", source="myhome", source_id="i1", url="http://x",
            title="ბინა ისანში", price_usd=60000, area_m2=50, district="ისანი"
        )
        varketili_listing = PropertyListing(
            id="v1", source="myhome", source_id="v1", url="http://x",
            title="ბინა ვარკეთილში", price_usd=55000, area_m2=50, district="ვარკეთილი"
        )

        self.assertTrue(self.engine.matches_user(isani_user, isani_listing))
        self.assertFalse(self.engine.matches_user(isani_user, varketili_listing))

        self.assertTrue(self.engine.matches_user(samgori_user, varketili_listing))
        self.assertFalse(self.engine.matches_user(samgori_user, isani_listing))

    def test_myhome_scraper_does_not_poison_subdistrict(self):
        """
        Verifies that MyHome normalization sets district to urban_name and leaves subdistrict None
        when district_name is a combined municipal district.
        """
        scraper = MyHomeScraper()
        raw_item = {
            "id": 26020564,
            "deal_type_id": 1,
            "urban_name": "საბურთალო",
            "district_name": "ვაკე-საბურთალო",
            "city_name": "თბილისი",
            "price": {"2": {"price_total": 160000}},
            "area": 58,
            "address": "კოსტავა მ. ქ. 80",
            "dynamic_title": "იყიდება 1 ოთახიანი ბინა საბურთალოზე"
        }
        listing = scraper._normalize_item(raw_item)
        self.assertIsNotNone(listing)
        self.assertEqual(listing.district, "საბურთალო")
        self.assertIsNone(listing.subdistrict, "Parent dual district must not pollute subdistrict")

    def test_ss_ge_scraper_does_not_poison_subdistrict(self):
        """
        Verifies that SS.ge normalization sets district to subdistrictTitle and leaves subdistrict None
        when districtTitle is a combined municipal district.
        """
        scraper = SSGeScraper()
        raw_item = {
            "applicationId": 999,
            "price": {"priceUsd": 50000},
            "totalArea": 50,
            "title": "იყიდება ბინა დიდუბეში",
            "detailUrl": "bina-iyideba-didubeshi/999",
            "address": {
                "cityTitle": "თბილისი",
                "subdistrictTitle": "დიდუბე",
                "districtTitle": "დიდუბე-ჩუღურეთი",
                "streetTitle": "წერეთლის გამზ."
            }
        }
        listing = scraper._normalize_item(raw_item)
        self.assertIsNotNone(listing)
        self.assertEqual(listing.district, "დიდუბე")
        self.assertIsNone(listing.subdistrict, "Parent dual district must not pollute subdistrict")


if __name__ == "__main__":
    unittest.main()
