import unittest

from scripts.portal_size import myhome_all_url, numbers, ssge_all_payload


class TestPortalSize(unittest.TestCase):
    def test_finds_totals_and_skips_listings(self):
        answer = {
            "result": True,
            "data": {
                "data": [{"id": 1, "user_statements_count": 7, "price": 5}],
                "meta": {"total": 51234, "last_page": 2135, "per_page": 24, "current_page": 1},
            },
            "totalCount": 9,
        }
        self.assertEqual(
            numbers(answer),
            [
                ("data.meta.total", 51234),
                ("data.meta.last_page", 2135),
                ("data.meta.per_page", 24),
                ("totalCount", 9),
            ],
        )

    def test_listing_counts_inside_a_listing_dict_are_not_printed(self):
        self.assertEqual(numbers({"user": {"statements_count": 3}, "items": {"count": 2}}), [])

    def test_all_searches_have_no_price_or_area(self):
        url = myhome_all_url("1")
        self.assertIn("cities=1", url)
        self.assertNotIn("price_from", url)
        self.assertNotIn("area_from", url)
        payload = ssge_all_payload(4)
        self.assertEqual(payload["cityIdList"], [95])
        self.assertFalse({"priceFrom", "areaFrom", "advancedSearch"} & set(payload))


if __name__ == "__main__":
    unittest.main()
