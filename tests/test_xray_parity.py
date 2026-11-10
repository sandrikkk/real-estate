"""The two copies of the X-Ray rules must give the same answers (AGENTS.md).

`cloudflare/worker.js` runs in production and `core/xray.py` is its Python twin. This file
runs `tests/fixtures/xray_rule_vectors.json` against the twin and compares the Worker's district
tables with the twin's; `tests/js/xray_parity.test.mjs` runs the same vectors against the
Worker. When a rule changes, change both copies and add a vector.
"""

import json
import os
import shutil
import subprocess
import unittest

from core.xray import (
    DISTRICT_ALIASES,
    DISTRICT_KEYWORDS,
    _candidate_matches_target,
    _compare_layout,
    _compare_location,
    _compare_total_floors,
    _expand_district_aliases,
    evaluate_broker_verdict,
    snapshot_fields,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(REPO, "tests", "fixtures", "xray_rule_vectors.json"), encoding="utf-8") as f:
    VECTORS = json.load(f)
with open(
    os.path.join(REPO, "tests", "fixtures", "xray_weights_sample.json"), encoding="utf-8"
) as f:
    SAMPLE_WEIGHTS = json.load(f)
with open(
    os.path.join(REPO, "tests", "fixtures", "xray_weights_sample_v2.json"), encoding="utf-8"
) as f:
    SAMPLE_WEIGHTS_V2 = json.load(f)


def run_vector(fn, args):
    if fn == "matches":
        return _candidate_matches_target(*args)
    if fn == "layout":
        return _compare_layout(*args)
    if fn == "total_floors":
        return _compare_total_floors(*args)
    if fn == "location":
        return _compare_location(*args)
    if fn == "aliases":
        return sorted(_expand_district_aliases(*args))
    if fn == "verdict":
        verdict = evaluate_broker_verdict(*args)
        return {k: verdict[k] for k in ("is_broker", "badge", "reason")}
    if fn == "snapshot":
        return snapshot_fields(*args)
    if fn == "levels":
        from core.xray_weights import pair_levels

        return list(pair_levels(*args, version=1))
    if fn == "levels_v2":
        from core.xray_weights import pair_levels

        return list(pair_levels(*args, version=2))
    if fn == "challenger":
        from core.xray_weights import challenger_says_same, posterior

        if not challenger_says_same(SAMPLE_WEIGHTS, *args):
            return None
        return round(posterior(SAMPLE_WEIGHTS, *args), 6)
    if fn == "challenger_v2":
        from core.xray_weights import challenger_says_same, posterior

        if not challenger_says_same(SAMPLE_WEIGHTS_V2, *args):
            return None
        return round(posterior(SAMPLE_WEIGHTS_V2, *args), 6)
    if fn == "calibrated":
        from core.xray_weights import calibrated

        return calibrated(*args)
    if fn == "tier":
        from core.xray_weights import tier

        return tier(*args)
    raise AssertionError(f"unknown vector function: {fn}")


class TestTwinRules(unittest.TestCase):
    def test_every_vector_gives_its_expected_answer(self):
        self.assertGreaterEqual(len(VECTORS), 25)
        for v in VECTORS:
            with self.subTest(v["name"]):
                self.assertEqual(run_vector(v["fn"], v["args"]), v["expected"])

    def test_vector_names_are_unique(self):
        names = [v["name"] for v in VECTORS]
        self.assertEqual(len(names), len(set(names)))


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestDistrictTables(unittest.TestCase):
    """The district keyword list and the alias lists are copied by hand into the Worker."""

    @classmethod
    def setUpClass(cls):
        out = subprocess.run(
            ["node", os.path.join("tests", "js", "dump_worker_tables.mjs")],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        cls.worker = json.loads(out.stdout)

    def test_the_keyword_lists_are_equal(self):
        self.assertEqual([list(pair) for pair in DISTRICT_KEYWORDS], self.worker["keywords"])

    def test_the_alias_lists_are_equal(self):
        self.assertEqual(DISTRICT_ALIASES, self.worker["aliases"])


if __name__ == "__main__":
    unittest.main()
