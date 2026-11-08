"""ADR 0003 stage 2: the owner search behind the "მეპატრონე" button (core/owner_search.py).

The user's example post (Gldani 6th micro-district, 3 rooms, floor 1, an agent's number) runs
against fake MyHome pages, fake SS.ge answers and a local SQLite copy of D1.
"""

import asyncio
import dataclasses
import json
import os
import sqlite3
import tempfile
import unittest

from core.database import DatabaseEngine
from core.owner_search import (
    MAX_PHOTO_CANDIDATES,
    SSGE_PAGE_SIZE,
    Sources,
    owner_report,
    search_owner,
)
from core.xray import MYHOME_SEARCH_PAGE_SIZE, myhome_search_url

AGENT = "555000111"
GLDANI = {"district": "გლდანი", "micro": 6, "rooms": 3, "floor": "1", "phone9": AGENT}


def item(item_id, user_id, **extra):
    base = {
        "id": item_id,
        "user_id": user_id,
        "user_type": {"type": "physical"},
        "urban_name": "გლდანი",
        "address": "გლდანი - VI მ/რ",
        "area": 72,
        "floor": 1,
        "total_floors": 5,
        "room": "3",
        "deal_type_id": 1,
        "price": {"2": {"price_total": 60000}},
    }
    base.update(extra)
    return base


def page(queries):
    data = {"props": {"pageProps": {"dehydratedState": {"queries": queries}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def search_page(params, items):
    q = {"queryKey": ["statements", "list", {"query": dict(params)}]}
    q["state"] = {"data": {"data": {"data": items}}}
    return page([q])


OWNER_PARAMS = {
    "deal_types": "1",
    "real_estate_types": "1",
    "cities": "1",
    "owner_type": "physical",
    "urbans": "2",
    "room_types": "3",
    "floor_from": "1",
    "floor_to": "1",
}
# The agent's own listing gives the target 72 m²; the search then asks for the duplicate
# finder's window around it (72 m² ± 15% = 61.2..82.8, rounded outwards).
WINDOW = {"area_from": "61", "area_to": "83"}


class FakeWeb:
    """MyHome pages by URL and SS.ge answers; anything else is unreachable (None)."""

    def __init__(self, myhome_owner_items, accounts, ssge_items=(), ssge_details=None):
        self.pages = {}
        agent_listing = item(
            9000, 4242, user_type={"type": "agent"}, price={"2": {"price_total": 75000}}
        )
        self.pages[myhome_search_url({"q": AGENT})] = search_page({"q": AGENT}, [agent_listing])
        statement = {**agent_listing, "user_phone_number": f"{AGENT[:6]}***"}
        # A real listing page also carries queries whose data is a plain value, such as
        # ["cardView"]; the live owner jobs of 2026-11-08 crashed on those.
        self.pages["https://www.myhome.ge/ka/pr/9000/"] = page(
            [
                {"queryKey": ["cardView"], "state": {"data": "grid"}},
                {"queryKey": ["flags"], "state": {"data": {"data": "on"}}},
                {"state": {"data": {"data": {"statement": statement}}}},
            ]
        )
        # The same answers with and without the area window, so a test can tell which was asked.
        for narrowing in ({}, WINDOW):
            for p in ("1", "2"):
                params = {**OWNER_PARAMS, **narrowing, "page": p}
                self.pages[myhome_search_url(params)] = search_page(
                    params, myhome_owner_items if p == "1" else []
                )
        for user_id, rows in accounts.items():
            params = {"users": str(user_id)}
            self.pages[myhome_search_url(params)] = search_page(params, rows)
        self.ssge_items = list(ssge_items)
        self.ssge_every_page = False
        self.ssge_details_by_id = ssge_details or {}
        self.payloads = []
        self.requested = []

    def sources(self, myhome_down=False):
        async def myhome_html(url):
            self.requested.append(url)
            return None if myhome_down else self.pages.get(url)

        async def ssge_search(payload):
            self.payloads.append(payload)
            return self.ssge_items if payload["page"] == 1 or self.ssge_every_page else []

        async def ssge_details(source_id):
            return self.ssge_details_by_id.get(source_id)

        return Sources(
            myhome_html=myhome_html,
            ssge_search=ssge_search,
            ssge_details=ssge_details,
            ssge_normalize=lambda row: dict(row),
            myhome_urbans={"გლდანი": [2]},
            ssge_subdistricts={"გლდანი": [32, 33]},
        )


def ssge_row(sid, **extra):
    row = {
        "id": f"ss_ge_{sid}",
        "source": "ss_ge",
        "source_id": str(sid),
        "url": f"https://home.ss.ge/ka/udzravi-qoneba/{sid}",
        "district": "გლდანი",
        "street": "გლდანი - VI მ/რ",
        "area_m2": 72,
        "floor": "1",
        "total_floors": 5,
        "rooms": 3,
        "price_usd": 64000,
        "author_id": f"u{sid}",
    }
    row.update(extra)
    return row


class TestOwnerSearch(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.temp_dir.name, "d1.db")
        DatabaseEngine(db_path=path, use_d1=False).init_db()
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def run_search(self, web, target=GLDANI, **kw):
        return asyncio.run(search_owner(dict(target), self.conn, web.sources(**kw)))

    def gldani_web(self):
        owner = item(1, 111, price={"2": {"price_total": 68000}})
        hidden = item(2, 222)  # private seller on paper, but the account sells two flats
        other_micro = item(3, 333, address="გლდანი - III მ/რ")
        agent_repost = item(4, 4242)  # the agent's own account, posted as physical
        accounts = {
            111: [owner],
            222: [hidden, item(20, 222, area=110, floor=7, address="ვაკე")],
            333: [other_micro],
            4242: [agent_repost],
        }
        ssge = [ssge_row(10), ssge_row(11)]
        details = {
            "10": {"is_owner": True, "author_listings_count": 1},
            "11": {"is_owner": False, "agency_id": 77, "author_listings_count": 40},
        }
        return FakeWeb([owner, hidden, other_micro, agent_repost], accounts, ssge, details)

    def test_the_users_example_finds_the_owners_and_drops_brokers(self):
        found = self.run_search(self.gldani_web())
        # Equal on every field, so the cheaper one comes first (price never rejects).
        self.assertEqual([c["id"] for c in found["owners"]], ["ss_ge_10", "myhome_1"])
        self.assertTrue(all(c["is_owner"] == 1 for c in found["owners"]))
        self.assertFalse(found["weak"], "the agent's listing should give the area")
        self.assertEqual(found["target"]["area_m2"], 72.0, "area from the agent's listing")
        self.assertEqual(found["target"]["price_usd"], 75000.0)
        self.assertEqual(found["failed"], [])

    def test_the_report_numbers_owners_and_shows_the_price_against_the_agent(self):
        text = owner_report(self.run_search(self.gldani_web()))
        self.assertIn(
            "🏠 <b>მესაკუთრის ძებნა:</b> გლდანი, მე-6 მიკრო, 3 ოთახი, 1 სართ., 72 მ²", text
        )
        self.assertIn('1. <a href="https://home.ss.ge/ka/udzravi-qoneba/10">SS.ge</a>', text)
        self.assertIn("-$11,000 აგენტზე იაფად", text)
        self.assertIn('2. <a href="https://www.myhome.ge/ka/pr/1/">MyHome</a>', text)
        self.assertIn("-$7,000 აგენტზე იაფად", text)
        self.assertIn("✅ იგივე ბინაა, ❌ სხვაა", text)
        # A portal's "private" label is the seller's own claim, so the line does not call it proof.
        self.assertIn("| სავარაუდო კერძო გამყიდველი", text)
        self.assertNotIn("| მესაკუთრე", text)

    def test_ssge_is_asked_for_private_sellers_with_the_rooms(self):
        web = self.gldani_web()
        self.run_search(web)
        first = web.payloads[0]
        self.assertEqual(first["advancedSearch"], {"individualEntityOnly": True})
        self.assertEqual((first["rooms"], first["subdistrictIds"]), ([3], [32, 33]))

    # Production, 2026-11-08: a private seller on the same street, 58 m², floor 3 was never
    # read. The search took the first 48 MyHome listings of twelve urbans and filtered by area
    # afterwards. The portals now get the window in the request, so the pages cover it.
    def test_the_portals_are_asked_only_for_the_duplicate_finders_area_window(self):
        web = self.gldani_web()
        self.run_search(web)
        searched = [u for u in web.requested if "owner_type=physical" in u]
        self.assertTrue(searched)
        self.assertTrue(all("area_from=61&area_to=83" in u for u in searched), searched)
        self.assertTrue(web.payloads)
        self.assertTrue(all((p["areaFrom"], p["areaTo"]) == (61, 83) for p in web.payloads))

    def test_without_an_area_the_portals_are_not_narrowed(self):
        web = self.gldani_web()
        self.run_search(web, target={"district": "გლდანი", "micro": 6, "rooms": 3, "floor": "1"})
        self.assertFalse(any("area_from" in u for u in web.requested))
        self.assertFalse(any("areaFrom" in p for p in web.payloads))

    # A "nothing found" that stopped at the page cap must not read like a complete search.
    def test_a_full_last_myhome_page_is_reported_as_not_read_to_the_end(self):
        web = self.gldani_web()
        for p in ("1", "2"):
            params = {**OWNER_PARAMS, **WINDOW, "page": p}
            rows = [item(100 * int(p) + n, 900 + n) for n in range(MYHOME_SEARCH_PAGE_SIZE)]
            web.pages[myhome_search_url(params)] = search_page(params, rows)
        found = self.run_search(web)
        self.assertEqual(found["truncated"], {"myhome": 2 * MYHOME_SEARCH_PAGE_SIZE})
        self.assertIn(
            f"myhome: მხოლოდ პირველი {2 * MYHOME_SEARCH_PAGE_SIZE} განცხადება", owner_report(found)
        )

    def test_a_full_last_ssge_page_is_reported_too(self):
        web = self.gldani_web()
        web.ssge_every_page = True
        web.ssge_items = [ssge_row(300 + n) for n in range(SSGE_PAGE_SIZE)]
        found = self.run_search(web)
        self.assertEqual(found["truncated"], {"ss.ge": 2 * SSGE_PAGE_SIZE})
        self.assertIn(f"ss.ge: მხოლოდ პირველი {2 * SSGE_PAGE_SIZE} განცხადება", owner_report(found))

    def test_a_short_last_page_is_not_reported(self):
        found = self.run_search(self.gldani_web())
        self.assertEqual(found["truncated"], {})
        self.assertNotIn("მხოლოდ პირველი", owner_report(found))

    def test_a_photo_match_in_d1_comes_first(self):
        self.conn.execute(
            "INSERT INTO properties (id, source, source_id, title, price_usd, area_m2, price_per_m2,"
            " district, street, floor, rooms, url, scraped_at, group_id, author_id)"
            " VALUES ('ss_ge_50', 'ss_ge', '50', 'ბინა', 66000, 72, 916, 'გლდანი', 'გლდანი - VI მ/რ',"
            " '1', 3, 'https://home.ss.ge/ka/udzravi-qoneba/50', '2026-11-08T09:00:00', 'g1', 'u50')"
        )
        self.conn.commit()
        web = self.gldani_web()
        web.ssge_details_by_id["50"] = {"is_owner": True, "author_listings_count": 1}
        found = self.run_search(web, target={**GLDANI, "group_id": "g1"})
        self.assertEqual(found["owners"][0]["id"], "ss_ge_50")
        self.assertIn("✅ <b>ფოტოები ემთხვევა:</b>", owner_report(found))

    def test_a_target_without_area_or_agent_listing_is_reported_as_weak(self):
        target = {"district": "გლდანი", "micro": 6, "rooms": 3, "floor": "1"}
        found = self.run_search(self.gldani_web(), target=target)
        self.assertTrue(found["weak"])
        self.assertIn("მხოლოდ კანდიდატებია", owner_report(found))

    def test_a_down_portal_is_named_and_the_others_still_answer(self):
        found = self.run_search(self.gldani_web(), myhome_down=True)
        self.assertEqual(found["failed"], ["myhome"])
        self.assertEqual([c["id"] for c in found["owners"]], ["ss_ge_10"])
        self.assertIn("⚠️ myhome ახლა არ პასუხობს", owner_report(found))

    # Production, 2026-11-08: a 70 m² flat was shown as the owner of a 51 m² one, because the
    # shared rule never compares area. The window is the duplicate finder's own (±15%).
    def test_an_area_outside_the_duplicate_finders_window_is_another_flat(self):
        web = self.gldani_web()
        near = item(5, 555, area=80)  # 72 m² target: 11% off
        far = item(6, 666, area=90)  # 25% off: the portal would not send it, the rule drops it
        web.pages[myhome_search_url({**OWNER_PARAMS, **WINDOW, "page": "1"})] = search_page(
            {**OWNER_PARAMS, **WINDOW, "page": "1"}, [near, far]
        )
        for user_id, row in ((555, near), (666, far)):
            web.pages[myhome_search_url({"users": str(user_id)})] = search_page(
                {"users": str(user_id)}, [row]
            )
        found = self.run_search(web)
        ids = [c["id"] for c in found["owners"]]
        self.assertIn("myhome_5", ids)
        self.assertNotIn("myhome_6", ids)

    # Production, 2026-11-08: with the area window in the request, a 51 m² Saburtalo flat reached
    # the rules for a 58 m² Didi Dighomi one. The query includes the parent district's urbans, and
    # the shared location rule only says "unknown" for a row filed under another district.
    def test_a_flat_filed_under_another_named_district_is_another_flat(self):
        web = self.gldani_web()
        owner = item(1, 111, price={"2": {"price_total": 68000}})
        elsewhere = item(8, 888, urban_name="საბურთალო", address="გოთუას ქ. 26")
        params = {**OWNER_PARAMS, **WINDOW, "page": "1"}
        web.pages[myhome_search_url(params)] = search_page(params, [owner, elsewhere])
        web.pages[myhome_search_url({"users": "888"})] = search_page({"users": "888"}, [elsewhere])
        ids = [c["id"] for c in self.run_search(web)["owners"]]
        self.assertIn("myhome_1", ids)
        self.assertNotIn("myhome_8", ids)

    # Production, 2026-11-08: one seller's flat, posted three times, was listed as three owners.
    def test_one_sellers_flat_posted_twice_is_one_line(self):
        web = self.gldani_web()
        owner = item(1, 111, price={"2": {"price_total": 68000}})
        repost = item(7, 111, price={"2": {"price_total": 68000}})
        web.pages[myhome_search_url({**OWNER_PARAMS, **WINDOW, "page": "1"})] = search_page(
            {**OWNER_PARAMS, **WINDOW, "page": "1"}, [owner, repost]
        )
        web.pages[myhome_search_url({"users": "111"})] = search_page(
            {"users": "111"}, [owner, repost]
        )
        web.ssge_items = [ssge_row(10, phone_number="+995 599 11 22 33")]
        web.ssge_items.append(ssge_row(12, phone_number="599112233", price_usd=64500))
        web.ssge_details_by_id["12"] = {"is_owner": True, "author_listings_count": 1}
        found = self.run_search(web)
        self.assertEqual([c["id"] for c in found["owners"]], ["ss_ge_10", "myhome_1"])
        self.assertEqual(found["owners"][0]["also"], ["https://home.ss.ge/ka/udzravi-qoneba/12"])
        self.assertEqual(found["owners"][1]["also"], ["https://www.myhome.ge/ka/pr/7/"])
        text = owner_report(found)
        self.assertIn(
            'კიდევ 1 განცხადება: <a href="https://www.myhome.ge/ka/pr/7/">MyHome</a>', text
        )
        self.assertNotIn("3.", text)

    def test_nothing_found_says_so(self):
        web = FakeWeb([], {})
        found = self.run_search(web)
        self.assertEqual(found["owners"], [])
        self.assertIn("მესაკუთრის განცხადება ვერ მოიძებნა", owner_report(found))


# ADR 0005 (owner search): the post's photos against the candidates' photos. Hashes are hex
# dHashes: A and B are one photo recompressed (8 bits apart, within the limit of 10); X and Y
# are other photos (64 and 32 bits from A).
A, B = "0000000000000000", "00000000000000ff"
X, Y = "ffffffffffffffff", "0f0f0f0f0f0f0f0f"


def photo_sources(web, post=(), images=None, calls=None):
    """The FakeWeb sources plus the post's hashes and the candidates' photo hashes."""
    images = images or {}

    async def post_hashes(batch_id):
        return list(post)

    async def image_hashes(urls):
        if calls is not None:
            calls.append(list(urls))
        return [images[u] for u in urls if u in images]

    return dataclasses.replace(web.sources(), post_hashes=post_hashes, image_hashes=image_hashes)


class TestOwnerSearchPhotos(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.temp_dir.name, "d1.db")
        DatabaseEngine(db_path=path, use_d1=False).init_db()
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def search(self, sources, target=GLDANI):
        return asyncio.run(search_owner(dict(target), self.conn, sources))

    def test_two_matching_photos_put_a_candidate_first_under_the_photo_heading(self):
        owner = item(
            1,
            111,
            price={"2": {"price_total": 68000}},
            images=[{"large": "https://img/1a"}, {"large": "https://img/1b"}],
        )
        web = FakeWeb(
            [owner],
            {111: [owner]},
            ssge_items=[ssge_row(10)],
            ssge_details={"10": {"is_owner": True, "author_listings_count": 1}},
        )
        sources = photo_sources(web, post=[A, B], images={"https://img/1a": A, "https://img/1b": B})
        found = self.search(sources, target={**GLDANI, "photo_batch": "B1"})
        self.assertEqual([c["id"] for c in found["owners"]], ["myhome_1", "ss_ge_10"])
        text = owner_report(found)
        self.assertIn("✅ <b>ფოტოები ემთხვევა:</b>", text)
        self.assertIn("| ემთხვევა 2 ფოტო", text)

    def test_two_matching_photos_keep_a_flat_whose_text_says_another_floor_count(self):
        cand = item(
            7,
            777,
            total_floors=18,
            images=[{"large": "https://img/7a"}, {"large": "https://img/7b"}],
        )
        web = FakeWeb([cand], {777: [cand]})
        sources = photo_sources(web, post=[A, B], images={"https://img/7a": A, "https://img/7b": B})
        target = {**GLDANI, "total_floors": 15, "area_m2": 72, "photo_batch": "B1"}
        found = asyncio.run(search_owner(dict(target), self.conn, sources))
        self.assertEqual([c["id"] for c in found["owners"]], ["myhome_7"])
        self.assertIn("⚠️ ტექსტში განსხვავდება: სართულიანობა", owner_report(found))

    def test_one_matching_photo_does_not_keep_a_flat_whose_text_disagrees(self):
        cand = item(
            7,
            777,
            total_floors=18,
            images=[{"large": "https://img/7a"}, {"large": "https://img/7b"}],
        )
        web = FakeWeb([cand], {777: [cand]})
        sources = photo_sources(web, post=[A, X], images={"https://img/7a": A, "https://img/7b": Y})
        target = {**GLDANI, "total_floors": 15, "area_m2": 72, "photo_batch": "B1"}
        found = asyncio.run(search_owner(dict(target), self.conn, sources))
        self.assertEqual(found["owners"], [])

    def test_a_photo_matched_flat_whose_account_sells_two_flats_is_still_not_an_owner(self):
        cand = item(
            7,
            777,
            total_floors=18,
            images=[{"large": "https://img/7a"}, {"large": "https://img/7b"}],
        )
        second = item(20, 777, area=110, floor=7, address="ვაკე")
        web = FakeWeb([cand], {777: [cand, second]})
        sources = photo_sources(web, post=[A, B], images={"https://img/7a": A, "https://img/7b": B})
        target = {**GLDANI, "total_floors": 15, "area_m2": 72, "photo_batch": "B1"}
        found = asyncio.run(search_owner(dict(target), self.conn, sources))
        self.assertEqual(found["owners"], [])

    def test_without_a_photo_batch_no_photo_is_downloaded_or_compared(self):
        owner = item(
            1, 111, price={"2": {"price_total": 68000}}, images=[{"large": "https://img/1a"}]
        )
        web = FakeWeb([owner], {111: [owner]})
        calls = []
        sources = photo_sources(web, post=[A], images={"https://img/1a": A}, calls=calls)
        found = self.search(sources)
        self.assertEqual(calls, [])
        self.assertEqual([c["id"] for c in found["owners"]], ["myhome_1"])

    def test_no_more_than_the_photo_cap_is_hashed(self):
        blocked = [
            item(100 + n, 900 + n, total_floors=18, images=[{"large": f"https://img/{n}"}])
            for n in range(30)
        ]
        web = FakeWeb(blocked, {})
        calls = []
        sources = photo_sources(web, post=[A], calls=calls)
        target = {**GLDANI, "total_floors": 15, "area_m2": 72, "photo_batch": "B1"}
        asyncio.run(search_owner(dict(target), self.conn, sources))
        self.assertEqual(len(calls), MAX_PHOTO_CANDIDATES)
        self.assertLessEqual(len(calls), MAX_PHOTO_CANDIDATES)

    def test_a_candidate_is_hashed_at_most_three_photos(self):
        owner = item(
            1,
            111,
            price={"2": {"price_total": 68000}},
            images=[{"large": f"https://img/1{k}"} for k in "abcde"],
        )
        web = FakeWeb([owner], {111: [owner]})
        calls = []
        sources = photo_sources(web, post=[A], images={}, calls=calls)
        asyncio.run(search_owner({**GLDANI, "photo_batch": "B1"}, self.conn, sources))
        self.assertTrue(calls)
        self.assertTrue(all(len(urls) <= 3 for urls in calls), calls)


if __name__ == "__main__":
    unittest.main()
