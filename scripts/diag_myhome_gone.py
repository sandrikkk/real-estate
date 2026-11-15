"""Throwaway: the labels and photo data are all still there after the sweep."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.database import DatabaseEngine  # noqa: E402

with DatabaseEngine()._connection() as conn:
    q = lambda sql: conn.execute(sql).fetchone()["n"]  # noqa: E731
    print("answers (xray_feedback):", q("SELECT COUNT(*) n FROM xray_feedback"))
    print("photo hashes (image_hashes):", q("SELECT COUNT(*) n FROM image_hashes"))
    print("listings (properties):", q("SELECT COUNT(*) n FROM properties"))
    print("  marked delisted (kept, only hidden):", q("SELECT COUNT(*) n FROM properties WHERE delisted_at IS NOT NULL"))
    print("answers whose listing is now marked delisted:", q(
        "SELECT COUNT(*) n FROM xray_feedback f JOIN properties p ON p.id = f.candidate_id WHERE p.delisted_at IS NOT NULL"))
