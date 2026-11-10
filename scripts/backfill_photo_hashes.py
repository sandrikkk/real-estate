#!/usr/bin/env python3
"""Backfill perceptual hashes (dHash) for listings that have photos but no hash yet (ADR 0005).

Usage:
    python scripts/backfill_photo_hashes.py [--limit N] [--dry-run]
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import DatabaseEngine  # noqa: E402
from core.dedup import DuplicateDetector, _compute_hashes  # noqa: E402

BACKFILL_PER_RUN = 1500


def classify_photo_host(url: str) -> str:
    """Classifies photo URL host into old MyHome / new MyHome / SS.ge / other."""
    if not url:
        return "other"
    if "static-statements.tnet.ge" in url:
        return "old MyHome"
    if "static-api-statements.tnet.ge" in url:
        return "new MyHome"
    if "ss.ge" in url:
        return "SS.ge"
    return "other"


def parse_images_json(raw: Optional[str]) -> List[str]:
    """Extract list of image URLs from images_json string or list."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
            if isinstance(parsed, str) and parsed:
                return [parsed]
        except Exception:
            return []
    return []


async def run_backfill(
    db: DatabaseEngine,
    limit: int = BACKFILL_PER_RUN,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Selects unhashed listings, computes photo hashes, persists hashes or attempts."""
    select_sql = """
    SELECT p.id, p.images_json FROM properties p
    WHERE p.images_json IS NOT NULL AND p.images_json NOT IN ('', '[]')
      AND NOT EXISTS (SELECT 1 FROM image_hashes h WHERE h.listing_id = p.id)
      AND NOT EXISTS (SELECT 1 FROM photo_hash_attempts a WHERE a.listing_id = p.id)
    ORDER BY p.scraped_at DESC LIMIT ?
    """

    with db._connection() as conn:
        cursor = conn.cursor()
        cursor.execute(select_sql, (limit,))
        rows = cursor.fetchall()

    selected = len(rows)
    failed_by_host = {"old MyHome": 0, "new MyHome": 0, "SS.ge": 0, "other": 0}
    by_listing: Dict[str, List[str]] = {}
    failed_ids: List[str] = []

    parsed_rows: List[Tuple[str, List[str]]] = []
    for r in rows:
        lid = r["id"] if hasattr(r, "__getitem__") and not isinstance(r, (tuple, list)) else r[0]
        raw_img = (
            r["images_json"]
            if hasattr(r, "__getitem__") and not isinstance(r, (tuple, list))
            else r[1]
        )
        images = parse_images_json(raw_img)
        if not images:
            failed_ids.append(lid)
            failed_by_host["other"] += 1
        else:
            parsed_rows.append((lid, images))

    sem = asyncio.Semaphore(3)

    async def _hash_one(lid: str, urls: List[str]) -> Tuple[str, List[str], str]:
        first_url = urls[0] if urls else ""
        async with sem:
            try:
                hs = await _compute_hashes(urls, max_images=3)
            except Exception:
                hs = []
        return lid, hs, first_url

    if parsed_rows:
        results = await asyncio.gather(*(_hash_one(lid, urls) for lid, urls in parsed_rows))
        for lid, hs, first_url in results:
            if hs:
                by_listing[lid] = hs
            else:
                failed_ids.append(lid)
                cat = classify_photo_host(first_url)
                failed_by_host[cat] += 1

    hashed = len(by_listing)
    failed = len(failed_ids)

    if not dry_run:
        detector = DuplicateDetector(db)
        if by_listing:
            detector.save_hashes_batch(by_listing)

        if failed_ids:
            now_iso = datetime.now(timezone.utc).isoformat()
            attempt_rows = [(lid, now_iso) for lid in failed_ids]
            with db._connection() as conn:
                cursor = conn.cursor()
                for i in range(0, len(attempt_rows), 50):
                    cursor.executemany(
                        "INSERT OR REPLACE INTO photo_hash_attempts (listing_id, tried_at) VALUES (?, ?)",
                        attempt_rows[i : i + 50],
                    )
                conn.commit()

    count_sql = """
    SELECT COUNT(*) FROM properties p
    WHERE p.images_json IS NOT NULL AND p.images_json NOT IN ('', '[]')
      AND NOT EXISTS (SELECT 1 FROM image_hashes h WHERE h.listing_id = p.id)
      AND NOT EXISTS (SELECT 1 FROM photo_hash_attempts a WHERE a.listing_id = p.id)
    """
    remaining = 0
    with db._connection() as conn:
        cursor = conn.cursor()
        cursor.execute(count_sql)
        row = cursor.fetchone()
        if row:
            try:
                remaining = int(row[0])
            except Exception:
                remaining = int(row.get("COUNT(*)", 0) if hasattr(row, "get") else 0)

    stats = {
        "selected": selected,
        "hashed": hashed,
        "failed": failed,
        "failed_by_host": failed_by_host,
        "remaining": remaining,
    }

    print(f"Selected: {selected}")
    print(f"Hashed: {hashed}")
    print(f"Failed: {failed}")
    print("Failed by host:")
    print(f"  old MyHome: {failed_by_host['old MyHome']}")
    print(f"  new MyHome: {failed_by_host['new MyHome']}")
    print(f"  SS.ge: {failed_by_host['SS.ge']}")
    print(f"  other: {failed_by_host['other']}")
    print(f"Remaining: {remaining}")

    if hasattr(db, "d1_client") and db.d1_client and hasattr(db.d1_client, "usage_line"):
        print(db.d1_client.usage_line())

    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill photo hashes for unhashed listings.")
    parser.add_argument(
        "--limit",
        type=int,
        default=BACKFILL_PER_RUN,
        help=f"Listing budget to process (default {BACKFILL_PER_RUN})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and hash only; do not write to database",
    )
    args = parser.parse_args()

    db = DatabaseEngine()
    asyncio.run(run_backfill(db, limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
