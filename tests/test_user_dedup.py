import unittest
import os
import tempfile
import json
from pathlib import Path
from core.database import DatabaseEngine

class TestUserDeduplication(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_properties.db")
        # Create a mock seen_ids.txt with existing IDs
        seen_path = Path(self.tmp_dir.name) / "seen_ids.txt"
        with open(seen_path, "w", encoding="utf-8") as f:
            f.write("ss_ge_111\nmyhome_222\n")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_seeding_and_per_user_notifications(self):
        db = DatabaseEngine(self.db_path)
        sandro_id = "1105321687"
        valeriy_id = "646957970"

        # 1. Verify Sandro has existing IDs seeded
        self.assertTrue(db.is_user_notified(sandro_id, "ss_ge_111"))
        self.assertTrue(db.is_user_notified(sandro_id, "myhome_222"))

        # 2. Verify Valeriy does NOT have them yet (clean slate)
        self.assertFalse(db.is_user_notified(valeriy_id, "ss_ge_111"))
        self.assertFalse(db.is_user_notified(valeriy_id, "myhome_222"))

        # 3. Mark Valeriy notified for ss_ge_111
        db.mark_user_notified(valeriy_id, "ss_ge_111")
        self.assertTrue(db.is_user_notified(valeriy_id, "ss_ge_111"))
        self.assertFalse(db.is_user_notified(valeriy_id, "myhome_222"))

        # 4. Verify user_seen.json was created and persists
        user_seen_file = Path(self.tmp_dir.name) / "user_seen.json"
        self.assertTrue(user_seen_file.exists())
        with open(user_seen_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("ss_ge_111", data[valeriy_id])
        self.assertIn("ss_ge_111", data[sandro_id])

        # 5. Reload database from disk and ensure state persists
        db2 = DatabaseEngine(self.db_path)
        self.assertTrue(db2.is_user_notified(valeriy_id, "ss_ge_111"))
        self.assertFalse(db2.is_user_notified(valeriy_id, "myhome_222"))
        self.assertTrue(db2.is_user_notified(sandro_id, "ss_ge_111"))

if __name__ == "__main__":
    unittest.main()
