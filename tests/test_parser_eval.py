"""Unit tests for scripts/parser_eval.py."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.parser_eval import (
    COLUMNS,
    FIELDS,
    evaluate,
    load_rows,
    main,
    pred_of,
    same,
    to_markdown,
    truth_of,
)


def _create_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols_def = ", ".join(f"{col} TEXT" for col in COLUMNS)
    conn.execute(f"CREATE TABLE properties ({cols_def})")

    # Row 1: Sale, correct attributes extracted by parser
    conn.execute(
        f"INSERT INTO properties ({', '.join(COLUMNS)}) VALUES ({', '.join(['?'] * len(COLUMNS))})",
        (
            "1",
            "https://example.com/1",
            "იყიდება 3 ოთახიანი ბინა 72 კვ.მ მე-5 სართული 85000$",
            "sale",
            "საბურთალო",
            None,
            None,
            "5/12",
            "12",
            "3",
            "2",
            "72.0",
            "85000.0",
            "10",
        ),
    )

    # Row 2: Rent, text has 2 rooms, column has 3 rooms; rent price should be ignored in truth
    conn.execute(
        f"INSERT INTO properties ({', '.join(COLUMNS)}) VALUES ({', '.join(['?'] * len(COLUMNS))})",
        (
            "2",
            "https://example.com/2",
            "ქირავდება 2 ოთახიანი ბინა მეტროსთან",
            "rent",
            None,
            None,
            None,
            "3",
            "5",
            "3",
            "1",
            "50.0",
            "1000.0",
            None,
        ),
    )

    # Row 3: Empty description, should not be loaded
    conn.execute(
        f"INSERT INTO properties ({', '.join(COLUMNS)}) VALUES ({', '.join(['?'] * len(COLUMNS))})",
        (
            "3",
            "https://example.com/3",
            "",
            "sale",
            None,
            None,
            None,
            "1",
            "5",
            "1",
            "1",
            "40.0",
            "50000.0",
            None,
        ),
    )
    conn.commit()
    return conn


class TestParserEval(unittest.TestCase):
    def setUp(self):
        self.conn = _create_test_db()

    def tearDown(self):
        self.conn.close()

    def test_load_rows_filters_empty_and_loads_columns(self):
        rows = load_rows(self.conn)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "1")
        self.assertEqual(rows[1]["id"], "2")
        for col in COLUMNS:
            self.assertIn(col, rows[0])

    def test_load_rows_paging(self):
        with mock.patch("scripts.parser_eval.PAGE", 1):
            paged_rows = load_rows(self.conn)
        rows = load_rows(self.conn)
        self.assertEqual(paged_rows, rows)

    def test_truth_of_fields(self):
        row_sale = {
            "deal_type": "sale",
            "area_m2": "72.5",
            "floor": "5/12",
            "total_floors": "12",
            "rooms": "3",
            "bedrooms": "2",
            "price_usd": "85000",
            "district": "საბურთალო",
            "subdistrict": "მე-6 მ/რ",
            "street": None,
        }
        self.assertEqual(truth_of(row_sale, "area_m2"), 72.5)
        self.assertEqual(truth_of(row_sale, "floor"), "5")
        self.assertEqual(truth_of(row_sale, "total_floors"), 12)
        self.assertEqual(truth_of(row_sale, "rooms"), 3)
        self.assertEqual(truth_of(row_sale, "bedrooms"), 2)
        self.assertEqual(truth_of(row_sale, "price_usd"), 85000.0)
        self.assertEqual(truth_of(row_sale, "district"), "საბურთალო")
        self.assertEqual(truth_of(row_sale, "micro"), 6)

        row_rent = {
            "deal_type": "rent",
            "price_usd": "1000",
            "area_m2": "0",
            "total_floors": "0",
        }
        # price_usd returns None for rent
        self.assertIsNone(truth_of(row_rent, "price_usd"))
        # 0 counts as None for area_m2 and total_floors
        self.assertIsNone(truth_of(row_rent, "area_m2"))
        self.assertIsNone(truth_of(row_rent, "total_floors"))

    def test_pred_of_normalisation(self):
        attrs = {
            "area_m2": 72.0,
            "floor": "5",
            "total_floors": 12,
            "rooms": 3,
            "bedrooms": 2,
            "price_usd": 85000.0,
            "district": "საბურთალო",
            "micro": 6,
        }
        for field in FIELDS:
            self.assertIsNotNone(pred_of(attrs, field))

    def test_same_comparison(self):
        self.assertTrue(same("area_m2", 72.0, 72.8))
        self.assertTrue(same("area_m2", 72.0, 73.0))
        self.assertFalse(same("area_m2", 72.0, 73.5))

        self.assertTrue(same("price_usd", 100000.0, 100500.0))
        self.assertTrue(same("price_usd", 100000.0, 101000.0))
        self.assertFalse(same("price_usd", 100000.0, 102000.0))

        self.assertTrue(same("rooms", 3, 3))
        self.assertFalse(same("rooms", 3, 2))

    def test_evaluate(self):
        rows = load_rows(self.conn)
        report = evaluate(rows)

        self.assertEqual(report["rows"], 2)
        self.assertEqual(report["by_deal_type"], {"sale": 1, "rent": 1})
        self.assertEqual(report["with_metro_id"], 1)
        self.assertEqual(report["mentions_metro"], 1)
        self.assertEqual(report["metro_id_and_mention"], 0)

        # rooms: row 1 truth=3, pred=3 (correct); row 2 truth=3, pred=2 (wrong)
        rooms_stats = report["rooms"]
        self.assertEqual(rooms_stats["truth_n"], 2)
        self.assertEqual(rooms_stats["correct_n"], 1)
        self.assertEqual(len(rooms_stats["examples"]), 1)
        self.assertEqual(rooms_stats["examples"][0]["id"], "2")
        self.assertEqual(rooms_stats["examples"][0]["truth"], 3)
        self.assertEqual(rooms_stats["examples"][0]["pred"], 2)

        # price_usd: evaluated only on sale (row 1)
        price_stats = report["price_usd"]
        self.assertEqual(price_stats["truth_n"], 1)
        self.assertEqual(price_stats["correct_n"], 1)

    def test_to_markdown(self):
        rows = load_rows(self.conn)
        report = evaluate(rows)
        md = to_markdown(report)
        self.assertIn("| field | truth_n | coverage | precision |", md)
        for field in FIELDS:
            self.assertIn(f"| {field} |", md)

    def test_main_with_conn(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ret = main(["--out-dir", tmp_dir], conn=self.conn)
            self.assertEqual(ret, 0)
            out_file = Path(tmp_dir) / "parser_eval.json"
            self.assertTrue(out_file.exists())
            with open(out_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["rows"], 2)
            self.assertEqual(data["rooms"]["truth_n"], 2)

    def test_main_missing_settings_exits_2(self):
        with mock.patch("scripts.parser_eval.settings.CLOUDFLARE_PROXY_URL", ""):
            ret = main([], conn=None)
            self.assertEqual(ret, 2)


if __name__ == "__main__":
    unittest.main()
