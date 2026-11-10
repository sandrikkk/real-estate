"""Tests for the X-Ray golden pairs builder (core/golden_pairs.py, ADR 0002)."""

import json
import os
import sqlite3
import tempfile
import unittest

from core.database import DatabaseEngine
from core.golden_pairs import build_golden_pairs, summarize, to_jsonl


class TestGoldenPairs(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.temp_dir.name, "golden.db")
        DatabaseEngine(db_path=path, use_d1=False).init_db()
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

        # properties: myhome_1
        self.conn.execute(
            """
            INSERT INTO properties (
                id, source, source_id, title, price_usd, area_m2, price_per_m2,
                district, street, rooms, floor, total_floors, phone_number, url, scraped_at
            ) VALUES (
                'myhome_1', 'myhome', '1', 'ბინა', 61000, 72, 847,
                'გლდანი', 'გლდანი - VI მ/რ', 3, '1', 9, '555000111',
                'https://www.myhome.ge/ka/pr/1/', '2026-11-08T09:00:00Z'
            )
            """
        )

        # xray_results: R1, R2, R3
        self.conn.execute(
            """
            INSERT INTO xray_results (id, chat_id, kind, target, candidates, owner_target_id, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                "R1",
                "777",
                "dup",
                json.dumps(
                    {
                        "district": "გლდანი",
                        "rooms": 3,
                        "floor": "1",
                        "area_m2": 72,
                        "id": "fb_input",
                        "source": "facebook",
                        "phone9": "555000111",
                    }
                ),
                json.dumps([{"id": "myhome_1", "url": "https://www.myhome.ge/ka/pr/1/"}]),
                "2026-11-08T10:00:00Z",
            ),
        )

        self.conn.execute(
            """
            INSERT INTO xray_results (id, chat_id, kind, target, candidates, owner_target_id, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                "R2",
                "777",
                "dup",
                json.dumps(
                    {
                        "district": "გლდანი",
                        "rooms": 3,
                        "id": "fb_input",
                        "source": "facebook",
                    }
                ),
                json.dumps(
                    [
                        {
                            "id": "ss_ge_9",
                            "source": "ss_ge",
                            "url": "https://home.ss.ge/9",
                            "district": "გლდანი",
                            "rooms": 2,
                            "area_m2": 50,
                            "author_id": "a1",
                        }
                    ]
                ),
                "2026-11-08T10:01:00Z",
            ),
        )

        self.conn.execute(
            """
            INSERT INTO xray_results (id, chat_id, kind, target, candidates, owner_target_id, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                "R3",
                "777",
                "review_random",
                json.dumps(
                    {
                        "id": "myhome_1",
                        "source": "myhome",
                        "district": "გლდანი",
                        "area_m2": 72,
                        "_review": {
                            "origin": "review_random",
                            "posterior": 0.2,
                            "q": 0.001,
                            "pool": 1000,
                            "week": "2026-W46",
                            "version": "w-2026-W46",
                        },
                    }
                ),
                json.dumps(
                    [
                        {
                            "id": "ss_ge_9",
                            "source": "ss_ge",
                            "url": "https://home.ss.ge/9",
                            "area_m2": 71,
                        }
                    ]
                ),
                "2026-11-08T10:02:00Z",
            ),
        )

        # xray_targets: T1
        self.conn.execute(
            """
            INSERT INTO xray_targets (id, chat_id, target, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "T1",
                "777",
                json.dumps(
                    {
                        "district": "გლდანი",
                        "rooms": 3,
                        "floor": "1",
                        "area_m2": 72,
                        "phone9": "555000111",
                    }
                ),
                "2026-11-08T09:59:00Z",
            ),
        )

        # xray_jobs: J1, J2
        self.conn.execute(
            """
            INSERT INTO xray_jobs (id, kind, phone9, target_id, chat_id, status, result, created_at, done_at)
            VALUES (?, ?, '', ?, ?, 'done', ?, ?, ?)
            """,
            (
                "J1",
                "owner",
                "T1",
                "777",
                json.dumps(
                    {
                        "text": "…",
                        "candidates": [{"id": "ss_ge_404", "url": "https://home.ss.ge/404"}],
                    }
                ),
                "2026-11-08T10:03:00Z",
                "2026-11-08T10:03:05Z",
            ),
        )

        self.conn.execute(
            """
            INSERT INTO xray_jobs (id, kind, phone9, target_id, chat_id, status, result, created_at, done_at)
            VALUES (?, ?, '', ?, ?, 'done', ?, ?, ?)
            """,
            (
                "J2",
                "photo",
                "777:m5",
                "777",
                json.dumps(
                    {
                        "candidates": [
                            {
                                "id": "myhome_1",
                                "url": "https://www.myhome.ge/ka/pr/1/",
                                "district": "გლდანი",
                                "photo_matches": 2,
                            }
                        ]
                    }
                ),
                "2026-11-08T10:04:00Z",
                "2026-11-08T10:04:05Z",
            ),
        )

        # xray_photo_batches
        self.conn.execute(
            """
            INSERT INTO xray_photo_batches (batch_id, chat_id, caption, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "777:m5",
                "777",
                "იყიდება 3-ოთახიანი ბინა გლდანში, მე-6 მიკრორაიონი, 72 მ², 1 სართული, $60,000. დარეკეთ: 555000111",
                "2026-11-08T10:04:00Z",
            ),
        )

        # xray_feedback
        feedback_rows = [
            ("R1", "myhome_1", "777", None, 1, "2026-11-08T11:01:00Z"),
            ("R2", "ss_ge_9", "777", None, 0, "2026-11-08T11:02:00Z"),
            ("R3", "ss_ge_9", "777", None, -1, "2026-11-08T11:03:00Z"),
            ("J1", "ss_ge_404", "777", "T1", 0, "2026-11-08T11:04:00Z"),
            ("J2", "myhome_1", "777", "777:m5", 1, "2026-11-08T11:05:00Z"),
            ("R1", "myhome_1", "999", None, 0, "2026-11-08T11:06:00Z"),
            ("gone", "x", "777", None, 1, "2026-11-08T11:07:00Z"),
        ]
        for row in feedback_rows:
            self.conn.execute(
                """
                INSERT INTO xray_feedback (job_id, candidate_id, chat_id, target_id, label, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_admin_only_and_orphans_counted(self):
        records, skipped = build_golden_pairs(self.conn, "777")
        self.assertEqual(len(records), 5)
        self.assertEqual(skipped, 1)
        r1 = next(r for r in records if r["pair_id"] == "R1__myhome_1")
        self.assertEqual(r1["labelled_at"], "2026-11-08T11:01:00Z")

    def test_snapshot_statuses(self):
        records, _ = build_golden_pairs(self.conn, "777")
        r1 = next(r for r in records if r["pair_id"] == "R1__myhome_1")
        r2 = next(r for r in records if r["pair_id"] == "R2__ss_ge_9")
        j1 = next(r for r in records if r["pair_id"] == "J1__ss_ge_404")

        self.assertEqual(r1["snapshot"], "backfilled")
        self.assertEqual(r1["candidate"]["street"], "გლდანი - VI მ/რ")
        self.assertEqual(r2["snapshot"], "at_label")
        self.assertEqual(j1["snapshot"], "missing")

    def test_no_pii(self):
        records, _ = build_golden_pairs(self.conn, "777")
        for r in records:
            for k in ("phone9", "phone_number", "author_id"):
                self.assertNotIn(k, r["target"])
                self.assertNotIn(k, r["candidate"])
        jsonl = to_jsonl(records)
        self.assertNotIn("555000111", jsonl)
        r2 = next(r for r in records if r["pair_id"] == "R2__ss_ge_9")
        self.assertNotIn("a1", r2["candidate"].values())

    def test_relations(self):
        records, _ = build_golden_pairs(self.conn, "777")
        r1 = next(r for r in records if r["pair_id"] == "R1__myhome_1")
        j1 = next(r for r in records if r["pair_id"] == "J1__ss_ge_404")
        r2 = next(r for r in records if r["pair_id"] == "R2__ss_ge_9")

        self.assertEqual(r1["relations"]["same_phone"], "agree")
        self.assertEqual(j1["relations"]["same_phone"], "unknown")
        self.assertEqual(r2["relations"]["same_author"], "unknown")

    def test_review_meta(self):
        records, _ = build_golden_pairs(self.conn, "777")
        r3 = next(r for r in records if r["pair_id"] == "R3__ss_ge_9")
        self.assertEqual(r3["label_origin"], "review_random")
        self.assertEqual(r3["reply_kind"], "review")
        self.assertEqual(r3["review"]["q"], 0.001)
        self.assertNotIn("_review", r3["target"])
        self.assertEqual(r3["target_kind"], "listing")
        self.assertEqual(r3["listing_pair_key"], "myhome_1__ss_ge_9")

    def test_fb_target(self):
        records, _ = build_golden_pairs(self.conn, "777")
        r1 = next(r for r in records if r["pair_id"] == "R1__myhome_1")
        self.assertEqual(r1["target_kind"], "post")
        self.assertIsNone(r1["listing_pair_key"])

    def test_photo(self):
        records, _ = build_golden_pairs(self.conn, "777")
        j2 = next(r for r in records if r["pair_id"] == "J2__myhome_1")
        self.assertEqual(j2["reply_kind"], "photo")
        self.assertEqual(j2["evidence"], {"photo_matches": 2})
        self.assertNotIn("photo_matches", j2["candidate"])
        self.assertNotIn("photo_batch", j2["target"])
        self.assertEqual(j2["target"]["district"], "გლდანი")
        self.assertNotIn("777:", to_jsonl(records))

    def test_owner(self):
        records, _ = build_golden_pairs(self.conn, "777")
        j1 = next(r for r in records if r["pair_id"] == "J1__ss_ge_404")
        self.assertEqual(j1["target"]["district"], "გლდანი")

    def test_determinism(self):
        records1, _ = build_golden_pairs(self.conn, "777")
        records2, _ = build_golden_pairs(self.conn, "777")
        self.assertEqual(to_jsonl(records1), to_jsonl(records2))
        self.assertEqual([r["pair_id"] for r in records1], sorted(r["pair_id"] for r in records1))
        for line in to_jsonl(records1).splitlines(keepends=True):
            self.assertTrue(line.endswith("\n"))

    def test_unsure_kept(self):
        records, _ = build_golden_pairs(self.conn, "777")
        s = summarize(records, 1)
        self.assertEqual(s["label"], {"-1": 1, "0": 2, "1": 2})

    def test_phone_dup_candidate_uses_its_own_ref_as_target(self):
        self.conn.execute(
            """
            INSERT INTO xray_results (id, chat_id, kind, target, candidates, owner_target_id, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                "RP",
                "777",
                "phone_dup",
                "{}",
                json.dumps(
                    [
                        {
                            "id": "myhome_1",
                            "source": "myhome",
                            "phone9": "555000111",
                            "district": "გლდანი",
                            "area_m2": 72,
                            "_ref": {
                                "id": "ss_ge_main",
                                "source": "ss_ge",
                                "phone9": "555000111",
                                "district": "გლდანი",
                                "area_m2": 72,
                            },
                        }
                    ]
                ),
                "2026-11-11T10:00:00Z",
            ),
        )
        self.conn.execute(
            """
            INSERT INTO xray_feedback (job_id, candidate_id, chat_id, target_id, label, created_at)
            VALUES (?, ?, ?, NULL, ?, ?)
            """,
            ("RP", "myhome_1", "777", 1, "2026-11-11T10:05:00Z"),
        )
        self.conn.commit()

        records, _ = build_golden_pairs(self.conn, "777")
        rec = next(r for r in records if r["pair_id"] == "RP__myhome_1")
        self.assertEqual(rec["target"]["id"], "ss_ge_main")
        self.assertEqual(rec["target_kind"], "listing")
        self.assertEqual(rec["listing_pair_key"], "myhome_1__ss_ge_main")
        self.assertNotIn("_ref", rec["candidate"])
        self.assertNotIn("phone9", rec["target"])
        self.assertEqual(rec["relations"]["same_phone"], "agree")
        self.assertEqual(rec["reply_kind"], "phone_dup")
        self.assertEqual(rec["snapshot"], "at_label")

    def _add_result(self, rid, target, candidates, created_at):
        self.conn.execute(
            """
            INSERT INTO xray_results (id, chat_id, kind, target, candidates, owner_target_id, created_at)
            VALUES (?, '777', 'dup', ?, ?, NULL, ?)
            """,
            (rid, json.dumps(target), json.dumps(candidates), created_at),
        )

    def _add_answer(self, rid, candidate_id, label, created_at):
        self.conn.execute(
            """
            INSERT INTO xray_feedback (job_id, candidate_id, chat_id, target_id, label, created_at)
            VALUES (?, ?, '777', NULL, ?, ?)
            """,
            (rid, candidate_id, label, created_at),
        )

    def test_same_listing_pair_in_two_replies_keeps_only_the_latest_answer(self):
        t = {"id": "myhome_7", "source": "myhome", "district": "საბურთალო", "area_m2": 45}
        c = {"id": "myhome_8", "source": "myhome", "district": "საბურთალო", "area_m2": 45}
        self._add_result("RA", t, [c], "2026-11-11T10:00:00Z")
        self._add_result("RB", t, [c], "2026-11-11T11:00:00Z")
        self._add_answer("RA", "myhome_8", 1, "2026-11-11T10:05:00Z")
        self._add_answer("RB", "myhome_8", 0, "2026-11-11T11:05:00Z")
        self.conn.commit()
        records, _ = build_golden_pairs(self.conn, "777")
        same = [r for r in records if r["listing_pair_key"] == "myhome_7__myhome_8"]
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]["pair_id"], "RB__myhome_8")
        self.assertEqual(same[0]["label"], 0)

    def test_reversed_listing_pair_is_the_same_record(self):
        a = {"id": "myhome_7", "source": "myhome", "district": "საბურთალო", "area_m2": 45}
        b = {"id": "myhome_8", "source": "myhome", "district": "საბურთალო", "area_m2": 45}
        self._add_result("RA", a, [b], "2026-11-11T10:00:00Z")
        self._add_result("RB", b, [a], "2026-11-11T11:00:00Z")
        self._add_answer("RA", "myhome_8", 1, "2026-11-11T10:05:00Z")
        self._add_answer("RB", "myhome_7", 1, "2026-11-11T11:05:00Z")
        self.conn.commit()
        records, _ = build_golden_pairs(self.conn, "777")
        same = [r for r in records if r["listing_pair_key"] == "myhome_7__myhome_8"]
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]["pair_id"], "RB__myhome_7")

    def test_same_post_twice_is_one_record_even_at_a_new_price(self):
        post = {
            "id": "fb_input",
            "source": "facebook",
            "district": "თემქა",
            "rooms": 3,
            "floor": "1",
            "area_m2": 44,
            "price_usd": 65000,
            "phone9": "510102767",
        }
        repost = {**post, "price_usd": 62000, "area_m2": 44.0, "rooms": "3"}
        c = {"id": "myhome_26465354", "source": "myhome", "district": "თემქა", "area_m2": 44}
        self._add_result("RA", post, [c], "2026-11-11T10:00:00Z")
        self._add_result("RB", repost, [c], "2026-11-11T11:00:00Z")
        self._add_answer("RA", "myhome_26465354", 1, "2026-11-11T10:05:00Z")
        self._add_answer("RB", "myhome_26465354", 1, "2026-11-11T11:05:00Z")
        self.conn.commit()
        records, _ = build_golden_pairs(self.conn, "777")
        same = [r for r in records if r["candidate"].get("id") == "myhome_26465354"]
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]["pair_id"], "RB__myhome_26465354")
        self.assertTrue(same[0]["post_key"])
        self.assertNotIn("510102767", to_jsonl(records))

    def test_different_posts_about_one_candidate_stay_two_records(self):
        post = {
            "id": "fb_input",
            "source": "facebook",
            "district": "თემქა",
            "rooms": 3,
            "floor": "1",
            "area_m2": 44,
        }
        other = {**post, "area_m2": 60}
        c = {"id": "myhome_26465354", "source": "myhome", "district": "თემქა", "area_m2": 44}
        self._add_result("RA", post, [c], "2026-11-11T10:00:00Z")
        self._add_result("RB", other, [c], "2026-11-11T11:00:00Z")
        self._add_answer("RA", "myhome_26465354", 1, "2026-11-11T10:05:00Z")
        self._add_answer("RB", "myhome_26465354", 0, "2026-11-11T11:05:00Z")
        self.conn.commit()
        records, _ = build_golden_pairs(self.conn, "777")
        same = [r for r in records if r["candidate"].get("id") == "myhome_26465354"]
        self.assertEqual(len(same), 2)
        self.assertNotEqual(same[0]["post_key"], same[1]["post_key"])


if __name__ == "__main__":
    unittest.main()
