import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock

from core.analytics import MarketAnalytics
from core.district_prices import bedroom_type_for, lookup_price, parse_price_table
from core.models import PropertyListing
from core.xray import _expand_district_aliases

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "myhome_district_prices_bed2.json")


def _load_fixture() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


class TestBedroomTypeFor(unittest.TestCase):
    def test_known_bedrooms_win(self):
        self.assertEqual(bedroom_type_for(None, 1), 1)
        self.assertEqual(bedroom_type_for(None, 3), 3)
        # bedrooms beats rooms when both are known
        self.assertEqual(bedroom_type_for(3, 2), 2)

    def test_rooms_is_bedrooms_plus_one_when_bedrooms_unknown(self):
        self.assertEqual(bedroom_type_for(2, None), 1)
        self.assertEqual(bedroom_type_for(3, None), 2)
        self.assertEqual(bedroom_type_for(4, 0), 3)

    def test_one_room_is_a_studio(self):
        self.assertEqual(bedroom_type_for(1, None), 6)

    def test_no_table_for_four_plus_or_unknown(self):
        self.assertIsNone(bedroom_type_for(None, 4))
        self.assertIsNone(bedroom_type_for(5, None))
        self.assertIsNone(bedroom_type_for(None, None))
        self.assertIsNone(bedroom_type_for(0, 0))


class TestParsePriceTable(unittest.TestCase):
    def test_flattens_groups_and_urbans(self):
        table = parse_price_table(_load_fixture())
        self.assertEqual(table["საბურთალო"], 1577.81)
        self.assertEqual(table["ნუცუბიძის ფერდობი"], 1359.2)
        # the group (parent) figure sits next to its urbans
        self.assertEqual(table["ვაკე-საბურთალო"], 1478.23)
        self.assertEqual(table["აეროპორტის დას."], 1100.0)

    def test_unusable_payloads_give_none(self):
        self.assertIsNone(parse_price_table(None))
        self.assertIsNone(parse_price_table({"result": False, "data": [], "errors": ["x"]}))
        # 4+ bedrooms: MyHome answers 200 with no items
        self.assertIsNone(parse_price_table({"result": True, "data": {"items": [], "date": "d"}}))
        self.assertIsNone(parse_price_table({"result": True, "data": []}))


class TestLookupPrice(unittest.TestCase):
    def setUp(self):
        self.table = parse_price_table(_load_fixture())

    def test_exact_name(self):
        self.assertEqual(lookup_price(self.table, ["საბურთალო"]), 1577.81)

    def test_first_candidate_present_in_the_table_wins(self):
        # Nutsubidze is a sub-area: its own figure beats the parent Saburtalo
        names = _expand_district_aliases("ნუცუბიძე")
        self.assertEqual(lookup_price(self.table, names), 1359.2)

    def test_alias_spelling_differs_from_myhome(self):
        names = _expand_district_aliases("სანზონა")
        self.assertEqual(lookup_price(self.table, names), 1143.96)

    def test_unknown_district_has_no_figure(self):
        self.assertIsNone(lookup_price(self.table, ["არარსებული უბანი"]))
        self.assertIsNone(lookup_price(self.table, []))
        self.assertIsNone(lookup_price(None, ["საბურთალო"]))


def _listing(district="საბურთალო", price_per_m2=1000.0, rooms=None, bedrooms=None, source="ss_ge"):
    return PropertyListing(
        id="t1",
        source=source,
        source_id="1",
        title="t",
        price_usd=price_per_m2 * 50,
        area_m2=50,
        price_per_m2=price_per_m2,
        district=district,
        rooms=rooms,
        bedrooms=bedrooms,
        url="http://example.com/1",
    )


class TestAlertBenchmark(unittest.TestCase):
    """MarketAnalytics compares against MyHome's table, with nothing hardcoded behind it."""

    def setUp(self):
        self.table = parse_price_table(_load_fixture())

    def test_uses_the_table_of_the_listings_bedroom_type(self):
        studio_table = {"საბურთალო": 2000.0}
        analytics = MarketAnalytics(price_tables={2: self.table, 6: studio_table, None: {}})
        two_bed = analytics.evaluate_listing(_listing(rooms=3))
        self.assertEqual(two_bed.market_median_price_m2, 1577.81)
        studio = analytics.evaluate_listing(_listing(rooms=1))
        self.assertEqual(studio.market_median_price_m2, 2000.0)

    def test_parent_substring_cannot_hijack_the_district(self):
        # The old hardcoded lookup matched "ვაკე" inside "ვაკე-საბურთალო" before anything else
        analytics = MarketAnalytics(price_tables={None: self.table})
        listing = analytics.evaluate_listing(_listing(district="ვაკე-საბურთალო"))
        self.assertEqual(listing.market_median_price_m2, 1478.23)

    def test_sub_area_uses_its_own_figure(self):
        analytics = MarketAnalytics(price_tables={None: self.table})
        listing = analytics.evaluate_listing(_listing(district="ნუცუბიძე"))
        self.assertEqual(listing.market_median_price_m2, 1359.2)

    def test_discount_and_scale_come_from_that_figure(self):
        analytics = MarketAnalytics(price_tables={None: self.table})
        listing = analytics.evaluate_listing(
            _listing(price_per_m2=1200.0), discount_threshold_pct=15.0
        )
        self.assertEqual(listing.discount_pct, round((1577.81 - 1200.0) / 1577.81 * 100, 1))
        self.assertTrue(listing.is_bargain)
        self.assertEqual(listing.valuation_scale_source, "market")

    def test_no_figure_means_no_market_verdict(self):
        for analytics in (
            MarketAnalytics(),  # nothing fetched: API down
            MarketAnalytics(price_tables={None: None}),  # asked, MyHome had no answer
            MarketAnalytics(price_tables={None: self.table}),  # table has no such district
        ):
            district = "არარსებული უბანი" if analytics._price_tables else "საბურთალო"
            listing = analytics.evaluate_listing(_listing(district=district))
            self.assertIsNone(listing.market_avg_price_m2)
            self.assertIsNone(listing.market_median_price_m2)
            self.assertIsNone(listing.discount_pct)
            self.assertIsNone(listing.valuation_scale_label)
            self.assertIsNone(listing.price_status_label)
            self.assertFalse(listing.is_bargain)

    def test_official_myhome_label_still_applies_without_a_figure(self):
        listing = _listing(source="myhome")
        MarketAnalytics().evaluate_listing(
            listing, myhome_price_label={"display_label": "top_price", "range_label": "top_price_1"}
        )
        self.assertEqual(listing.valuation_scale_source, "myhome")
        self.assertEqual(listing.valuation_scale_tier, 1)
        self.assertTrue(listing.is_bargain)
        self.assertIsNone(listing.discount_pct)

    def test_implausibly_cheap_is_still_flagged_without_a_figure(self):
        listing = MarketAnalytics().evaluate_listing(_listing(price_per_m2=200.0))
        self.assertIn("საეჭვოდ დაბალი ფასი", listing.price_status_label)
        self.assertFalse(listing.is_bargain)


class TestLoadPriceTable(unittest.TestCase):
    def test_fetches_each_bedroom_type_once_per_run_failures_included(self):
        fetch = AsyncMock(
            side_effect=lambda bedroom_type_id: None if bedroom_type_id == 6 else {"x": 1.0}
        )
        analytics = MarketAnalytics(fetch_price_table=fetch)

        async def run():
            for rooms in (3, 3, 3, 1, 1, None):  # 2 bedrooms, studio (API down), unknown
                await analytics.load_price_table(_listing(rooms=rooms))

        asyncio.run(run())
        self.assertEqual(sorted(c.args[0] or 0 for c in fetch.await_args_list), [0, 2, 6])

    def test_without_a_fetcher_nothing_is_requested(self):
        analytics = MarketAnalytics()
        asyncio.run(analytics.load_price_table(_listing(rooms=3)))
        self.assertEqual(analytics._price_tables, {})


if __name__ == "__main__":
    unittest.main()
