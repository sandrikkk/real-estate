"""ADR 0004: the GitHub Actions job that reads MyHome for a queued X-Ray phone lookup.

Runs scripts/xray_lookup.py against a local SQLite copy of the D1 tables, with fake MyHome
pages. Nothing here touches the network.
"""

import asyncio
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from core.database import DatabaseEngine
from scrapers.myhome import MyHomeScraper

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "xray_lookup", os.path.join(REPO, "scripts", "xray_lookup.py")
)
xray_lookup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xray_lookup)

PHONE = "555000111"
JOB_ID = "0b0e9b1c-1d2e-4f3a-8b4c-5d6e7f8a9b0c"
SEARCH_URL = f"https://www.myhome.ge/ka/s/udzravi-qoneba/?q={PHONE}"


def a_minute_ago():
    t = datetime.now(timezone.utc) - timedelta(minutes=1)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
        "room": "3",
        "deal_type_id": 1,
        "real_estate_type_id": 1,
        "price": {"2": {"price_total": 60000 + item_id}},
    }
    item.update(extra)
    return item


def next_data_page(queries):
    data = {"props": {"pageProps": {"dehydratedState": {"queries": queries}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def fake_myhome(items, masked=f"{PHONE[:6]}***", search_ok=True):
    """MyHome pages as fetch_html would return them; None stands for a failed request."""
    asked = []

    async def fetch_html(url):
        asked.append(url)
        if url == SEARCH_URL:
            if not search_ok:
                return None
            ours = {"queryKey": ["statements", "list", {"query": {"q": PHONE}}]}
            ours["state"] = {"data": {"data": {"data": items}}}
            developers = {
                "queryKey": [
                    "statements",
                    "list",
                    {"query": {"q": PHONE, "owner_type": "developer"}},
                ],
                "state": {"data": {"data": {"data": [list_item(999999)]}}},
            }
            return next_data_page([ours, developers])
        for it in items:
            if url == f"https://www.myhome.ge/ka/pr/{it['id']}/":
                statement = {**it, "user_phone_number": masked, "comment": ""}
                return next_data_page([{"state": {"data": {"data": {"statement": statement}}}}])
        return None

    return fetch_html, asked


class TestXRayLookupJob(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "jobs.db")
        DatabaseEngine(db_path=self.db_path, use_d1=False).init_db()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "INSERT INTO xray_jobs (id, phone9, chat_id, status, created_at)"
            " VALUES (?, ?, ?, 'queued', ?)",
            (JOB_ID, PHONE, "777", a_minute_ago()),
        )
        self.conn.commit()
        self.notified = []

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def run_job(self, fetch_html, job_id=JOB_ID):
        return asyncio.run(
            xray_lookup.run_job(
                job_id, self.conn, fetch_html, MyHomeScraper(), self.notified.append
            )
        )

    def job(self):
        return dict(self.conn.execute("SELECT * FROM xray_jobs WHERE id = ?", (JOB_ID,)).fetchone())

    def saved(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM xray_live_listings ORDER BY id")]

    def test_found_listings_are_saved_the_job_is_done_and_the_worker_is_told(self):
        fetch_html, asked = fake_myhome([list_item(1), list_item(2)])
        outcome = self.run_job(fetch_html)
        self.assertEqual(outcome, {"status": "done", "found": 2, "full": False})
        self.assertEqual(asked[0], SEARCH_URL, "only the plain 9 digits go to MyHome")
        job = self.job()
        self.assertEqual((job["status"], job["found"], job["page_full"]), ("done", 2, 0))
        self.assertTrue(
            job["done_at"].endswith("Z"), "done_at must compare with the Worker's ISO times"
        )
        saved = self.saved()
        self.assertEqual([r["id"] for r in saved], ["myhome_1", "myhome_2"])
        self.assertTrue(all(r["phone9"] == PHONE for r in saved))
        self.assertGreaterEqual(saved[0]["seen_at"], job["created_at"])
        self.assertEqual(saved[0]["is_owner"], 0, "the first listing's page marks an agent")
        self.assertEqual(self.notified, [JOB_ID])

    def test_first_hit_under_another_number_fails_the_job_and_saves_nothing(self):
        fetch_html, _ = fake_myhome([list_item(1)], masked="599111***")
        self.assertEqual(self.run_job(fetch_html)["status"], "failed")
        self.assertEqual(self.job()["status"], "failed")
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.notified, [JOB_ID], "the user still gets told")

    def test_an_unreadable_search_page_fails_the_job(self):
        fetch_html, _ = fake_myhome([list_item(1)], search_ok=False)
        self.assertEqual(self.run_job(fetch_html)["status"], "failed")
        self.assertEqual(self.saved(), [])

    def test_no_listings_is_a_finished_lookup_not_a_failure(self):
        fetch_html, asked = fake_myhome([])
        self.assertEqual(self.run_job(fetch_html), {"status": "done", "found": 0, "full": False})
        self.assertEqual(asked, [SEARCH_URL], "no listing page is needed for zero hits")

    def test_an_unknown_job_is_left_alone(self):
        fetch_html, asked = fake_myhome([list_item(1)])
        other = "11111111-2222-4333-8444-555555555555"
        self.assertEqual(self.run_job(fetch_html, job_id=other), {"status": "missing"})
        self.assertEqual((asked, self.notified), ([], []))

    def test_a_malformed_job_id_never_reaches_d1(self):
        self.assertEqual(xray_lookup.main(["--job", "1; DROP TABLE xray_jobs"]), 2)


LISTING_JOB = "22222222-3333-4444-8555-666666666666"
LISTING = 26574188


def listing_page(extra=None, account_size=24):
    """A MyHome listing page (rooms as room_type_id) and its account's search page."""
    statement = {
        "id": LISTING,
        "user_id": 4242,
        "user_type": {"type": "agent"},
        "urban_name": "გლდანი",
        "address": "გლდანი - VI მ/რ",
        "area": 72,
        "floor": 1,
        "room_type_id": 3,
        "bedroom_type_id": 2,
        "deal_type_id": 1,
        "real_estate_type_id": 1,
        "price": {"2": {"price_total": 61000}},
        "user_phone_number": "568443***",
        "comment": "",
        **(extra or {}),
    }
    account = [list_item(LISTING)] + [list_item(500 + i) for i in range(account_size - 1)]

    async def fetch_html(url):
        if url == f"https://www.myhome.ge/ka/pr/{LISTING}/":
            return next_data_page([{"state": {"data": {"data": {"statement": statement}}}}])
        if url == "https://www.myhome.ge/ka/s/udzravi-qoneba/?users=4242":
            q = {"queryKey": ["statements", "list", {"query": {"users": "4242"}}]}
            q["state"] = {"data": {"data": {"data": account}}}
            return next_data_page([q])
        return None

    return fetch_html


class TestXRayListingJob(unittest.TestCase):
    """A "listing" job: the X-Ray of a MyHome link, whose page the Worker cannot read."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "jobs.db")
        DatabaseEngine(db_path=self.db_path, use_d1=False).init_db()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "INSERT INTO xray_jobs (id, kind, listing_id, chat_id, status, created_at)"
            " VALUES (?, 'listing', ?, ?, 'queued', ?)",
            (LISTING_JOB, str(LISTING), "777", a_minute_ago()),
        )
        self.conn.commit()
        self.notified = []

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def run_job(self, fetch_html):
        return asyncio.run(
            xray_lookup.run_job(
                LISTING_JOB, self.conn, fetch_html, MyHomeScraper(), self.notified.append
            )
        )

    def saved(self):
        return self.conn.execute(
            "SELECT * FROM xray_live_listings WHERE id = ?", (f"myhome_{LISTING}",)
        ).fetchone()

    def test_the_listing_and_its_account_size_are_saved(self):
        outcome = self.run_job(listing_page())
        self.assertEqual(outcome, {"status": "done", "found": 1, "full": True})
        row = self.saved()
        self.assertEqual(
            row["author_listings_count"], 24, "the account's listings are the agent signal"
        )
        self.assertEqual(row["rooms"], 3, "room_type_id from the listing page")
        self.assertEqual(row["is_owner"], 0)
        self.assertEqual(row["phone_number"], "568443***")
        self.assertIsNone(row["phone9"])
        self.assertEqual(self.notified, [LISTING_JOB])

    def test_a_listing_already_in_properties_gets_its_owner_fields_and_nothing_is_inserted(self):
        self.conn.execute(
            "INSERT INTO properties (id, source, source_id, title, price_usd, area_m2, price_per_m2,"
            " url, scraped_at, is_owner, author_listings_count)"
            " VALUES (?, 'myhome', ?, 'ბინა', 61000, 72, 847, ?, '2026-11-08T09:00:00', NULL, NULL)",
            (f"myhome_{LISTING}", str(LISTING), f"https://www.myhome.ge/ka/pr/{LISTING}/"),
        )
        self.conn.commit()
        self.run_job(listing_page())
        prop = self.conn.execute("SELECT * FROM properties").fetchall()
        self.assertEqual(len(prop), 1)
        self.assertEqual((prop[0]["is_owner"], prop[0]["author_listings_count"]), (0, 24))

    def test_a_listing_not_in_properties_stays_out_of_it(self):
        self.run_job(listing_page())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0], 0)

    def test_an_unreadable_listing_page_fails_the_job(self):
        async def down(url):
            return None

        self.assertEqual(self.run_job(down)["status"], "failed")
        self.assertIsNone(self.saved())
        self.assertEqual(self.notified, [LISTING_JOB])

    def test_empty_agency_objects_are_not_agent_evidence(self):
        # Incident rule once held by the Worker's parseMyHomeStatementDetails: MyHome sends
        # {} / [] / "" for an absent agency, and that must not turn an owner into an agent.
        scraper = MyHomeScraper()
        base = list_item(LISTING, user_type={"type": "physical"}, is_owner=True, comment="")
        for empty in ({}, [], {"id": None}, {"name": ""}, ""):
            st = {**base, "agency": empty, "broker": empty, "user_statements_count": 1}
            self.assertEqual(xray_lookup.statement_details(scraper, st)["is_owner"], 1, repr(empty))

    def test_a_listing_job_keeps_the_phone_a_phone_job_stored(self):
        xray_lookup.save_live_listings(
            self.conn, PHONE, [xray_lookup.parse_myhome_list_item(list_item(LISTING))]
        )
        self.run_job(listing_page())
        self.assertEqual(self.saved()["phone9"], PHONE)
        self.assertEqual(self.saved()["author_listings_count"], 24)


if __name__ == "__main__":
    unittest.main()
