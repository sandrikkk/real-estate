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
from core.xray import SNAPSHOT_FIELDS
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

    def test_a_private_seller_whose_number_lists_one_flat_is_the_owner(self):
        fetch_html, _ = fake_myhome([list_item(1, user_type={"type": "physical"})])
        self.run_job(fetch_html)
        self.assertEqual(self.saved()[0]["is_owner"], 1)

    def test_a_private_seller_whose_number_lists_two_flats_is_a_broker(self):
        physical = {"user_type": {"type": "physical"}}
        items = [list_item(1, **physical), list_item(2, area=110, floor=7, **physical)]
        fetch_html, _ = fake_myhome(items)
        self.run_job(fetch_html)
        self.assertEqual(self.saved()[0]["is_owner"], 0)

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


def listing_page(extra=None, account_size=24, account=None):
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
    if account is None:
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
            details = xray_lookup.statement_details(scraper, st)
            self.assertNotEqual(details["is_owner"], 0, repr(empty))

    def test_myhomes_own_is_owner_flag_is_ignored(self):
        # It is false on every page an anonymous visitor gets, owners' included.
        scraper = MyHomeScraper()
        st = list_item(LISTING, user_type={"type": "physical"}, is_owner=False, comment="")
        self.assertIsNone(xray_lookup.statement_details(scraper, st)["is_owner"])

    def test_a_private_seller_whose_account_is_one_flat_is_the_owner(self):
        physical = {"user_type": {"type": "physical"}, "is_owner": False}
        page = listing_page(extra=physical, account=[list_item(LISTING, **physical)])
        self.run_job(page)
        self.assertEqual(self.saved()["is_owner"], 1)

    def test_a_private_seller_with_two_different_flats_is_a_broker(self):
        physical = {"user_type": {"type": "physical"}}
        account = [list_item(LISTING, **physical), list_item(900, area=110, floor=7, **physical)]
        self.run_job(listing_page(extra=physical, account=account))
        self.assertEqual(self.saved()["is_owner"], 0)

    def test_a_listing_job_keeps_the_phone_a_phone_job_stored(self):
        xray_lookup.save_live_listings(
            self.conn, PHONE, [xray_lookup.parse_myhome_list_item(list_item(LISTING))]
        )
        self.run_job(listing_page())
        self.assertEqual(self.saved()["phone9"], PHONE)
        self.assertEqual(self.saved()["author_listings_count"], 24)


OWNER_JOB = "33333333-4444-4555-8666-777777777777"
TARGET_ID = "44444444-5555-4666-8777-888888888888"


def quiet_sources(fail=False):
    """Owner-search sources that find nothing, or that break."""

    async def nothing(_):
        if fail:
            raise RuntimeError("portal down")
        return None

    return xray_lookup.Sources(
        myhome_html=nothing,
        ssge_search=nothing,
        ssge_details=nothing,
        ssge_normalize=lambda row: row,
        myhome_urbans={"გლდანი": [2]},
        ssge_subdistricts={"გლდანი": [32]},
    )


class TestXRayOwnerJob(unittest.TestCase):
    """An "owner" job: the owner search behind the "მეპატრონე" button (core/owner_search.py)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.temp_dir.name, "jobs.db")
        DatabaseEngine(db_path=path, use_d1=False).init_db()
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        target = {"district": "გლდანი", "micro": 6, "rooms": 3, "floor": "1"}
        self.conn.execute(
            "INSERT INTO xray_targets (id, chat_id, target, created_at) VALUES (?, '777', ?, ?)",
            (TARGET_ID, json.dumps(target, ensure_ascii=False), a_minute_ago()),
        )
        self.conn.execute(
            "INSERT INTO xray_jobs (id, kind, phone9, target_id, chat_id, status, created_at)"
            " VALUES (?, 'owner', '', ?, '777', 'queued', ?)",
            (OWNER_JOB, TARGET_ID, a_minute_ago()),
        )
        self.conn.commit()
        self.notified = []

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def run_job(self, sources):
        return asyncio.run(
            xray_lookup.run_job(
                OWNER_JOB, self.conn, None, MyHomeScraper(), self.notified.append, sources
            )
        )

    def job(self):
        return dict(
            self.conn.execute("SELECT * FROM xray_jobs WHERE id = ?", (OWNER_JOB,)).fetchone()
        )

    def test_the_report_and_its_candidates_are_stored_for_the_follow_up(self):
        self.assertEqual(self.run_job(quiet_sources())["status"], "done")
        job = self.job()
        stored = json.loads(job["result"])
        self.assertIn("მესაკუთრის ძებნა:</b> გლდანი, მე-6 მიკრო, 3 ოთახი, 1 სართ.", stored["text"])
        self.assertEqual(stored["candidates"], [])
        self.assertIn("⚠️ myhome ახლა არ პასუხობს", stored["text"])
        self.assertEqual(self.notified, [OWNER_JOB])

    def test_a_crash_fails_the_job_and_still_tells_the_user(self):
        self.assertEqual(self.run_job(quiet_sources(fail=True))["status"], "failed")
        self.assertIsNone(self.job()["result"])
        self.assertEqual(self.notified, [OWNER_JOB])


PHOTO_JOB = "55555555-6666-4777-8888-999999999999"
PHOTO_BATCH = "777:g1"


def noise_png(seed):
    import io
    import random

    from PIL import Image

    rng = random.Random(seed)
    img = Image.frombytes("L", (64, 64), bytes(rng.randrange(256) for _ in range(64 * 64)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class TestXRayPhotoJob(unittest.TestCase):
    """A "photo" job: the photos of a post against the listings in the database (ADR 0005)."""

    def setUp(self):
        from core.dedup import _decode_dhash_sync

        self.temp_dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.temp_dir.name, "photo.db")
        DatabaseEngine(db_path=path, use_d1=False).init_db()
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "INSERT INTO properties (id, source, source_id, title, price_usd, area_m2,"
            " price_per_m2, district, street, floor, total_floors, rooms, url, scraped_at)"
            " VALUES ('myhome_1', 'myhome', '1', 'ბინა', 95000, 58, 1600, 'დიდი დიღომი', 'ქუჩა',"
            " '3', 18, 3, 'https://www.myhome.ge/ka/pr/1/', '2026-11-08T09:00:00')"
        )
        for i, seed in enumerate((1, 2, 3)):
            self.conn.execute(
                "INSERT INTO image_hashes (listing_id, image_index, phash) VALUES ('myhome_1', ?, ?)",
                (i, _decode_dhash_sync(noise_png(seed))),
            )
        for n, seed in enumerate((1, 2), 1):
            self.conn.execute(
                "INSERT INTO xray_photos (batch_id, message_id, chat_id, file_id, created_at)"
                " VALUES (?, ?, '777', ?, '2026-01-01T00:00:00.000Z')",
                (PHOTO_BATCH, n, f"f{n}"),
            )
        self.conn.execute(
            "INSERT INTO xray_photo_batches (batch_id, chat_id, created_at)"
            " VALUES (?, '777', '2026-01-01T00:00:00.000Z')",
            (PHOTO_BATCH,),
        )
        self.conn.execute(
            "INSERT INTO xray_jobs (id, kind, phone9, target_id, chat_id, status, created_at)"
            " VALUES (?, 'photo', '', ?, '777', 'queued', ?)",
            (PHOTO_JOB, PHOTO_BATCH, a_minute_ago()),
        )
        self.conn.commit()
        self.files = {"f1": noise_png(1), "f2": noise_png(2)}
        self.notified = []

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    async def download(self, file_id):
        return self.files.get(file_id)

    def run_job(self, download="default"):
        return asyncio.run(
            xray_lookup.run_job(
                PHOTO_JOB,
                self.conn,
                None,
                MyHomeScraper(),
                self.notified.append,
                None,
                self.download if download == "default" else download,
            )
        )

    def job(self):
        return dict(
            self.conn.execute("SELECT * FROM xray_jobs WHERE id = ?", (PHOTO_JOB,)).fetchone()
        )

    def test_the_matching_listing_and_its_candidates_are_stored_for_the_follow_up(self):
        outcome = self.run_job()
        self.assertEqual((outcome["status"], outcome["found"]), ("done", 1))
        stored = json.loads(self.job()["result"])
        cand0 = stored["candidates"][0]
        self.assertEqual(cand0["id"], "myhome_1")
        self.assertEqual(cand0["url"], "https://www.myhome.ge/ka/pr/1/")
        self.assertGreaterEqual(cand0.get("photo_matches", 0), 1)
        self.assertEqual(cand0["area_m2"], 58)
        self.assertTrue(set(cand0.keys()).issubset(set(SNAPSHOT_FIELDS) | {"url", "photo_matches"}))
        self.assertIn("✅ <b>იგივე ფოტოები:</b>", stored["text"])
        self.assertEqual(self.notified, [PHOTO_JOB])

    def test_a_photo_job_without_a_downloader_fails_and_still_tells_the_user(self):
        self.assertEqual(self.run_job(download=None)["status"], "failed")
        self.assertIsNone(self.job()["result"])
        self.assertEqual(self.notified, [PHOTO_JOB])

    def test_photos_that_cannot_be_downloaded_fail_the_job(self):
        self.files.clear()
        self.assertEqual(self.run_job()["status"], "failed")
        self.assertEqual(self.notified, [PHOTO_JOB])


class _Reply:
    """What urllib.request.urlopen returns: a context manager with read()."""

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


class TestTelegramDownloader(unittest.TestCase):
    TOKEN = "123456:SECRET-TOKEN"

    def download(self, replies):
        import contextlib
        import io
        from unittest import mock

        asked = []

        def fake_urlopen(url, timeout=None):
            asked.append(url)
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        out = io.StringIO()
        with mock.patch.object(xray_lookup.urllib.request, "urlopen", fake_urlopen):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                data = asyncio.run(xray_lookup.telegram_downloader(self.TOKEN)("AgAC file/id"))
        return data, asked, out.getvalue()

    def test_a_file_is_fetched_in_two_steps(self):
        data, asked, _ = self.download(
            [
                _Reply(json.dumps({"ok": True, "result": {"file_path": "photos/a.jpg"}}).encode()),
                _Reply(b"IMAGE-BYTES"),
            ]
        )
        self.assertEqual(data, b"IMAGE-BYTES")
        self.assertEqual(
            asked[0], f"https://api.telegram.org/bot{self.TOKEN}/getFile?file_id=AgAC%20file%2Fid"
        )
        self.assertEqual(asked[1], f"https://api.telegram.org/file/bot{self.TOKEN}/photos/a.jpg")

    def test_a_failure_gives_none_and_prints_nothing_that_holds_the_token(self):
        data, _, printed = self.download([OSError(f"failed for bot{self.TOKEN}")])
        self.assertIsNone(data)
        self.assertNotIn("SECRET", printed)
        self.assertEqual(printed, "")

    def test_an_answer_without_a_file_path_gives_none(self):
        data, asked, _ = self.download([_Reply(json.dumps({"ok": False}).encode())])
        self.assertIsNone(data)
        self.assertEqual(len(asked), 1)

    def test_a_file_over_the_size_limit_gives_none(self):
        from core.dedup import MAX_IMAGE_BYTES

        data, _, _ = self.download(
            [
                _Reply(json.dumps({"result": {"file_path": "p.jpg"}}).encode()),
                _Reply(b"x" * (MAX_IMAGE_BYTES + 5)),
            ]
        )
        self.assertIsNone(data)


class TestXRayTablesMigration(unittest.TestCase):
    """Production D1 got the first X-Ray tables; init_db must add the columns that came later."""

    def test_old_tables_get_the_new_columns_and_take_a_listing_job(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.db")
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE xray_jobs (id TEXT PRIMARY KEY, phone9 TEXT NOT NULL,"
                " chat_id TEXT NOT NULL, status TEXT NOT NULL, found INTEGER,"
                " page_full INTEGER, created_at TEXT NOT NULL, done_at TEXT)"
            )
            # The columns production D1 reported on 2026-11-08.
            conn.execute(
                "CREATE TABLE xray_live_listings (id TEXT PRIMARY KEY, source TEXT, source_id TEXT,"
                " phone9 TEXT, author_id TEXT, user_type TEXT, is_owner INTEGER, district TEXT,"
                " subdistrict TEXT, street TEXT, area_m2 REAL, floor TEXT, total_floors INTEGER,"
                " rooms INTEGER, bedrooms INTEGER, price_usd REAL, deal_type TEXT, url TEXT,"
                " seen_at TEXT)"
            )
            conn.commit()
            conn.close()
            DatabaseEngine(db_path=path, use_d1=False).init_db()
            conn = sqlite3.connect(path)
            jobs = {r[1] for r in conn.execute("PRAGMA table_info(xray_jobs)")}
            live = {r[1] for r in conn.execute("PRAGMA table_info(xray_live_listings)")}
            self.assertTrue({"kind", "listing_id"} <= jobs)
            self.assertTrue({"phone_number", "author_listings_count"} <= live)
            # The Worker writes phone9 "" for a listing job, which the old NOT NULL accepts.
            conn.execute(
                "INSERT INTO xray_jobs (id, kind, phone9, listing_id, chat_id, status, created_at)"
                " VALUES ('j', 'listing', '', '1', 'c', 'queued', 't')"
            )
            self.assertEqual(conn.execute("SELECT kind FROM xray_jobs").fetchone()[0], "listing")
            conn.close()


if __name__ == "__main__":
    unittest.main()
