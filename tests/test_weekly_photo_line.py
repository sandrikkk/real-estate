"""The weekly X-Ray summary reports how often the photo search's lines were the same flat,
from the admin's ✅/❌ answers on photo-search replies (reply_kind "photo")."""

import unittest

from scripts.estimate_xray_weights import photo_answers, summary_text

SUMMARY = {"label": {"1": 3, "0": 2, "-1": 1}}
REPORT = {"champion": "w-2026-W46", "all": {"disagreements": 0}, "passed": False}


def rec(reply_kind, label):
    return {"reply_kind": reply_kind, "label": label}


class TestWeeklyPhotoLine(unittest.TestCase):
    def test_counts_only_answered_photo_search_pairs(self):
        records = [
            rec("photo", 1),
            rec("photo", 1),
            rec("photo", 0),
            rec("photo", -1),  # 🤷 is kept, never scored
            rec("owner", 1),
            rec("review", 0),
        ]
        self.assertEqual(photo_answers(records), (2, 3))

    def test_summary_has_the_photo_line(self):
        text = summary_text("w-1", SUMMARY, REPORT, photo=(2, 3))
        self.assertIn("📸 ფოტოთი ძებნის ხაზებიდან 2/3 იგივე ბინაა.", text)

    def test_summary_says_when_there_is_no_photo_answer(self):
        text = summary_text("w-1", SUMMARY, REPORT, photo=(0, 0))
        self.assertIn("📸 ფოტოთი ძებნაზე პასუხი ჯერ არ გაქვს.", text)

    def test_summary_without_photo_counts_is_unchanged(self):
        self.assertNotIn("📸", summary_text("w-1", SUMMARY, REPORT))


if __name__ == "__main__":
    unittest.main()
