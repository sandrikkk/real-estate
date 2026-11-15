"""The nightly sweep marks MyHome listings that MyHome deleted (scripts/check_delisted.py).

X-Ray replies already check the MyHome duplicates they would show; the sweep keeps D1 itself
clean, so the photo search, the owner search and the next reply skip a deleted listing
without a check. It reads the least recently checked rows first and stamps alive_checked_at,
so nights rotate through the table. A page that does not load is left for another night, and
a known live listing read as deleted stops the run without marking anything.
"""

import asyncio
import os
import sqlite3
import tempfile
import unittest

from core.database import DatabaseEngine
from scripts.check_delisted import sweep
from tests.test_xray_lookup_alive import GONE_PAGE, statement_page, url


class TestSweep(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.temp_dir.name, "sweep.db")
        DatabaseEngine(db_path=path, use_d1=False).init_db()
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        for lid, sid, scraped, checked in [
            ("myhome_1", "1", "2026-09-01T00:00:00", "2026-11-10T00:00:00"),
            ("myhome_2", "2", "2026-09-02T00:00:00", None),
            ("myhome_3", "3", "2026-09-03T00:00:00", None),
            ("myhome_9", "9", "2026-11-15T00:00:00", None),  # newest: the control
            ("ss_ge_4", "4", "2026-09-04T00:00:00", None),
        ]:
            self.conn.execute(
                "INSERT INTO properties (id, source, source_id, title, price_usd, area_m2,"
                " price_per_m2, url, scraped_at, alive_checked_at)"
                " VALUES (?, ?, ?, 't', 1, 1, 1, 'u', ?, ?)",
                (lid, lid.rsplit("_", 1)[0], sid, scraped, checked),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def sweep(self, pages, limit=10):
        asked = []

        async def fetch_html(u):
            asked.append(u)
            return pages.get(u)

        return asyncio.run(sweep(self.conn, fetch_html, limit)), asked

    def column(self, name):
        return {
            r["id"]: r[name]
            for r in self.conn.execute(f"SELECT id, {name} FROM properties ORDER BY id")
        }

    def test_marks_deleted_stamps_checked_and_leaves_unloaded_pages(self):
        counts, asked = self.sweep(
            {url(9): statement_page(9), url(2): GONE_PAGE, url(1): statement_page(1)}
        )
        self.assertEqual(counts, {"checked": 3, "live": 1, "deleted": 1, "unknown": 1})
        delisted = self.column("delisted_at")
        self.assertIsNotNone(delisted["myhome_2"])
        self.assertTrue(all(v is None for k, v in delisted.items() if k != "myhome_2"))
        checked = self.column("alive_checked_at")
        self.assertIsNone(checked["myhome_3"])  # its page did not load: another night
        self.assertNotEqual(checked["myhome_1"], "2026-11-10T00:00:00")
        self.assertNotIn(url(4), asked)  # SS.ge rows are not MyHome's

    def test_least_recently_checked_first(self):
        _, asked = self.sweep({url(9): statement_page(9)}, limit=2)
        # The control, then the two never checked, oldest scraped first.
        self.assertEqual(asked, [url(9), url(2), url(3)])

    def test_a_control_that_reads_deleted_stops_the_run(self):
        counts, asked = self.sweep({url(9): GONE_PAGE, url(2): GONE_PAGE})
        self.assertIsNone(counts)
        self.assertEqual(asked, [url(9)])
        self.assertTrue(all(v is None for v in self.column("delisted_at").values()))

    def test_already_delisted_rows_are_not_read_again(self):
        self.conn.execute("UPDATE properties SET delisted_at = 'x' WHERE id = 'myhome_2'")
        self.conn.commit()
        _, asked = self.sweep({url(9): statement_page(9)})
        self.assertNotIn(url(2), asked)


if __name__ == "__main__":
    unittest.main()
