"""An "alive" job: are the MyHome listings an X-Ray reply showed still on MyHome?

The Worker cannot read MyHome (ADR 0004), so it shows MyHome duplicates from D1 unchecked. The
reply queues an "alive" job with the shown listings' ids in reply order; the job reads each
MyHome listing's page, marks a deleted one with properties.delisted_at (so no reply shows it
again) and stores a follow-up naming it by its number in the reply.

A listing is deleted when its page loads without a statement or with is_active false. A page
that does not load says nothing. A known live listing is read first as a control: when it
too looks deleted, MyHome's page has changed and nothing is marked.
"""

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest

from core.database import DatabaseEngine
from scrapers.myhome import MyHomeScraper
from tests.test_owner_search import page
from tests.test_xray_lookup_job import a_minute_ago, xray_lookup

JOB_ID = "1c2d3e4f-0000-4000-8000-000000000001"


def statement_page(source_id, active=True):
    q = {"queryKey": ["statement", str(source_id)]}
    q["state"] = {"data": {"data": {"statement": {"id": source_id, "is_active": active}}}}
    return page([q])


GONE_PAGE = page([{"queryKey": ["statement", "x"], "state": {"data": {"data": {}}}}])


def url(source_id):
    return f"https://www.myhome.ge/ka/pr/{source_id}/"


class TestAliveJob(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.temp_dir.name, "alive.db")
        DatabaseEngine(db_path=path, use_d1=False).init_db()
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        for lid, source, sid, price, at in [
            ("myhome_11", "myhome", "11", 79000, "2026-10-29T00:00:00"),
            ("ss_ge_12", "ss_ge", "12", 80000, "2026-11-01T00:00:00"),
            ("myhome_13", "myhome", "13", 81000, "2026-11-02T00:00:00"),
            ("myhome_99", "myhome", "99", 90000, "2026-11-15T00:00:00"),  # newest: the control
        ]:
            self.conn.execute(
                "INSERT INTO properties (id, source, source_id, title, price_usd, area_m2,"
                " price_per_m2, url, scraped_at) VALUES (?, ?, ?, 't', ?, 61, 1, 'u', ?)",
                (lid, source, sid, price, at),
            )
        self.conn.commit()
        self.notified = []

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def queue(self, ids):
        self.conn.execute(
            "INSERT INTO xray_jobs (id, kind, phone9, target_id, chat_id, status, created_at)"
            " VALUES (?, 'alive', '', ?, '777', 'queued', ?)",
            (JOB_ID, json.dumps(ids), a_minute_ago()),
        )
        self.conn.commit()

    def run_alive(self, pages):
        asked = []

        async def fetch_html(u):
            asked.append(u)
            return pages.get(u)

        outcome = asyncio.run(
            xray_lookup.run_job(
                JOB_ID, self.conn, fetch_html, MyHomeScraper(), self.notified.append
            )
        )
        return outcome, asked

    def delisted(self):
        return {
            r["id"]
            for r in self.conn.execute("SELECT id FROM properties WHERE delisted_at IS NOT NULL")
        }

    def result(self):
        row = self.conn.execute("SELECT result FROM xray_jobs WHERE id = ?", (JOB_ID,)).fetchone()
        return json.loads(row["result"])

    def test_a_deleted_listing_is_marked_and_named_by_its_reply_number(self):
        self.queue(["ss_ge_12", "myhome_11", "myhome_13"])
        outcome, asked = self.run_alive(
            {url(99): statement_page(99), url(11): GONE_PAGE, url(13): statement_page(13)}
        )
        self.assertEqual(outcome["status"], "done")
        self.assertEqual(self.delisted(), {"myhome_11"})
        self.assertNotIn(url(12), asked)  # SS.ge rows are the Worker's to check
        result = self.result()
        self.assertEqual(result["gone"], ["myhome_11"])
        self.assertIn("2. MyHome — $79,000", result["text"])
        self.assertNotIn("81,000", result["text"])
        self.assertEqual(self.notified, [JOB_ID])

    def test_an_inactive_statement_counts_as_deleted(self):
        self.queue(["myhome_13"])
        self.run_alive({url(99): statement_page(99), url(13): statement_page(13, active=False)})
        self.assertEqual(self.delisted(), {"myhome_13"})

    def test_a_page_that_does_not_load_marks_nothing(self):
        self.queue(["myhome_11"])
        outcome, _ = self.run_alive({url(99): statement_page(99)})
        self.assertEqual(self.delisted(), set())
        self.assertEqual(self.result(), {"gone": [], "text": None})
        self.assertEqual(outcome["status"], "done")

    def test_when_the_control_looks_deleted_nothing_is_marked(self):
        self.queue(["myhome_11", "myhome_13"])
        _, asked = self.run_alive({url(99): GONE_PAGE, url(11): GONE_PAGE, url(13): GONE_PAGE})
        self.assertEqual(self.delisted(), set())
        self.assertEqual(self.result(), {"gone": [], "text": None})

    def test_the_control_is_read_only_when_something_looks_deleted(self):
        self.queue(["myhome_13"])
        _, asked = self.run_alive({url(13): statement_page(13)})
        self.assertEqual(asked, [url(13)])
        self.assertEqual(self.delisted(), set())

    def test_a_marked_listing_is_not_shown_by_the_photo_or_owner_search_reads(self):
        self.queue(["myhome_11"])
        self.run_alive({url(99): statement_page(99), url(11): GONE_PAGE})
        from core.photo_match import load_properties

        self.assertEqual(set(load_properties(self.conn, ["myhome_11", "myhome_13"])), {"myhome_13"})


if __name__ == "__main__":
    unittest.main()
