import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from core.database import XRAY_TABLES_DDL
import core.xray_weights as xw
from scripts.estimate_xray_weights import main, run


class TestXrayWeights(unittest.TestCase):
    def test_01_levels(self):
        # floor_level
        self.assertEqual(xw.floor_level({"floor": "5"}, {"floor": "5/9"}), "equal")
        self.assertEqual(xw.floor_level({"floor": "5"}, {"floor": "6"}), "off_by_one")
        self.assertEqual(xw.floor_level({"floor": "5"}, {"floor": "8"}), "further")
        self.assertEqual(xw.floor_level({"floor": ""}, {"floor": "5"}), "unknown")

        # area_level: 54.5 vs 55.4 both round half up to 55
        self.assertEqual(xw.area_level({"area_m2": 54.5}, {"area_m2": 55.4}), "equal")
        self.assertEqual(xw.area_level({"area_m2": 72}, {"area_m2": 70}), "close")
        self.assertEqual(xw.area_level({"area_m2": 72}, {"area_m2": 50}), "far")
        self.assertEqual(xw.area_level({"area_m2": 0}, {"area_m2": 50}), "unknown")

        # _both_ways with stub
        stub_agree = MagicMock(side_effect=["agree", "unknown"])
        self.assertEqual(xw._both_ways(stub_agree, {}, {}), "agree")
        stub_conflict = MagicMock(side_effect=["conflict", "agree"])
        self.assertEqual(xw._both_ways(stub_conflict, {}, {}), "conflict")

    def test_02_blocks(self):
        # Four listings in one block plus one with empty district and one with area 0
        listings = [
            {"id": "1", "district": "Saburtalo", "deal_type": "sale", "area_m2": 50, "floor": "1"},
            {"id": "2", "district": "Saburtalo", "deal_type": "sale", "area_m2": 50, "floor": "1"},
            {"id": "3", "district": "Saburtalo", "deal_type": "sale", "area_m2": 50, "floor": "2"},
            {"id": "4", "district": "Saburtalo", "deal_type": "sale", "area_m2": 50, "floor": "5"},
            {"id": "5", "district": "", "deal_type": "sale", "area_m2": 50, "floor": "1"},
            {"id": "6", "district": "Saburtalo", "deal_type": "sale", "area_m2": 0, "floor": "1"},
        ]
        counts = xw.count_pairs(listings)
        self.assertEqual(counts["pairs"], 6)

        # Hand-counted floor u_counts for 6 pairs:
        # (1,2) -> equal
        # (1,3) -> off_by_one
        # (1,4) -> further
        # (2,3) -> off_by_one
        # (2,4) -> further
        # (3,4) -> further
        expected_floor = {"equal": 1, "off_by_one": 2, "further": 3}
        self.assertEqual(counts["u_counts"]["floor"], expected_floor)

    def test_03_group_weighting(self):
        # A group of 3 and a group of 2
        listings = [
            {"id": "a", "group_id": "g1", "floor": "1", "area_m2": 50},
            {"id": "b", "group_id": "g1", "floor": "2", "area_m2": 50},
            {"id": "c", "group_id": "g1", "floor": "3", "area_m2": 50},
            {"id": "d", "group_id": "g2", "floor": "4", "area_m2": 60},
            {"id": "e", "group_id": "g2", "floor": "5", "area_m2": 60},
        ]
        pairs = xw.group_pairs(listings)
        weights = [round(w, 4) for _, w in pairs]
        self.assertEqual(weights, [round(1 / 3, 4), round(1 / 3, 4), round(1 / 3, 4), 1.0])

        # Total per group is 1.0
        g1_total = sum(w for (_, w) in pairs[:3])
        g2_total = sum(w for (_, w) in pairs[3:])
        self.assertAlmostEqual(g1_total, 1.0)
        self.assertAlmostEqual(g2_total, 1.0)

        # Every pattern's area level is "unknown"
        for pattern, _ in pairs:
            self.assertEqual(pattern[1], "unknown")

    def test_04_em_recovery(self):
        true_m = {
            "floor": {"equal": 0.85, "off_by_one": 0.10, "further": 0.05},
            "layout": {"agree": 0.95, "conflict": 0.05},
            "total_floors": {"agree": 0.85, "off_by_one": 0.10, "conflict": 0.05},
            "location": {"agree": 0.9, "conflict": 0.1},
        }
        true_u = {
            "floor": {"equal": 0.10, "off_by_one": 0.20, "further": 0.70},
            "layout": {"agree": 0.5, "conflict": 0.5},
            "total_floors": {"agree": 0.2, "off_by_one": 0.1, "conflict": 0.7},
            "location": {"agree": 0.4, "conflict": 0.6},
        }
        true_lam = 0.02
        N = 200_000

        import itertools

        active_fields = ("floor", "layout", "total_floors", "location")
        all_combos = list(itertools.product(*(xw.LEVELS[f] for f in active_fields)))
        pool = {}
        for fl, lay, tf, loc in all_combos:
            pat = (fl, xw.UNKNOWN, lay, tf, loc)
            pm = (
                true_m["floor"][fl]
                * true_m["layout"][lay]
                * true_m["total_floors"][tf]
                * true_m["location"][loc]
            )
            pu = (
                true_u["floor"][fl]
                * true_u["layout"][lay]
                * true_u["total_floors"][tf]
                * true_u["location"][loc]
            )
            n_p = round(N * (true_lam * pm + (1 - true_lam) * pu))
            pool[pat] = n_p

        m0 = {f: {lvl: 1.0 / len(xw.LEVELS[f]) for lvl in xw.LEVELS[f]} for f in xw.FIELDS}
        u = {
            f: dict(true_u.get(f, {lvl: 1.0 / len(xw.LEVELS[f]) for lvl in xw.LEVELS[f]}))
            for f in xw.FIELDS
        }

        res = xw.em_session(pool, "area", m0, u, 0.1)

        self.assertLess(res["iterations"], xw.EM_MAX_ITERATIONS)
        self.assertAlmostEqual(res["lambda"], true_lam, delta=0.005)
        for f in active_fields:
            for lvl in xw.LEVELS[f]:
                self.assertAlmostEqual(res["m"][f][lvl], true_m[f][lvl], delta=0.02)

    def _make_synthetic_fixture(self):
        listings = []
        for i in range(40):
            district = "Vake" if i < 20 else "Saburtalo"
            deal = "sale" if i % 2 == 0 else "rent"
            fl = str((i % 9) + 1)
            tf = 9 if i < 20 else 16
            area = 45.0 + (i % 10) * 5.0
            gid = f"g{i % 4}" if i % 3 == 0 else ""
            listings.append(
                {
                    "id": f"prop_{i:02d}",
                    "source": "myhome" if i % 2 == 0 else "ss_ge",
                    "url": f"https://example.com/{i}",
                    "district": district,
                    "subdistrict": "",
                    "street": f"Street {i % 5}",
                    "floor": fl,
                    "total_floors": tf,
                    "rooms": 2 + (i % 3),
                    "bedrooms": 1 + (i % 2),
                    "area_m2": area,
                    "price_usd": 50000 + i * 1000,
                    "deal_type": deal,
                    "group_id": gid,
                }
            )
        return listings

    def test_05_determinism(self):
        fixture = self._make_synthetic_fixture()
        counts1 = xw.count_pairs(fixture)
        groups1 = xw.group_pairs(fixture)
        w1, _ = xw.estimate(counts1, groups1)

        counts2 = xw.count_pairs(fixture)
        groups2 = xw.group_pairs(fixture)
        w2, _ = xw.estimate(counts2, groups2)

        s1 = json.dumps(w1, sort_keys=True)
        s2 = json.dumps(w2, sort_keys=True)
        self.assertEqual(s1, s2)

    def test_06_sign_test(self):
        self.assertEqual(xw.sign_test_p(5, 5), 1 / 32)
        self.assertLess(xw.sign_test_p(5, 5), 0.05)

        self.assertEqual(xw.sign_test_p(4, 4), 1 / 16)
        self.assertFalse(xw.sign_test_p(4, 4) < 0.05)

        self.assertEqual(xw.sign_test_p(0, 0), 1.0)

    def test_07_gate(self):
        weights = {"lambda": 0.02, "fields": {}}

        # Base 5 winning records
        def make_record(pid, label, target_kind="listing", snapshot="at_label"):
            return {
                "pair_id": pid,
                "target_key": pid,
                "label": label,
                "label_origin": "organic",
                "reply_kind": "dup",
                "target_kind": target_kind,
                "listing_pair_key": f"{pid}__cand",
                "shown_at": "2026-11-09T00:00:00",
                "labelled_at": "2026-11-09T00:00:00",
                "snapshot": snapshot,
                "target": {"pair_id": pid, "district": "Vake"},
                "candidate": {"pair_id": pid, "id": f"cand_{pid}", "district": "Vake"},
                "relations": {},
                "evidence": {},
                "review": None,
            }

        records_5 = [make_record(f"p{i}", 1) for i in range(1, 6)]
        vectors_pass = [{"name": "v1", "gate": True, "args": [{}, {}], "expected": False}]

        champ_lookup = {f"p{i}": False for i in range(1, 6)}
        chall_lookup = {f"p{i}": True for i in range(1, 6)}

        orig_champ = xw._candidate_matches_target
        orig_chall = xw.challenger_says_same

        try:

            def mock_champ(t, c):
                pid = t.get("pair_id") or c.get("pair_id")
                return champ_lookup.get(pid, False)

            def mock_chall(w, *args):
                if len(args) == 2:
                    t, c = args
                    pid = t.get("pair_id") or c.get("pair_id")
                    if pid in chall_lookup:
                        return chall_lookup[pid]
                return False

            xw._candidate_matches_target = mock_champ
            xw.challenger_says_same = mock_chall

            # (a) 5 wins under 5 different target_keys -> p_value == 1/32, passes gate
            res_a = xw.gate(weights, records_5, vectors_pass, {"ok": True})
            self.assertTrue(res_a["passed"])
            self.assertEqual(res_a["all"]["wins"], 5)
            self.assertEqual(res_a["all"]["losses"], 0)
            self.assertEqual(res_a["all"]["replies"], 5)
            self.assertEqual(res_a["all"]["replies_won"], 5)
            self.assertEqual(res_a["all"]["replies_lost"], 0)
            self.assertEqual(res_a["all"]["replies_tied"], 0)
            self.assertEqual(res_a["all"]["p_value"], 1 / 32)

            # (b) One extra challenger false match -> fails B
            # Add record with label 0, challenger True, champion False
            rec_fp = make_record("p_fp", 0)
            chall_lookup["p_fp"] = True
            champ_lookup["p_fp"] = False
            res_b = xw.gate(weights, records_5 + [rec_fp], vectors_pass, {"ok": True})
            self.assertFalse(res_b["passed"])
            self.assertGreater(res_b["all"]["fp_challenger"], res_b["all"]["fp_champion"])

            # (c) Post stratum with 1 win and 2 losses -> fails C
            # Make 3 post records
            chall_lookup.pop("p_fp", None)
            post_recs = [
                make_record("post1", 1, target_kind="post"),
                make_record("post2", 1, target_kind="post"),
                make_record("post3", 1, target_kind="post"),
            ]
            # post1: win (chall True, champ False)
            champ_lookup["post1"] = False
            chall_lookup["post1"] = True
            # post2: loss (chall False, champ True)
            champ_lookup["post2"] = True
            chall_lookup["post2"] = False
            # post3: loss (chall False, champ True)
            champ_lookup["post3"] = True
            chall_lookup["post3"] = False

            res_c = xw.gate(weights, records_5 + post_recs, vectors_pass, {"ok": True})
            self.assertFalse(res_c["passed"])
            self.assertLess(res_c["post"]["wins"], res_c["post"]["losses"])
            self.assertLess(res_c["post"]["replies_won"], res_c["post"]["replies_lost"])

            # (d) One gate vector failing -> not passed, and name in failed
            vectors_fail = [
                {"name": "vector_failing_name", "gate": True, "args": [{}, {}], "expected": True}
            ]
            res_d = xw.gate(weights, records_5, vectors_fail, {"ok": True})
            self.assertFalse(res_d["passed"])
            self.assertIn("vector_failing_name", res_d["vectors"]["failed"])

            # (e) -1 and missing records excluded and counted
            rec_unsure = make_record("p_u", -1)
            rec_missing = make_record("p_m", 1, snapshot="missing")
            res_e = xw.gate(
                weights, records_5 + [rec_unsure, rec_missing], vectors_pass, {"ok": True}
            )
            self.assertEqual(res_e["exam"]["unsure"], 1)
            self.assertEqual(res_e["exam"]["missing"], 1)
            self.assertEqual(res_e["exam"]["pairs"], 5)
        finally:
            xw._candidate_matches_target = orig_champ
            xw.challenger_says_same = orig_chall

    def test_08_vectors(self):
        fixture = self._make_synthetic_fixture()
        counts = xw.count_pairs(fixture)
        groups = xw.group_pairs(fixture)
        weights, _ = xw.estimate(counts, groups)

        vec_path = Path(__file__).resolve().parent / "fixtures" / "xray_rule_vectors.json"
        with open(vec_path, "r", encoding="utf-8") as f:
            vectors = json.load(f)

        res = xw.gate(weights, [], vectors, {"ok": True})
        self.assertEqual(res["vectors"]["checked"], 5)

    def test_09_script(self):
        import tempfile

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Set up schema
        conn.execute("""
            CREATE TABLE properties (
                id TEXT PRIMARY KEY,
                source TEXT,
                url TEXT,
                district TEXT,
                subdistrict TEXT,
                street TEXT,
                floor TEXT,
                total_floors INTEGER,
                rooms INTEGER,
                bedrooms INTEGER,
                area_m2 REAL,
                price_usd REAL,
                deal_type TEXT,
                phone_number TEXT,
                group_id TEXT
            )
        """)
        for ddl in XRAY_TABLES_DDL:
            conn.execute(ddl)

        # Insert sample property
        conn.execute("""
            INSERT INTO properties (id, source, url, district, area_m2, floor, deal_type, price_usd, phone_number)
            VALUES ('ss_ge_1', 'ss_ge', 'https://ss.ge/1', 'Vake', 55, '3', 'sale', 80000, '555000111')
        """)
        conn.commit()

        class FakeSettings:
            TELEGRAM_CHAT_ID = "777"
            TELEGRAM_BOT_TOKEN = "fake_token"
            CLOUDFLARE_PROXY_URL = "http://fake"
            CLOUDFLARE_SYNC_KEY = "fake_key"

        now = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
        fake_send = MagicMock(return_value={"ok": True})

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "out"

            # dry_run=False
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                run(conn, FakeSettings(), now, fake_send, out_dir=str(out_dir), dry_run=False)
            output = buf.getvalue()

            # Row inserted into xray_weights
            row = conn.execute("SELECT * FROM xray_weights WHERE id LIKE 'w-2026-W46-%'").fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(row["id"].startswith("w-2026-W46-"))

            # Fake send called once with HTML parse mode and 📊 in summary_text
            self.assertEqual(fake_send.call_count, 1)
            call_args = fake_send.call_args[0]
            self.assertEqual(call_args[0], "sendMessage")
            payload = call_args[1]
            self.assertEqual(payload["parse_mode"], "HTML")
            self.assertIn("📊", payload["text"])

            # dry_run=True in another directory
            dry_out = Path(tmp_dir) / "dry_out"
            fake_send.reset_mock()
            conn.execute("DELETE FROM xray_weights")
            conn.commit()

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                run(conn, FakeSettings(), now, fake_send, out_dir=str(dry_out), dry_run=True)
            output_dry = buf.getvalue()

            # No row inserted
            row_dry = conn.execute("SELECT * FROM xray_weights WHERE id = 'w-2026-W46'").fetchone()
            self.assertIsNone(row_dry)
            # No send call
            fake_send.assert_not_called()

            # 4 files exist
            self.assertTrue((dry_out / "golden_pairs.jsonl").exists())
            self.assertTrue((dry_out / "weights.json").exists())
            self.assertTrue((dry_out / "counts.json").exists())
            self.assertTrue((dry_out / "report.json").exists())

            # Stdout has no PII (phone number or chat_id)
            self.assertNotIn("555000111", output)
            self.assertNotIn("777", output)
            self.assertNotIn("555000111", output_dry)
            self.assertNotIn("777", output_dry)

    def test_10_parity(self):
        # tests/test_xray_parity.py still passes with the gate keys
        import tests.test_xray_parity as txp

        suite = unittest.TestLoader().loadTestsFromModule(txp)
        runner = unittest.TextTestRunner(stream=io.StringIO())
        result = runner.run(suite)
        self.assertTrue(result.wasSuccessful())

    def test_11_wilson95(self):
        lo, hi = xw.wilson95(98, 100)
        self.assertTrue(lo < 0.98 < hi <= 1.0)
        self.assertEqual(xw.wilson95(0, 0), (0.0, 1.0))

    def test_12_leave_one_out_recovers_independent_data(self):
        import itertools

        n = 2_000_000
        lam = 0.01

        true_m = {
            "floor": {"equal": 0.97, "off_by_one": 0.02, "further": 0.01},
            "area": {"equal": 0.8, "close": 0.19, "far": 0.01},
            "layout": {"agree": 0.98, "conflict": 0.02},
            "total_floors": {"agree": 0.90, "off_by_one": 0.05, "conflict": 0.05},
            "location": {"agree": 0.95, "conflict": 0.05},
        }
        true_u = {
            "floor": {"equal": 0.1, "off_by_one": 0.15, "further": 0.75},
            "area": {"equal": 0.05, "close": 0.35, "far": 0.6},
            "layout": {"agree": 0.5, "conflict": 0.5},
            "total_floors": {"agree": 0.1, "off_by_one": 0.1, "conflict": 0.8},
            "location": {"agree": 0.9, "conflict": 0.1},
        }

        all_combos = list(itertools.product(*(xw.LEVELS[f] for f in xw.FIELDS)))
        patterns = {}
        u_counts = {f: {lvl: 0 for lvl in xw.LEVELS[f]} for f in xw.FIELDS}
        total_pairs = 0

        for p in all_combos:
            fl, ar, lay, tf, loc = p
            pm = (
                true_m["floor"][fl]
                * true_m["area"][ar]
                * true_m["layout"][lay]
                * true_m["total_floors"][tf]
                * true_m["location"][loc]
            )
            pu = (
                true_u["floor"][fl]
                * true_u["area"][ar]
                * true_u["layout"][lay]
                * true_u["total_floors"][tf]
                * true_u["location"][loc]
            )
            n_p = round(n * (lam * pm + (1 - lam) * pu))
            patterns[p] = n_p
            total_pairs += n_p
            for f, lvl in zip(xw.FIELDS, p):
                u_counts[f][lvl] += n_p

        close_area = {p: count for p, count in patterns.items() if p[1] in ("equal", "close")}
        counts = {
            "pairs": total_pairs,
            "u_counts": u_counts,
            "patterns": patterns,
            "pools": {"close_area": close_area},
        }

        groups = (
            [(("equal", xw.UNKNOWN, "agree", "agree", "agree"), 1.0)] * 98
            + [(("off_by_one", xw.UNKNOWN, "conflict", "conflict", "conflict"), 1.0)]
            + [(("further", xw.UNKNOWN, "conflict", "conflict", "conflict"), 1.0)]
        )

        w, rep = xw.estimate(counts, groups)
        self.assertTrue(rep["photo_floor"]["ok"])
        self.assertAlmostEqual(w["lambda"] * n, 20000, delta=2000)  # within 10%
        for f in xw.STRONG:
            top_lvl = xw.TOP[f]
            self.assertAlmostEqual(w["fields"][f][top_lvl]["m"], true_m[f][top_lvl], delta=0.03)

    def test_13_look_alike_flats_fail_photo_check(self):
        import itertools

        n = 2_000_000
        lam = 0.01

        true_m = {
            "floor": {"equal": 0.97, "off_by_one": 0.02, "further": 0.01},
            "area": {"equal": 0.8, "close": 0.19, "far": 0.01},
            "layout": {"agree": 0.98, "conflict": 0.02},
            "total_floors": {"agree": 0.90, "off_by_one": 0.05, "conflict": 0.05},
            "location": {"agree": 0.95, "conflict": 0.05},
        }
        true_u = {
            "floor": {"equal": 0.1, "off_by_one": 0.15, "further": 0.75},
            "area": {"equal": 0.05, "close": 0.35, "far": 0.6},
            "layout": {"agree": 0.5, "conflict": 0.5},
            "total_floors": {"agree": 0.1, "off_by_one": 0.1, "conflict": 0.8},
            "location": {"agree": 0.9, "conflict": 0.1},
        }

        lookalike_p = {
            "floor": {"equal": 0.1, "off_by_one": 0.15, "further": 0.75},
            "area": {"equal": 0.15, "close": 0.85, "far": 0.0},
            "layout": {"agree": 1.0, "conflict": 0.0},
            "total_floors": {"agree": 0.9, "off_by_one": 0.1, "conflict": 0.0},
            "location": {"agree": 0.95, "conflict": 0.05},
        }

        all_combos = list(itertools.product(*(xw.LEVELS[f] for f in xw.FIELDS)))
        patterns = {}
        u_counts = {f: {lvl: 0 for lvl in xw.LEVELS[f]} for f in xw.FIELDS}
        total_pairs = 0

        for p in all_combos:
            fl, ar, lay, tf, loc = p
            pm = (
                true_m["floor"][fl]
                * true_m["area"][ar]
                * true_m["layout"][lay]
                * true_m["total_floors"][tf]
                * true_m["location"][loc]
            )
            pu_random = (
                true_u["floor"][fl]
                * true_u["area"][ar]
                * true_u["layout"][lay]
                * true_u["total_floors"][tf]
                * true_u["location"][loc]
            )
            pu_lookalike = (
                lookalike_p["floor"][fl]
                * lookalike_p["area"][ar]
                * lookalike_p["layout"][lay]
                * lookalike_p["total_floors"][tf]
                * lookalike_p["location"][loc]
            )
            pu = 0.70 * pu_random + 0.30 * pu_lookalike
            n_p = round(n * (lam * pm + (1 - lam) * pu))
            patterns[p] = n_p
            total_pairs += n_p
            for f, lvl in zip(xw.FIELDS, p):
                u_counts[f][lvl] += n_p

        close_area = {p: count for p, count in patterns.items() if p[1] in ("equal", "close")}
        counts = {
            "pairs": total_pairs,
            "u_counts": u_counts,
            "patterns": patterns,
            "pools": {"close_area": close_area},
        }

        groups = (
            [(("equal", xw.UNKNOWN, "agree", "agree", "agree"), 1.0)] * 98
            + [(("off_by_one", xw.UNKNOWN, "conflict", "conflict", "conflict"), 1.0)]
            + [(("further", xw.UNKNOWN, "conflict", "conflict", "conflict"), 1.0)]
        )

        w, rep = xw.estimate(counts, groups)
        self.assertFalse(rep["photo_floor"]["ok"])

        gate_res = xw.gate(w, [], [], rep["photo_floor"])
        self.assertFalse(gate_res["passed"])
        self.assertFalse(gate_res["photo_floor"]["ok"])

    def test_14_reply_sign_and_gate(self):
        # 1. Direct _reply_sign test: tie with 2 challenger-right, 2 champion-right
        rec1 = {"target_key": "rep1"}
        rec2 = {"target_key": "rep1"}
        rec3 = {"target_key": "rep1"}
        rec4 = {"target_key": "rep1"}
        disagree_items = [
            (rec1, False, True, True),  # challenger right
            (rec2, False, True, True),  # challenger right
            (rec3, True, False, True),  # champion right
            (rec4, True, False, True),  # champion right
        ]
        won, lost, tied = xw._reply_sign(disagree_items)
        self.assertEqual(won, 0)
        self.assertEqual(lost, 0)
        self.assertEqual(tied, 1)

        # 2. Gate with 5 disagreement wins under ONE target_key:
        # gives replies_won == 1, p_value == 0.5, gate not passed (A)
        weights = {"lambda": 0.01, "fields": {}}

        def make_rec(pid, label=1, target_key="single_target"):
            return {
                "pair_id": pid,
                "target_key": target_key,
                "label": label,
                "label_origin": "organic",
                "reply_kind": "dup",
                "target_kind": "listing",
                "listing_pair_key": f"{pid}__cand",
                "shown_at": "2026-11-09T00:00:00",
                "labelled_at": "2026-11-09T00:00:00",
                "snapshot": "at_label",
                "target": {"pair_id": pid, "district": "Vake"},
                "candidate": {"pair_id": pid, "id": f"cand_{pid}", "district": "Vake"},
                "relations": {},
                "evidence": {},
                "review": None,
            }

        recs_one_target = [make_rec(f"single_p{i}") for i in range(1, 6)]
        vectors_pass = [{"name": "v1", "gate": True, "args": [{}, {}], "expected": False}]

        orig_champ = xw._candidate_matches_target
        orig_chall = xw.challenger_says_same
        try:
            xw._candidate_matches_target = lambda t, c: False
            xw.challenger_says_same = lambda w, *args: True

            res = xw.gate(weights, recs_one_target, vectors_pass, {"ok": True})
            self.assertFalse(res["passed"])
            self.assertEqual(res["all"]["wins"], 5)
            self.assertEqual(res["all"]["losses"], 0)
            self.assertEqual(res["all"]["replies"], 1)
            self.assertEqual(res["all"]["replies_won"], 1)
            self.assertEqual(res["all"]["replies_lost"], 0)
            self.assertEqual(res["all"]["replies_tied"], 0)
            self.assertEqual(res["all"]["p_value"], 0.5)

            # 3. Tie under one target in gate gives replies_tied == 1, not counted in won or lost
            tie_recs = [
                make_rec("t1"),
                make_rec("t2"),
                make_rec("t3"),
                make_rec("t4"),
            ]
            champ_map = {"t1": False, "t2": False, "t3": True, "t4": True}
            chall_map = {"t1": True, "t2": True, "t3": False, "t4": False}
            xw._candidate_matches_target = lambda t, c: champ_map.get(t.get("pair_id"), False)
            xw.challenger_says_same = lambda w, *args: chall_map.get(
                args[0].get("pair_id") if args and isinstance(args[0], dict) else None, False
            )

            res_tie = xw.gate(weights, tie_recs, vectors_pass, {"ok": True})
            self.assertFalse(res_tie["passed"])
            self.assertEqual(res_tie["all"]["replies"], 1)
            self.assertEqual(res_tie["all"]["replies_won"], 0)
            self.assertEqual(res_tie["all"]["replies_lost"], 0)
            self.assertEqual(res_tie["all"]["replies_tied"], 1)
            self.assertEqual(res_tie["all"]["p_value"], 1.0)
        finally:
            xw._candidate_matches_target = orig_champ
            xw.challenger_says_same = orig_chall

    def test_15_review_sprint_options(self):
        # 1. Negative inputs return exit code 2
        self.assertEqual(main(["--review-uncertain", "-1"]), 2)
        self.assertEqual(main(["--review-random", "-1"]), 2)

        # 2. Setup in-memory sqlite with properties and XRAY_TABLES_DDL
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE properties (
                id TEXT PRIMARY KEY,
                source TEXT,
                url TEXT,
                district TEXT,
                subdistrict TEXT,
                street TEXT,
                floor TEXT,
                total_floors INTEGER,
                rooms INTEGER,
                bedrooms INTEGER,
                area_m2 REAL,
                price_usd REAL,
                deal_type TEXT,
                phone_number TEXT,
                group_id TEXT
            )
        """)
        for ddl in XRAY_TABLES_DDL:
            conn.execute(ddl)

        conn.execute("""
            INSERT INTO properties (id, source, url, district, area_m2, floor, deal_type, price_usd, phone_number)
            VALUES ('ss_ge_1', 'ss_ge', 'https://ss.ge/1', 'Vake', 55, '3', 'sale', 80000, '555000111')
        """)
        conn.commit()

        class FakeSettings:
            TELEGRAM_CHAT_ID = "777"
            TELEGRAM_BOT_TOKEN = "fake_token"
            CLOUDFLARE_PROXY_URL = "http://fake"
            CLOUDFLARE_SYNC_KEY = "fake_key"

        now = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
        fake_send = MagicMock(return_value={"ok": True})

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "out"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                run(
                    conn,
                    FakeSettings(),
                    now,
                    fake_send,
                    out_dir=str(out_dir),
                    dry_run=False,
                    review_uncertain=0,
                    review_random=0,
                )

            # Summary sent without "შესამოწმებელი"
            self.assertEqual(fake_send.call_count, 1)
            call_args = fake_send.call_args[0]
            payload = call_args[1]
            self.assertNotIn("შესამოწმებელი", payload["text"])

            # 0 rows in xray_results
            rows = conn.execute("SELECT * FROM xray_results").fetchall()
            self.assertEqual(len(rows), 0)

    def test_16_challenger_guard(self):
        # Weights where floor-equal and area-equal alone give posterior > 0.5
        weights = {
            "levels_version": "v1",
            "lambda": 0.01,
            "fields": {
                "floor": {"equal": {"bits": 4.0, "m": 0.5, "u": 0.03}},
                "area": {"equal": {"bits": 4.0, "m": 0.5, "u": 0.03}},
                "layout": {"agree": {"bits": 2.0, "m": 0.5, "u": 0.1}},
                "total_floors": {"agree": {"bits": 2.0, "m": 0.5, "u": 0.1}},
                "location": {"agree": {"bits": 2.0, "m": 0.5, "u": 0.1}},
            },
        }
        # Floor and area equal, but layout, total_floors and location are unknown.
        a = {"floor": "3", "area_m2": 60.0}
        b = {"floor": "3", "area_m2": 60.0}
        self.assertGreater(xw.posterior(weights, a, b), xw.POSSIBLE)
        self.assertFalse(xw.challenger_says_same(weights, a, b))

        # When layout agrees, challenger_says_same is True
        a_layout = {"floor": "3", "area_m2": 60.0, "rooms": 2, "bedrooms": 1}
        b_layout = {"floor": "3", "area_m2": 60.0, "rooms": 2, "bedrooms": 1}
        self.assertTrue(xw.challenger_says_same(weights, a_layout, b_layout))

    def test_17_gate_units(self):
        # 1. Two review records sharing a listing form one unit.
        r1 = {
            "pair_id": "p1",
            "listing_pair_key": "L1__L2",
            "target": {"id": "L1"},
            "candidate": {"id": "L2"},
        }
        r2 = {
            "pair_id": "p2",
            "listing_pair_key": "L2__L3",
            "target": {"id": "L2"},
            "candidate": {"id": "L3"},
        }
        units = xw._units([r1, r2])
        self.assertEqual(units["p1"], units["p2"])

        # 2. Two records with disjoint listings but one shared group_id form one unit.
        r3 = {
            "pair_id": "p3",
            "listing_pair_key": "L10__L11",
            "target": {"id": "L10", "group_id": "grpA"},
            "candidate": {"id": "L11"},
        }
        r4 = {
            "pair_id": "p4",
            "listing_pair_key": "L20__L21",
            "target": {"id": "L20"},
            "candidate": {"id": "L21", "group_id": "grpA"},
        }
        units_grp = xw._units([r3, r4])
        self.assertEqual(units_grp["p3"], units_grp["p4"])

        # 3. Two post records with different target_keys are two units.
        p1 = {
            "pair_id": "post_p1",
            "target_key": "fb_post_1",
            "target": {"url": "http://fb/1"},
            "candidate": {"id": "L30"},
        }
        p2 = {
            "pair_id": "post_p2",
            "target_key": "fb_post_2",
            "target": {"url": "http://fb/2"},
            "candidate": {"id": "L31"},
        }
        units_post = xw._units([p1, p2])
        self.assertNotEqual(units_post["post_p1"], units_post["post_p2"])

        # 4. Two units won and none lost give p_value == 0.25.
        self.assertEqual(xw.sign_test_p(2, 2), 0.25)

    def test_18_total_floors_level(self):
        # 1. 10/9 is off_by_one
        self.assertEqual(
            xw.total_floors_level({"total_floors": 10}, {"total_floors": 9}), "off_by_one"
        )
        # 2. 10/10 is agree
        self.assertEqual(xw.total_floors_level({"total_floors": 10}, {"total_floors": 10}), "agree")
        # 3. 10/12 is conflict
        self.assertEqual(
            xw.total_floors_level({"total_floors": 10}, {"total_floors": 12}), "conflict"
        )
        # 4. 0/9 is unknown
        self.assertEqual(xw.total_floors_level({"total_floors": 0}, {"total_floors": 9}), "unknown")
        # 5. "8/10" against total 9 is off_by_one (_total_floors_of reads the slash)
        self.assertEqual(
            xw.total_floors_level({"floor": "8/10"}, {"total_floors": 9}), "off_by_one"
        )

    def test_19_v1_and_v2_at_once(self):
        # v1 and v2 pair levels for 10/9
        a = {"floor": "8", "total_floors": 10, "area_m2": 50, "rooms": 3, "district": "გლდანი"}
        b = {
            "floor": "8",
            "total_floors": 9,
            "area_m2": 50,
            "rooms": 3,
            "district": "გლდანი",
            "street": "შენგელია რ. ქ. 7",
        }
        v1_levels = xw.pair_levels(a, b, version=1)
        v2_levels = xw.pair_levels(a, b, version=2)
        self.assertEqual(v1_levels[3], "unknown")
        self.assertEqual(v2_levels[3], "off_by_one")

        # Load samples
        sample_path = Path(__file__).resolve().parent / "fixtures" / "xray_weights_sample.json"
        with open(sample_path, encoding="utf-8") as f:
            w1 = json.load(f)
        sample_v2_path = (
            Path(__file__).resolve().parent / "fixtures" / "xray_weights_sample_v2.json"
        )
        with open(sample_v2_path, encoding="utf-8") as f:
            w2 = json.load(f)

        # 9.0 pair posterior with v1 sample equals ~0.854
        p1 = xw.posterior(w1, a, b)
        self.assertAlmostEqual(p1, 0.853746, places=4)

        # 9.0 pair posterior with v2 sample is lower (~0.763)
        p2 = xw.posterior(w2, a, b)
        self.assertAlmostEqual(p2, 0.762542, places=4)
        self.assertLess(p2, p1)

    def test_20_gate_with_champion(self):
        sample_path = Path(__file__).resolve().parent / "fixtures" / "xray_weights_sample.json"
        with open(sample_path, encoding="utf-8") as f:
            w1 = json.load(f)
        champion = {**w1, "id": "w-2026-W46"}

        def make_rec(pid, label, target_kind="listing"):
            return {
                "pair_id": pid,
                "target_key": pid,
                "listing_pair_key": f"l1_{pid}__l2_{pid}",
                "target_kind": target_kind,
                "label": label,
                "labelled_at": "2026-11-09T00:00:00",
                "snapshot": "present",
                "target": {"pair_id": pid, "district": "Vake"},
                "candidate": {"pair_id": pid, "id": f"cand_{pid}", "district": "Vake"},
                "relations": {},
                "evidence": {},
                "review": None,
            }

        records_5 = [make_rec(f"p{i}", 1) for i in range(1, 6)]
        vectors_pass = [{"name": "v1", "gate": True, "args": [{}, {}], "expected": False}]

        # 1. Champion is w-2026-W46, report has "champion": "w-2026-W46"
        res = xw.gate(w1, records_5, vectors_pass, {"ok": True}, champion=champion)
        self.assertEqual(res["champion"], "w-2026-W46")
        # Identical challenger has 0 disagreements and is not passed
        self.assertEqual(res["all"]["disagreements"], 0)
        self.assertFalse(res["passed"])

        # 2. Fake challenger fixing 5 independent champion mistakes passes A
        orig_chall = xw.challenger_says_same
        try:
            # champion says False on all 5, challenger says True (matches label 1)
            xw.challenger_says_same = lambda w, t, c: w.get("id") != "w-2026-W46"
            w_chall = {**w1, "id": "w-challenger"}
            vectors_true = [{"name": "v1", "gate": True, "args": [{}, {}], "expected": True}]
            res_pass = xw.gate(w_chall, records_5, vectors_true, {"ok": True}, champion=champion)
            self.assertEqual(res_pass["all"]["disagreements"], 5)
            self.assertEqual(res_pass["all"]["wins"], 5)
            self.assertEqual(res_pass["all"]["losses"], 0)
            self.assertEqual(res_pass["all"]["p_value"], 1 / 32)
            self.assertTrue(res_pass["passed"])
        finally:
            xw.challenger_says_same = orig_chall

    def test_21_version_id_and_champion(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE properties (
                id TEXT PRIMARY KEY,
                source TEXT,
                url TEXT,
                district TEXT,
                subdistrict TEXT,
                street TEXT,
                floor TEXT,
                total_floors INTEGER,
                rooms INTEGER,
                bedrooms INTEGER,
                area_m2 REAL,
                price_usd REAL,
                deal_type TEXT,
                phone_number TEXT,
                group_id TEXT
            )
        """)
        for ddl in XRAY_TABLES_DDL:
            conn.execute(ddl)

        conn.execute("""
            INSERT INTO properties (id, source, url, district, area_m2, floor, deal_type, price_usd, phone_number)
            VALUES ('ss_ge_1', 'ss_ge', 'https://ss.ge/1', 'Vake', 55, '3', 'sale', 80000, '555000111')
        """)
        conn.commit()

        sample_v2_path = (
            Path(__file__).resolve().parent / "fixtures" / "xray_weights_sample_v2.json"
        )
        with open(sample_v2_path, encoding="utf-8") as f:
            w2 = json.load(f)

        # Insert a passed champion row
        champ_id = "w-2026-W46-20261109T030000Z"
        conn.execute(
            """
            INSERT INTO xray_weights (id, created_at, status, weights, thresholds, counts, labels_hash, report)
            VALUES (?, '2026-11-09T03:00:00Z', 'passed', ?, '{}', '{}', 'hash1', '{}')
            """,
            (champ_id, json.dumps(w2)),
        )
        conn.commit()

        class FakeSettings:
            TELEGRAM_CHAT_ID = "777"
            TELEGRAM_BOT_TOKEN = "fake_token"
            CLOUDFLARE_PROXY_URL = "http://fake"
            CLOUDFLARE_SYNC_KEY = "fake_key"

        now2 = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)
        fake_send = MagicMock(return_value={"ok": True})

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "out"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                run(
                    conn,
                    FakeSettings(),
                    now2,
                    fake_send,
                    out_dir=str(out_dir),
                    dry_run=False,
                    review_uncertain=0,
                    review_random=0,
                )

            # Two distinct rows exist, first row keeps status 'passed'
            rows = conn.execute(
                "SELECT id, status FROM xray_weights ORDER BY created_at"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["id"], champ_id)
            self.assertEqual(rows[0]["status"], "passed")
            self.assertEqual(rows[1]["id"], "w-2026-W46-20261109T040000Z")

            # Report used the active champion
            report_data = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report_data["champion"], champ_id)

    def test_22_calibration_pav(self):
        weights = {"lambda": 0.01, "levels_version": 2, "fields": {}}
        p_map = {
            "p1": 0.1,
            "p2": 0.2,
            "p3": 0.3,
            "p4": 0.4,
            "p5": 0.5,
            "p6": 0.6,
            "p7": 0.7,
            "p8": 0.8,
        }
        orig_post = xw.posterior
        try:
            xw.posterior = lambda w, t, c: p_map[t["pair_id"]]
            recs = [
                {
                    "pair_id": "p1",
                    "snapshot": "present",
                    "label": 0,
                    "target": {"pair_id": "p1"},
                    "candidate": {"pair_id": "p1"},
                },
                {
                    "pair_id": "p2",
                    "snapshot": "present",
                    "label": 0,
                    "target": {"pair_id": "p2"},
                    "candidate": {"pair_id": "p2"},
                },
                {
                    "pair_id": "p3",
                    "snapshot": "present",
                    "label": 1,
                    "target": {"pair_id": "p3"},
                    "candidate": {"pair_id": "p3"},
                },
                {
                    "pair_id": "p4",
                    "snapshot": "present",
                    "label": 0,
                    "target": {"pair_id": "p4"},
                    "candidate": {"pair_id": "p4"},
                },
                {
                    "pair_id": "p5",
                    "snapshot": "present",
                    "label": 1,
                    "target": {"pair_id": "p5"},
                    "candidate": {"pair_id": "p5"},
                },
                {
                    "pair_id": "p6",
                    "snapshot": "present",
                    "label": 1,
                    "target": {"pair_id": "p6"},
                    "candidate": {"pair_id": "p6"},
                },
                {
                    "pair_id": "p7",
                    "snapshot": "present",
                    "label": 1,
                    "target": {"pair_id": "p7"},
                    "candidate": {"pair_id": "p7"},
                },
                {
                    "pair_id": "p8",
                    "snapshot": "present",
                    "label": 1,
                    "target": {"pair_id": "p8"},
                    "candidate": {"pair_id": "p8"},
                },
            ]
            cal1 = xw.calibration(weights, recs)
            self.assertEqual(cal1["answers"], 8)
            expected_bps = [
                [0.2, 0.25],
                [0.4, 0.5],
                [0.8, 5.0 / 6.0],
            ]
            self.assertEqual(len(cal1["breakpoints"]), 3)
            for (hi, rate), (exp_hi, exp_rate) in zip(cal1["breakpoints"], expected_bps):
                self.assertAlmostEqual(hi, exp_hi)
                self.assertAlmostEqual(rate, exp_rate)

            # Shuffling records or renaming every pair_id gives identical output
            import random

            recs_shuffled = list(recs)
            random.Random(42).shuffle(recs_shuffled)
            cal_shuffled = xw.calibration(weights, recs_shuffled)
            self.assertEqual(cal1, cal_shuffled)

            recs_renamed = [
                {
                    **r,
                    "pair_id": f"renamed_{r['pair_id']}",
                    "target": {"pair_id": r["pair_id"]},
                    "candidate": {"pair_id": r["pair_id"]},
                }
                for r in recs
            ]
            cal_renamed = xw.calibration(weights, recs_renamed)
            self.assertEqual(cal1, cal_renamed)

            # Two answers with one score and labels 1 and 0 form one group (ties pooled)
            xw.posterior = lambda w, t, c: 0.5
            recs_10 = [
                {"pair_id": "a", "snapshot": "present", "label": 1, "target": {}, "candidate": {}},
                {"pair_id": "b", "snapshot": "present", "label": 0, "target": {}, "candidate": {}},
            ]
            cal_10 = xw.calibration(weights, recs_10)
            self.assertEqual(len(cal_10["breakpoints"]), 1)
            self.assertEqual(cal_10["breakpoints"][0], [0.5, 0.5])
            self.assertEqual(cal_10["blocks"][0], [0.5, 1, 2])

            # No two breakpoints share a hi; hi values rounded to 6 decimals; rates never decrease
            prev_hi = -1.0
            prev_rate = -1.0
            for hi, rate in cal1["breakpoints"]:
                self.assertGreater(hi, prev_hi)
                self.assertEqual(hi, round(hi, 6))
                self.assertGreaterEqual(rate, prev_rate)
                prev_hi = hi
                prev_rate = rate

            # A top block of 14/14 after a block of 7/13 gives sure_above = the 7/13 block's hi
            recs_top14 = []
            for i in range(13):
                recs_top14.append(
                    {
                        "pair_id": f"m{i}",
                        "snapshot": "present",
                        "label": 1 if i < 7 else 0,
                        "target": {"score": 0.853746},
                    }
                )
            for i in range(14):
                recs_top14.append(
                    {
                        "pair_id": f"s{i}",
                        "snapshot": "present",
                        "label": 1,
                        "target": {"score": 0.979429},
                    }
                )
            xw.posterior = lambda w, t, c: t.get("score") or 0.5
            cal_top = xw.calibration(weights, recs_top14)
            self.assertEqual(len(cal_top["blocks"]), 2)
            self.assertEqual(cal_top["sure_above"], 0.853746)

            # A top block of 2/2 (Wilson lower bound below one half) gives sure_above == 1.0
            recs_22 = [
                {"pair_id": "t1", "snapshot": "present", "label": 1, "target": {"score": 0.9}},
                {"pair_id": "t2", "snapshot": "present", "label": 1, "target": {"score": 0.9}},
            ]
            cal_22 = xw.calibration(weights, recs_22)
            self.assertEqual(cal_22["sure_above"], 1.0)

            # No answers gives sure_above == 1.0
            cal_empty = xw.calibration(weights, [])
            self.assertEqual(
                cal_empty, {"breakpoints": [], "blocks": [], "sure_above": 1.0, "answers": 0}
            )
        finally:
            xw.posterior = orig_post

    def test_24_shown_pair(self):
        sample_path = Path(__file__).resolve().parent / "fixtures" / "xray_weights_sample.json"
        with open(sample_path, encoding="utf-8") as f:
            weights = json.load(f)

        target = {"district": "გლდანი", "area_m2": 72, "floor": "1", "rooms": 3, "micro": 6}
        cand_agree = {
            "district": "გლდანი",
            "street": "გლდანი - VI მ/რ",
            "area_m2": 72,
            "floor": "1",
            "rooms": 3,
        }
        # cand_agree raw is ~0.8537
        cal_low = {"breakpoints": [[0.90, 0.30]], "sure_above": 0.95}
        w_cal = {**weights, "calibration": cal_low}
        shown = xw.shown_pair(w_cal, target, cand_agree)
        self.assertIsNotNone(shown)
        self.assertEqual(shown["tier"], "maybe")
        self.assertAlmostEqual(shown["shown"], 0.30)
        self.assertAlmostEqual(shown["raw"], 0.8579, places=3)

        # Pair with raw 0.45 is None (below 0.5)
        cand_far = {
            "district": "გლდანი",
            "street": "გლდანი - VI მ/რ",
            "area_m2": 50,
            "floor": "1",
            "rooms": 3,
        }
        self.assertIsNone(xw.shown_pair(w_cal, target, cand_far))

        # Guard still applies: floor/area alone without other agreed facts is None
        cand_fa_only = {
            "district": "საბურთალო",
            "street": "ვაჟა-ფშაველას გამზ.",
            "area_m2": 72,
            "floor": "1",
            "rooms": None,
        }
        self.assertIsNone(xw.shown_pair(w_cal, target, cand_fa_only))

    def test_25_tier_report(self):
        sample_path = Path(__file__).resolve().parent / "fixtures" / "xray_weights_sample.json"
        with open(sample_path, encoding="utf-8") as f:
            weights = json.load(f)

        # 6 units: units 0..4 have 1 agreeing pair with label 1; unit 5 has 1 pair with label 0
        recs = [
            {
                "pair_id": f"p{i}",
                "listing_pair_key": f"k{i}",
                "snapshot": "present",
                "label": 1 if i < 5 else 0,
                "target": {
                    "id": f"t{i}",
                    "district": "გლდანი",
                    "area_m2": 72,
                    "floor": "1",
                    "rooms": 3,
                },
                "candidate": {
                    "id": f"c{i}",
                    "district": "გლდანი",
                    "street": "გლდანი - VI მ/რ",
                    "area_m2": 72,
                    "floor": "1",
                    "rooms": 3,
                },
            }
            for i in range(6)
        ]
        rep = xw.tier_report(weights, recs)
        self.assertEqual(rep["units"], 6)
        self.assertEqual(rep["sure"]["pairs"] + rep["maybe"]["pairs"] + rep["hidden"]["pairs"], 6)
        self.assertEqual(rep["sure"]["same"] + rep["maybe"]["same"] + rep["hidden"]["same"], 5)

    def test_23_script_writes_two_calibration_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE properties (
                id TEXT PRIMARY KEY,
                source TEXT,
                url TEXT,
                district TEXT,
                subdistrict TEXT,
                street TEXT,
                floor TEXT,
                total_floors INTEGER,
                rooms INTEGER,
                bedrooms INTEGER,
                area_m2 REAL,
                price_usd REAL,
                deal_type TEXT,
                phone_number TEXT,
                group_id TEXT
            )
        """)
        for ddl in XRAY_TABLES_DDL:
            conn.execute(ddl)

        conn.execute("""
            INSERT INTO properties (id, source, url, district, area_m2, floor, deal_type, price_usd, phone_number)
            VALUES ('ss_ge_1', 'ss_ge', 'https://ss.ge/1', 'Vake', 55, '3', 'sale', 80000, '555000111')
        """)
        conn.commit()

        sample_v2_path = (
            Path(__file__).resolve().parent / "fixtures" / "xray_weights_sample_v2.json"
        )
        with open(sample_v2_path, encoding="utf-8") as f:
            w2 = json.load(f)

        champ_id = "w-2026-W46-20261109T030000Z"
        conn.execute(
            """
            INSERT INTO xray_weights (id, created_at, status, weights, thresholds, counts, labels_hash, report)
            VALUES (?, '2026-11-09T03:00:00Z', 'passed', ?, '{}', '{}', 'hash1', '{}')
            """,
            (champ_id, json.dumps(w2)),
        )
        conn.commit()

        class FakeSettings:
            TELEGRAM_CHAT_ID = "777"
            TELEGRAM_BOT_TOKEN = "fake_token"
            CLOUDFLARE_PROXY_URL = "http://fake"
            CLOUDFLARE_SYNC_KEY = "fake_key"

        now = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)
        fake_send = MagicMock(return_value={"ok": True})

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "out"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                run(
                    conn,
                    FakeSettings(),
                    now,
                    fake_send,
                    out_dir=str(out_dir),
                    dry_run=False,
                    review_uncertain=0,
                    review_random=0,
                )

            # Live run writes 2 rows in xray_calibration: champion and challenger
            cal_rows = conn.execute(
                "SELECT weights_id FROM xray_calibration ORDER BY weights_id"
            ).fetchall()
            self.assertEqual(len(cal_rows), 2)
            self.assertEqual(cal_rows[0]["weights_id"], champ_id)
            self.assertEqual(cal_rows[1]["weights_id"], "w-2026-W46-20261109T040000Z")

            # Dry run writes calibration.json and writes 0 new rows
            dry_out = Path(tmp_dir) / "dry_out"
            conn.execute("DELETE FROM xray_calibration")
            conn.commit()
            buf_dry = io.StringIO()
            with contextlib.redirect_stdout(buf_dry):
                run(
                    conn,
                    FakeSettings(),
                    now,
                    fake_send,
                    out_dir=str(dry_out),
                    dry_run=True,
                    review_uncertain=0,
                    review_random=0,
                )
            cal_json = json.loads((dry_out / "calibration.json").read_text(encoding="utf-8"))
            self.assertIn(champ_id, cal_json)
            self.assertIn("w-2026-W46-20261109T040000Z", cal_json)
            self.assertIn("sure_above", cal_json[champ_id])
            self.assertIn("blocks", cal_json[champ_id])
            rep_json = json.loads((dry_out / "report.json").read_text(encoding="utf-8"))
            self.assertIn("tiers", rep_json)
            self.assertEqual(len(conn.execute("SELECT * FROM xray_calibration").fetchall()), 0)

    def test_30_post_units_follow_the_flat(self):
        def post(pid, reply, key, cand):
            return {
                "pair_id": pid,
                "target_key": reply,
                "post_key": key,
                "target": {},
                "candidate": {"id": cand},
            }

        # 1. The same post (same post_key) answered in two replies is one unit.
        u = xw._units([post("a", "RA", "k1", "L1"), post("b", "RB", "k1", "L1")])
        self.assertEqual(u["a"], u["b"])
        # 2. One post's answers on two candidates are one unit, like a listing reply.
        u = xw._units([post("c", "RC", "k2", "L2"), post("d", "RC", "k2", "L3")])
        self.assertEqual(u["c"], u["d"])
        # 3. A post answer and a listing answer about the same candidate flat are one unit.
        listing = {
            "pair_id": "f",
            "target_key": "RE",
            "listing_pair_key": "L4__L5",
            "target": {"id": "L5"},
            "candidate": {"id": "L4"},
        }
        u = xw._units([post("e", "RD", "k3", "L4"), listing])
        self.assertEqual(u["e"], u["f"])
        # 4. Different posts about different candidates stay apart.
        u = xw._units([post("g", "RF", "k4", "L6"), post("h", "RG", "k5", "L7")])
        self.assertNotEqual(u["g"], u["h"])

    def test_31_calibration_counts_each_flat_once(self):
        weights = {"lambda": 0.01, "levels_version": 2, "fields": {}}

        def rec(pid, a, b, label):
            return {
                "pair_id": pid,
                "listing_pair_key": "__".join(sorted([a, b])),
                "snapshot": "present",
                "label": label,
                "target": {"id": a},
                "candidate": {"id": b},
            }

        orig_post = xw.posterior
        try:
            xw.posterior = lambda w, t, c: 0.5
            # Three reposts of flat A answered ✅ (one unit), one other flat answered ❌.
            recs = [
                rec("x1", "A", "B", 1),
                rec("x2", "A", "C", 1),
                rec("x3", "A", "D", 1),
                rec("y1", "E", "F", 0),
            ]
            cal = xw.calibration(weights, recs)
            self.assertEqual(cal["answers"], 4)
            self.assertEqual(cal["blocks"], [[0.5, 1, 2]])
            self.assertEqual(cal["breakpoints"], [[0.5, 0.5]])
        finally:
            xw.posterior = orig_post

    def test_32_gate_counts_false_matches_per_flat(self):
        weights = {"lambda": 0.01, "fields": {}}

        def rec(pid, a, b):
            return {
                "pair_id": pid,
                "target_key": pid,
                "label": 0,
                "target_kind": "listing",
                "listing_pair_key": "__".join(sorted([a, b])),
                "snapshot": "at_label",
                "target": {"id": a},
                "candidate": {"id": b},
                "relations": {},
                "evidence": {},
                "review": None,
            }

        recs = [rec("x1", "A", "B"), rec("x2", "A", "C"), rec("x3", "A", "D")]
        vectors = [{"name": "v1", "gate": True, "args": [{}, {}], "expected": True}]
        orig_champ = xw._candidate_matches_target
        orig_chall = xw.challenger_says_same
        try:
            xw._candidate_matches_target = lambda t, c: False
            xw.challenger_says_same = lambda w, *args: True
            res = xw.gate(weights, recs, vectors, {"ok": True})
            self.assertEqual(res["all"]["fp_challenger"], 1)
            self.assertEqual(res["all"]["fp_champion"], 0)
        finally:
            xw._candidate_matches_target = orig_champ
            xw.challenger_says_same = orig_chall
