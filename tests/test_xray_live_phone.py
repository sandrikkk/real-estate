"""ADR 0003 stage 1 with ADR 0004: the phone X-Ray (Python twin of the Worker).

Mirrors tests/js/xray_live_phone.test.mjs. `live_phone` stands for the Worker's
myHomeLiveState(): what the GitHub Actions lookup job found, or that a job was queued.
The MyHome parsing and guard helpers tested here are also used by scripts/xray_lookup.py.
"""

import os
import sqlite3
import tempfile
import unittest

from core.database import DatabaseEngine
from core.xray import (
    XRAY_LIVE_COLUMNS,
    _candidate_matches_target,
    _compare_location,
    _extract_micro_district,
    find_statements_list,
    live_phone_result,
    parse_myhome_list_item,
    parse_xray_input,
    run_xray_on_db,
    save_live_listings,
)

PHONE = "555000111"
PHONE_FMT = "+995 555 00 01 11"
# The user's example post (2026-11-07) with a made-up phone number. It has no area.
GLDANI_POST = (
    "იყიდება ბინა , გლდანის მე-6მიკრო, სამ ოთახიანი ჩეხური პროექტის, სართული 1 ,ბინას აქვს აივანი და "
    "მოწესრიგებული სარდაფი, გაკეთებულია ცეტრალური გათბობა, რჩება ავეჯი და ტექნიკა. \n"
    "დეტალებზე დამირეკეთ:" + PHONE
)


def list_item(item_id, **extra):
    item = {
        "id": item_id,
        "user_id": 4242,
        "user_type": {"type": "agent"},
        "urban_name": "გლდანი",
        "district_name": "გლდანი-ნაძალადევი",
        "address": "გლდანი - VI მ/რ",
        "area": 70 + item_id % 5,
        "floor": 1,
        "total_floors": 5,
        "room": "3",
        "bedroom": "2",
        "deal_type_id": 1,
        "price": {"2": {"price_total": 60000 + item_id}},
    }
    item.update(extra)
    return item


def search_page(query, items):
    """__NEXT_DATA__ of a search page: our list plus the developer-projects block."""
    developers = {**query, "owner_type": "developer", "per_page": "12"}
    queries = [
        {
            "queryKey": ["statements", "list", {"query": query}],
            "state": {"data": {"data": {"data": items}}},
        },
        {
            "queryKey": ["statements", "list", {"query": developers}],
            "state": {"data": {"data": {"data": [list_item(999999)]}}},
        },
    ]
    return {"props": {"pageProps": {"dehydratedState": {"queries": queries}}}}


def live(items, masked=f"{PHONE[:6]}***"):
    statement = {"user_phone_number": masked} if items else None
    details = {"is_owner": 0, "user_type": "agent", "phone_number": masked} if items else None
    return live_phone_result(PHONE, items, statement, details)


def state(mode, ids=(), full=False):
    return {"mode": mode, "rows": [parse_myhome_list_item(list_item(i)) for i in ids], "full": full}


class TestLivePhoneParsing(unittest.TestCase):
    def test_post_without_area_keeps_flat_details_as_a_hint(self):
        p = parse_xray_input(GLDANI_POST)
        self.assertEqual(p["kind"], "phone")
        self.assertEqual(p["phone_9"], PHONE)
        self.assertEqual(p["flat"]["district"], "გლდანი")
        self.assertEqual(p["flat"]["micro"], 6)
        self.assertEqual(p["flat"]["rooms"], 3)
        self.assertEqual(p["flat"]["floor"], "1")
        # The hint is not a parsed fact: the golden set scores the top-level fields only.
        self.assertNotIn("district", p)

    def test_micro_district_forms(self):
        cases = [
            ("გლდანის მე-6მიკრო", 6),
            ("გლდანი - VI მ/რ", 6),
            ("6 მ/რ", 6),
            ("მე-6 მიკრორაიონი", 6),
            ("6-ე მიკრორაიონი", 6),
            ("გლდანი - I მ/რ 23ბ", 1),
            ("თემქაზე, 3 მ/რ, 4 კვარტალი", 3),
            ("ვაკე, ჭავჭავაძის გამზ. 6", None),
            ("", None),
        ]
        for text, want in cases:
            self.assertEqual(_extract_micro_district(text), want, text)

    def test_matcher_micro_district_conflict_rejects_and_agreement_corroborates(self):
        target = {"district": "გლდანი", "micro": 6, "rooms": 3, "floor": "1", "area_m2": 72}
        other = {"district": "გლდანი", "street": "გლდანი - III მ/რ", "rooms": 3, "floor": "1"}
        same = {"district": "გლდანი", "street": "გლდანი - VI მ/რ 23ბ", "rooms": None, "floor": "1"}
        self.assertEqual(_compare_location(target, other), "conflict")
        self.assertFalse(_candidate_matches_target(target, other))
        self.assertEqual(_compare_location(target, same), "agree")
        self.assertTrue(_candidate_matches_target(target, same))


class TestLivePhoneResult(unittest.TestCase):
    def test_statements_list_takes_only_the_exact_query(self):
        items = [list_item(1)]
        got = find_statements_list(search_page({"q": PHONE}, items), {"q": PHONE})
        self.assertEqual([i["id"] for i in got], [1])
        self.assertIsNone(find_statements_list(search_page({"q": "599"}, items), {"q": PHONE}))
        self.assertIsNone(find_statements_list(None, {"q": PHONE}))

    def test_rows_page_full_flag_and_enriched_first_row(self):
        r = live([list_item(1000 + i) for i in range(24)])
        self.assertTrue(r["ok"])
        self.assertTrue(r["full"])
        first = r["rows"][0]
        self.assertEqual(first["id"], "myhome_1000")
        self.assertEqual(first["author_id"], "4242")
        self.assertEqual(first["district"], "გლდანი")
        self.assertEqual(first["street"], "გლდანი - VI მ/რ")
        self.assertEqual(first["rooms"], 3)
        self.assertEqual(first["floor"], "1")
        self.assertEqual(first["price_usd"], 61000)
        self.assertEqual(first["is_owner"], 0)

    def test_first_hit_under_another_number_means_nothing_is_used(self):
        r = live([list_item(1)], masked="599111***")
        self.assertFalse(r["ok"])
        self.assertEqual(r["rows"], [])

    def test_failed_search_is_not_no_listings(self):
        self.assertFalse(live_phone_result(PHONE, None, None)["ok"])
        empty = live([])
        self.assertTrue(empty["ok"])
        self.assertEqual(empty["rows"], [])


class TestLivePhoneXRay(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_xray_live.db")
        self.db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        self.db.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _query(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _insert_property(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO properties (id, source, source_id, title, price_usd, area_m2, price_per_m2,"
            " district, street, floor, rooms, url, scraped_at, phone_number, is_owner, user_type,"
            " author_id, author_listings_count)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "myhome_2", "myhome", "2", "ბინა", 60002, 72, 833, "გლდანი", "გლდანი - VI მ/რ",
                "1", 3, "https://www.myhome.ge/ka/pr/2/", "2026-11-08T09:00:00", PHONE_FMT, 0,
                "agent", "4242", 2,
            ),
        )  # fmt: skip
        conn.commit()
        conn.close()

    def test_a_recent_lookup_merges_with_d1_rows_without_duplicates_and_links_the_list(self):
        self._insert_property()
        report = run_xray_on_db(self.db, GLDANI_POST, live_phone=state("fresh", (1, 2)))
        self.assertIn("ნაპოვნი განცხადებები (2)", report)
        self.assertEqual(report.count("myhome.ge/ka/pr/2/"), 1)
        self.assertIn("myhome.ge/ka/pr/1/", report)
        self.assertIn("myhome-ზე ამ ნომერზე <b>2</b>", report)
        self.assertIn(f"https://www.myhome.ge/ka/s/udzravi-qoneba/?q={PHONE}", report)

    def test_full_first_page_shows_n_plus(self):
        report = run_xray_on_db(
            self.db, GLDANI_POST, live_phone=state("fresh", range(100, 124), full=True)
        )
        self.assertIn("myhome-ზე ამ ნომერზე <b>24+</b>", report)
        self.assertIn("ნაპოვნი განცხადებები (24+)", report)

    def test_a_queued_lookup_answers_from_d1_now(self):
        self._insert_property()
        report = run_xray_on_db(self.db, GLDANI_POST, live_phone=state("queued"))
        self.assertIn("🔄 myhome-ზე ვეძებ", report)
        self.assertIn("myhome.ge/ka/pr/2/", report)

    def test_no_lookup_possible_says_so(self):
        self._insert_property()
        report = run_xray_on_db(self.db, GLDANI_POST, live_phone=state("failed"))
        self.assertIn("myhome-ის შედეგი ახლა ვერ მივიღეთ, ნაჩვენებია მხოლოდ ჩვენი ბაზა", report)

    def test_older_rows_are_shown_as_last_seen_while_a_new_lookup_runs(self):
        report = run_xray_on_db(self.db, GLDANI_POST, live_phone=state("queued", (5,)))
        self.assertIn("myhome.ge/ka/pr/5/", report)
        self.assertIn("ბოლოს ნანახიც ჩანს", report)

    def test_nothing_anywhere_says_so_plainly(self):
        report = run_xray_on_db(self.db, GLDANI_POST, live_phone=state("fresh"))
        self.assertIn("ვერც myhome-ზე და ვერც ჩვენს ბაზაში", report)

    def test_saved_live_rows_never_go_into_properties(self):
        conn = sqlite3.connect(self.db_path)
        save_live_listings(conn, PHONE, state("fresh", (1,))["rows"])
        conn.close()
        self.assertEqual(self._query("SELECT COUNT(*) FROM properties")[0][0], 0)
        self.assertEqual(
            self._query("SELECT id, phone9 FROM xray_live_listings"), [("myhome_1", PHONE)]
        )

    def test_live_columns_match_the_table(self):
        table_cols = tuple(r[1] for r in self._query("PRAGMA table_info(xray_live_listings)"))
        self.assertEqual(table_cols, XRAY_LIVE_COLUMNS)


if __name__ == "__main__":
    unittest.main()


class TestLinkMyHomeCheck(unittest.TestCase):
    """Mirrors the Worker's link path notes for a MyHome listing read by a listing job."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DatabaseEngine(db_path=os.path.join(self.temp_dir.name, "link.db"), use_d1=False)
        self.db.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_an_unknown_listing_says_a_check_is_running(self):
        report = run_xray_on_db(
            self.db, "https://www.myhome.ge/ka/pr/26574188/", myhome_check="queued"
        )
        self.assertIn("ეს განცხადება ჩვენს ბაზაში ჯერ არ არის", report)
        self.assertIn("🔄 myhome-ზე ვამოწმებ", report)

    def test_without_a_check_the_old_message_stays(self):
        report = run_xray_on_db(self.db, "https://www.myhome.ge/ka/pr/26574188/")
        self.assertIn("პორტალიდან მონაცემების წამოღება ვერ მოხერხდა", report)
