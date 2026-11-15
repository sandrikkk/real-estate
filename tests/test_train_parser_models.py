"""Unit tests for scripts/train_parser_models.py."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.train_parser_models import (
    MIN_TEXT,
    load_deal_rows,
    main,
    report_lines,
    run_deal_type,
)


def _create_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE properties (
            id TEXT PRIMARY KEY,
            group_id TEXT,
            description TEXT,
            deal_type TEXT
        )
        """
    )

    # 150 sale rows, 150 rent rows with long descriptions
    for i in range(150):
        conn.execute(
            "INSERT INTO properties (id, group_id, description, deal_type) VALUES (?, ?, ?, ?)",
            (
                f"sale_{i}",
                f"grp_{i // 2}",
                f"იყიდება {i % 4 + 1}-ოთახიანი გარემონტებული ბინა ვაკეში {50 + i} კვ.მ ფასი {80000 + i * 1000}$",
                "sale",
            ),
        )
        conn.execute(
            "INSERT INTO properties (id, group_id, description, deal_type) VALUES (?, ?, ?, ?)",
            (
                f"rent_{i}",
                f"grp_{100 + i // 2}",
                f"ქირავდება {i % 4 + 1}-ოთახიანი მყუდრო ბინა საბურთალოზე {40 + i} კვ.მ ფასი {500 + i * 20}$ თვეში",
                "rent",
            ),
        )

    # Filtered out: daily
    conn.execute(
        "INSERT INTO properties (id, group_id, description, deal_type) VALUES (?, ?, ?, ?)",
        ("daily_1", "grp_daily", "დღიურად ქირავდება ბინა ქალაქის ცენტრში", "daily"),
    )

    # Filtered out: too short (< MIN_TEXT)
    conn.execute(
        "INSERT INTO properties (id, group_id, description, deal_type) VALUES (?, ?, ?, ?)",
        ("short_1", "grp_short", "იყიდება ბინა", "sale"),
    )

    conn.commit()
    return conn


class TestTrainParserModels(unittest.TestCase):
    def setUp(self):
        self.conn = _create_test_db()

    def tearDown(self):
        self.conn.close()

    def test_load_deal_rows_filters_daily_and_short(self):
        rows = load_deal_rows(self.conn)
        self.assertEqual(len(rows), 300)
        for r in rows:
            self.assertIn(r["deal_type"], ("sale", "rent"))
            self.assertGreaterEqual(len(r["description"].strip()), MIN_TEXT)

    def test_group_id_splits_together(self):
        # Two rows sharing a group_id must be in the same split
        rows = [
            {
                "id": "1",
                "group_id": "shared_grp",
                "description": "იყიდება ბინა გლდანში მე-5 სართულზე 75 კვ.მ",
                "deal_type": "sale",
            },
            {
                "id": "2",
                "group_id": "shared_grp",
                "description": "იყიდება ბინა გლდანში მე-5 სართულზე 75 კვ.მ იგივე",
                "deal_type": "sale",
            },
        ]
        # Evaluate split decision on group_id
        from core.text_model import test_split

        self.assertEqual(test_split(rows[0]["group_id"]), test_split(rows[1]["group_id"]))

    def test_run_deal_type_direct(self):
        rows = load_deal_rows(self.conn)
        res = run_deal_type(rows, [])
        self.assertIn("model", res)
        self.assertIn("report", res)
        self.assertEqual(res["model"]["kind"], "deal_type")

    def test_run_deal_type_and_output_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ret = main(["--kind", "deal_type", "--out-dir", tmp_dir], conn=self.conn)
            self.assertEqual(ret, 0)

            model_file = Path(tmp_dir) / "model_deal_type.json"
            report_file = Path(tmp_dir) / "report_deal_type.json"
            self.assertTrue(model_file.exists())
            self.assertTrue(report_file.exists())

            # Model size < 90,000 bytes
            size = model_file.stat().st_size
            self.assertLess(size, 90_000)

            with open(model_file, "r", encoding="utf-8") as f:
                model = json.load(f)
            self.assertEqual(model["kind"], "deal_type")
            self.assertEqual(model["classes"], ["sale", "rent"])

            with open(report_file, "r", encoding="utf-8") as f:
                report = json.load(f)
            for field in (
                "train_n",
                "test_n",
                "by_class",
                "accuracy_all",
                "threshold",
                "coverage",
                "accuracy_confident",
                "fb_checked",
                "fb_confident",
                "fb_wrong",
                "passed",
                "fb_errors",
            ):
                self.assertIn(field, report)
            self.assertIsInstance(report["fb_errors"], list)
            self.assertLessEqual(len(report["fb_errors"]), 20)

    def test_report_lines_show_class_counts_and_errors_on_one_line_each(self):
        report = {
            "by_class": {"sale": 10, "rent": 2},
            "errors": [
                {
                    "id": "myhome_1",
                    "label": "sale",
                    "pred": "rent",
                    "prob": 0.997,
                    "snippet": "გირავდება ბინა\nვაკეში",
                }
            ],
        }
        lines = report_lines(report)
        self.assertIn("by_class: rent=2 sale=10", lines)
        self.assertIn("sale -> rent 0.997 myhome_1 | გირავდება ბინა ვაკეში", lines)

    def test_errors_most_confident_first(self):
        rows = load_deal_rows(self.conn)
        for i in range(40):
            rows.append(
                {
                    "id": f"wrong_{i}",
                    "group_id": None,
                    "description": (
                        f"ქირავდება {i % 4 + 1}-ოთახიანი მყუდრო ბინა საბურთალოზე "
                        f"{40 + i} კვ.მ ფასი {500 + i * 20}$ თვეში"
                    ),
                    "deal_type": "sale",
                }
            )
        errors = run_deal_type(rows, [])["report"]["errors"]
        self.assertTrue(errors)
        probs = [e["prob"] for e in errors]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_main_prints_report_lines(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--kind", "deal_type", "--out-dir", tmp_dir], conn=self.conn)
            self.assertIn("by_class: rent=150 sale=150", out.getvalue())

    def test_main_missing_settings_exits_2(self):
        with mock.patch("scripts.train_parser_models.settings.CLOUDFLARE_PROXY_URL", ""):
            ret = main(["--kind", "deal_type"], conn=None)
            self.assertEqual(ret, 2)


if __name__ == "__main__":
    unittest.main()
