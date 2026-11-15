#!/usr/bin/env python3
"""Nightly sweep: marks MyHome listings in D1 that MyHome deleted (properties.delisted_at).

X-Ray replies check the MyHome duplicates they would show (the "alive" lookup job); this keeps
D1 itself clean, so the photo search, the owner search and the duplicate reads skip a deleted
listing without a check. Each night reads SWEEP_PER_NIGHT rows, the least recently checked
first, and stamps alive_checked_at, so the nights rotate through the whole table. A page that
does not load is left for another night. The newest MyHome listing is read first as a
control: if it reads as deleted, MyHome's page has changed and the run marks nothing.

Usage:
    python scripts/check_delisted.py [--limit N]
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.myhome_alive import FetchHtml, looks_deleted  # noqa: E402

# About a week for a full pass over D1's ~13,000 MyHome rows (November 2026).
SWEEP_PER_NIGHT = 2000
CONCURRENCY = 4


async def sweep(conn: Any, fetch_html: FetchHtml, limit: int) -> Optional[Dict[str, int]]:
    """Counts of what the run read, or None when the control stopped it."""
    control = conn.execute(
        "SELECT id, source_id FROM properties WHERE source = 'myhome' AND delisted_at IS NULL"
        " ORDER BY scraped_at DESC LIMIT 1"
    ).fetchone()
    if control is None:
        return {"checked": 0, "live": 0, "deleted": 0, "unknown": 0}
    if await looks_deleted(fetch_html, str(control["source_id"])) is not False:
        print("MyHome sweep: the control listing did not read as live, nothing marked")
        return None
    rows = conn.execute(
        "SELECT id, source_id FROM properties WHERE source = 'myhome' AND delisted_at IS NULL"
        " AND id != ? ORDER BY alive_checked_at IS NOT NULL, alive_checked_at, scraped_at"
        " LIMIT ?",
        (control["id"], limit),
    ).fetchall()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def read(sid: str) -> Optional[bool]:
        async with sem:
            return await looks_deleted(fetch_html, sid)

    answers = await asyncio.gather(*(read(str(r["source_id"])) for r in rows))
    now = datetime.now(timezone.utc).isoformat()
    counts = {"checked": len(rows), "live": 0, "deleted": 0, "unknown": 0}
    for r, deleted in zip(rows, answers):
        if deleted is None:
            counts["unknown"] += 1
            continue
        if deleted:
            counts["deleted"] += 1
            conn.execute(
                "UPDATE properties SET delisted_at = ?, alive_checked_at = ? WHERE id = ?",
                (now, now, r["id"]),
            )
        else:
            counts["live"] += 1
            conn.execute("UPDATE properties SET alive_checked_at = ? WHERE id = ?", (now, r["id"]))
    conn.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Mark MyHome listings that MyHome deleted.")
    parser.add_argument("--limit", type=int, default=SWEEP_PER_NIGHT)
    args = parser.parse_args()

    from core.database import DatabaseEngine
    from scrapers.myhome import MyHomeScraper

    db = DatabaseEngine()
    with db._connection() as conn:
        counts = asyncio.run(sweep(conn, MyHomeScraper().fetch_html, args.limit))
    text = (
        "MyHome sweep stopped: the control listing did not read as live."
        if counts is None
        else "MyHome sweep: checked {checked}, live {live}, deleted {deleted},"
        " page did not load {unknown}.".format(**counts)
    )
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
