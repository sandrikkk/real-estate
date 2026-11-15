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


# --- The caption's phone number: every photo of that number's MyHome listings ---
from core.photo_match import PHONE_MAX_LISTINGS, PHONE_MAX_PHOTOS, phone_matches  # noqa: E402
from tests.test_owner_search import page  # noqa: E402

PHONE = "501100253"


def phone_web(listings, images, masked=f"{PHONE[:6]}***"):
    """MyHome's search for the number, and its first hit's page carrying `masked`."""
    web = FakeWeb([], {})
    web.pages[myhome_search_url({"q": PHONE})] = search_page({"q": PHONE}, listings)
    if listings:
        statement = {**listings[0], "user_phone_number": masked}
        web.pages[f"https://www.myhome.ge/ka/pr/{listings[0]['id']}/"] = page(
            [{"state": {"data": {"data": {"statement": statement}}}}]
        )
    calls = []

    async def image_hashes(urls):
        calls.append(list(urls))
        return [images[u] for u in urls if u in images]

    return dataclasses.replace(web.sources(), image_hashes=image_hashes), calls


def by_phone(flat, sources, kept=(A, X), shown_ids=()):
    import asyncio

    return asyncio.run(phone_matches(flat, list(kept), set(shown_ids), sources))


class TestPhoneMatches(unittest.TestCase):
    FLAT = {"phone_9": PHONE, "area_m2": 56.1}

    def test_one_equal_photo_deep_in_the_listing_is_enough_on_the_same_number(self):
        # The Nutsubidze post: no price, one photo, and it is the listing's 5th.
        photos = [f"p{k}" for k in range(1, 17)]
        images = {u: C for u in photos}
        images["p5"] = B  # A, recompressed
        listing = mh(26209700, photos, area=56.1)
        sources, calls = phone_web([listing], images)
        found = by_phone(self.FLAT, sources)
        self.assertEqual([r["id"] for r in found["rows"]], ["myhome_26209700"])
        self.assertEqual(found["counts"]["myhome_26209700"], 1)
        self.assertEqual(calls, [photos])  # every photo, not the first three
        self.assertEqual(found["checked"], 1)

    def test_no_equal_photo_shows_nothing(self):
        listing = mh(7, ["p1", "p2"])
        sources, _ = phone_web([listing], {"p1": C, "p2": C})
        self.assertEqual(by_phone(self.FLAT, sources)["rows"], [])

    def test_another_numbers_listings_are_not_read(self):
        # MyHome ignored the filter: its first hit carries another number.
        listing = mh(7, ["p1"])
        sources, calls = phone_web([listing], {"p1": A}, masked="599999***")
        found = by_phone(self.FLAT, sources)
        self.assertEqual(found["rows"], [])
        self.assertEqual(calls, [])

    def test_no_number_in_the_caption_asks_nothing(self):
        sources, calls = phone_web([mh(7, ["p1"])], {"p1": A})
        found = by_phone({"area_m2": 56.1}, sources)
        self.assertFalse(found["asked"])
        self.assertEqual(calls, [])

    def test_a_listing_already_shown_is_not_read_again(self):
        sources, calls = phone_web([mh(7, ["p1"])], {"p1": A})
        self.assertEqual(by_phone(self.FLAT, sources, shown_ids={"myhome_7"})["rows"], [])
        self.assertEqual(calls, [])

    def test_reads_at_most_the_caps_closest_area_first(self):
        far = [mh(100 + n, [f"f{n}"], area=90) for n in range(PHONE_MAX_LISTINGS)]
        close = mh(7, [f"c{k}" for k in range(PHONE_MAX_PHOTOS + 5)], area=56)
        sources, calls = phone_web([*far, close], {"c0": A})
        found = by_phone(self.FLAT, sources)
        self.assertEqual(len(calls), PHONE_MAX_LISTINGS)
        self.assertEqual(len(calls[0]), PHONE_MAX_PHOTOS)
        self.assertEqual([r["id"] for r in found["rows"]], ["myhome_7"])


class TestLookupByPhone(PhotoMatchCase):
    def test_the_numbers_listing_is_shown_with_its_mark(self):
        photos = [f"p{k}" for k in range(1, 9)]
        images = {u: hash_of(50 + k) for k, u in enumerate(photos)}
        images["p6"] = hash_of(1)
        listing = mh(26209700, photos, area=56.1, price={"2": {"price_total": 123000}})
        sources, _ = phone_web([listing], images)
        self.post([1, 2], caption="ფართობი: 56.1 კვ.მ ☎️501 100 253")
        result = asyncio.run(lookup_photos(self.conn, BATCH, self.download, sources))
        text = result["result"]["text"]
        self.assertEqual([c["id"] for c in result["result"]["candidates"]], ["myhome_26209700"])
        self.assertIn("ემთხვევა 1 ფოტო | 📞 იგივე ნომერი", text)
        self.assertIn("ℹ️ myhome-ზე ამ ნომრის 1 განცხადების ყველა ფოტო შევამოწმე.", text)
