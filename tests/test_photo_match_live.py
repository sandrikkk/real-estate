"""The photo search also asks MyHome and SS.ge (core/photo_match.py, live part).

When the caption names a district, the job reads the portals' listings in that district and
area window from every seller, downloads up to LIVE_MAX_HASHED candidates' first photos, and
shows the ones the duplicate rule accepts, next to the database's. Network access is faked
with the owner search's FakeWeb.
"""

import dataclasses
import unittest

from core.photo_match import LIVE_MAX_HASHED, live_matches
from core.xray import myhome_search_url
from tests.test_owner_search import FakeWeb, item, search_page, ssge_row

A, B = "0000000000000000", "00000000000000ff"  # one photo, recompressed (8 bits apart)
C = "ffffffffffffff00"
X = "0f0f0f0f0f0f0f0f"  # another photo

FLAT = {"district": "გლდანი", "area_m2": 72.0, "rooms": 3, "floor": "1", "price_usd": 60000.0}
# The duplicate finder's window around 72 m², as the owner search asks for it.
PARAMS = {
    "deal_types": "1",
    "real_estate_types": "1",
    "cities": "1",
    "urbans": "2",
    "room_types": "3",
    "floor_from": "1",
    "floor_to": "1",
    "area_from": "61",
    "area_to": "83",
}


def web_with(myhome_items, ssge_items=(), images=None, params=PARAMS):
    web = FakeWeb([], {}, ssge_items=ssge_items)
    for p in ("1", "2"):
        q = {**params, "page": p}
        web.pages[myhome_search_url(q)] = search_page(q, myhome_items if p == "1" else [])
    images = images or {}
    calls = []

    async def image_hashes(urls):
        calls.append(list(urls))
        return [images[u] for u in urls if u in images]

    return web, dataclasses.replace(web.sources(), image_hashes=image_hashes), calls


def mh(item_id, photos, **extra):
    return item(item_id, 700 + item_id, images=[{"large": u} for u in photos], **extra)


def run(flat, sources, kept=(A, C), shown_ids=()):
    import asyncio

    return asyncio.run(live_matches(flat, list(kept), set(shown_ids), sources))


class TestLiveMatches(unittest.TestCase):
    def test_finds_a_myhome_listing_with_two_equal_photos_from_any_seller(self):
        agent_item = mh(5, ["u1", "u2"], user_type={"type": "agent"})
        other = mh(6, ["u3"])
        web, sources, _ = web_with([agent_item, other], images={"u1": B, "u2": C, "u3": X})
        found = run(FLAT, sources)
        self.assertEqual([r["id"] for r in found["rows"]], ["myhome_5"])
        self.assertEqual(found["counts"]["myhome_5"], 2)
        self.assertEqual(found["checked"], 2)
        self.assertEqual(found["failed"], [])
        # Every seller, not only owners: no owner filter in the request.
        self.assertTrue(all("owner_type" not in u for u in web.requested))
        self.assertTrue(all("advancedSearch" not in p for p in web.payloads))

    def test_finds_an_ss_ge_listing(self):
        row = ssge_row(10, images=["s1", "s2"], area_m2=72, price_usd=59000)
        web, sources, _ = web_with([], ssge_items=[row], images={"s1": A, "s2": C})
        self.assertEqual([r["id"] for r in run(FLAT, sources)["rows"]], ["ss_ge_10"])

    def test_one_equal_photo_needs_the_caption_price_and_area(self):
        near = mh(5, ["u1"], area=72, price={"2": {"price_total": 61000}})
        _, sources, _ = web_with([near], images={"u1": A})
        self.assertEqual([r["id"] for r in run(FLAT, sources)["rows"]], ["myhome_5"])
        no_price = {k: v for k, v in FLAT.items() if k != "price_usd"}
        self.assertEqual(run(no_price, sources)["rows"], [])

    def test_a_listing_already_shown_from_the_database_is_not_downloaded(self):
        _, sources, calls = web_with([mh(5, ["u1", "u2"])], images={"u1": A, "u2": C})
        found = run(FLAT, sources, shown_ids={"myhome_5"})
        self.assertEqual(found["rows"], [])
        self.assertEqual(calls, [])

    def test_downloads_at_most_the_budget_and_closest_area_first(self):
        far = [mh(100 + n, [f"f{n}"], area=82) for n in range(LIVE_MAX_HASHED)]
        close = mh(5, ["u1", "u2"], area=72)
        _, sources, calls = web_with([*far, close], images={"u1": A, "u2": C})
        found = run(FLAT, sources)
        self.assertEqual(len(calls), LIVE_MAX_HASHED)
        self.assertEqual(calls[0], ["u1", "u2"])
        self.assertEqual([r["id"] for r in found["rows"]], ["myhome_5"])
        self.assertEqual(found["checked"], LIVE_MAX_HASHED)

    def test_no_district_in_the_caption_asks_nothing(self):
        web, sources, calls = web_with([mh(5, ["u1", "u2"])], images={"u1": A, "u2": C})
        found = run({"area_m2": 72.0}, sources)
        self.assertEqual(
            found, {"rows": [], "counts": {}, "checked": 0, "failed": [], "asked": False}
        )
        self.assertEqual(web.requested, [])
        self.assertEqual(calls, [])

    def test_a_portal_that_is_down_is_named(self):
        web = FakeWeb([], {})
        sources = web.sources(myhome_down=True)

        async def image_hashes(urls):
            return []

        sources = dataclasses.replace(sources, image_hashes=image_hashes)
        self.assertEqual(run(FLAT, sources)["failed"], ["myhome"])

    def test_rent_caption_asks_for_rentals(self):
        rent = {**FLAT, "deal_type": "rent"}
        web, sources, _ = web_with([], params={**PARAMS, "deal_types": "2"})
        run(rent, sources)
        self.assertTrue(any("deal_types=2" in u for u in web.requested))
        self.assertEqual(web.payloads[0]["realEstateDealType"], 1)


if __name__ == "__main__":
    unittest.main()


# The whole photo job with the live part: the database's listings and the portals' together.
import asyncio  # noqa: E402

from core.photo_match import lookup_photos  # noqa: E402
from tests.test_photo_match import BATCH, PhotoMatchCase, hash_of  # noqa: E402


class TestLookupWithPortals(PhotoMatchCase):
    def lookup(self, sources):
        return asyncio.run(lookup_photos(self.conn, BATCH, self.download, sources))

    def portal(self, items, images):
        # The caption reads as district დიღომი, floor 3, 58 m² (window 49..67), no rooms.
        params = {k: v for k, v in PARAMS.items() if k != "room_types"}
        params.update({"floor_from": "3", "floor_to": "3", "area_from": "49", "area_to": "67"})
        _, sources, calls = web_with(items, images=images, params=params)
        return dataclasses.replace(sources, myhome_urbans={"დიღომი": [2]}), calls

    def test_portal_listing_is_shown_next_to_the_database_one(self):
        self.listing("myhome_1", [1, 2, 3])
        live = mh(77, ["p1", "p2"], area=58, floor=3, price={"2": {"price_total": 94000}})
        sources, _ = self.portal([live], {"p1": hash_of(1), "p2": hash_of(2)})
        self.post([1, 2, 3], caption="იყიდება ბინა დიდ დიღომში, 58 მ², 3 სართული, $95,000")
        result = self.lookup(sources)
        ids = [c["id"] for c in result["result"]["candidates"]]
        self.assertEqual(ids, ["myhome_1", "myhome_77"])
        text = result["result"]["text"]
        self.assertIn("ემთხვევა 3 ფოტო", text)
        self.assertIn("ემთხვევა 2 ფოტო", text)
        self.assertIn("ℹ️ myhome-სა და ss.ge-ზე ახლა შევამოწმე 1 განცხადების ფოტოები.", text)

    def test_without_a_district_the_reply_says_how_to_search_the_portals(self):
        self.listing("myhome_1", [1, 2, 3])
        sources, calls = self.portal([], {})
        self.post([1, 2, 3])
        text = self.lookup(sources)["result"]["text"]
        self.assertIn("წარწერად დაურთე უბანი და ფართი", text)
        self.assertEqual(calls, [])

    def test_nothing_anywhere_says_so_without_naming_only_the_database(self):
        sources, _ = self.portal([], {})
        self.post([8, 9], caption="იყიდება ბინა დიდ დიღომში, 58 მ², 3 სართული, $95,000")
        text = self.lookup(sources)["result"]["text"]
        self.assertIn("ℹ️ ამ ფოტოებით განცხადება ვერ მოიძებნა.", text)
        self.assertNotIn("ჩვენს ბაზაში ამ ფოტოებით", text)
