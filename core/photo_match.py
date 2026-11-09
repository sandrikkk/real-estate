"""Photo match for the X-Ray (ADR 0005): a post's photos against the photos of the listings
in the database.

The Worker stores the photos a user sends (D1 `xray_photos`, one batch per album) and queues a
"photo" job. This module runs in that job (scripts/xray_lookup.py): it hashes the photos with
the duplicate finder's own dHash, compares them with every hash in `image_hashes`, applies the
duplicate finder's own rule (two photos, or one photo that price and area confirm), and builds
the follow-up text with its candidates for the ✅/❌ buttons.

The threshold and the rule are core/dedup.py's. One thing is new and needs no number: a photo
that is found in the listings of two different districts is a logo or a render, because a flat
is in one district, so it is not counted.

Nothing here prints a batch id (it holds the chat id) or a photo link.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from core.dedup import (
    HASH_THRESHOLD,
    _decode_dhash_sync,
    count_image_matches,
    hamming,
    is_photo_duplicate,
)
from core.xray import _clean_floor, parse_xray_input, snapshot_fields

# Budgets, not matching thresholds: how many listings to show, how many hash rows to read per
# D1 request, and how long to wait so that every photo of an album has reached D1.
PHOTO_MAX_SHOWN = 5
HASH_PAGE_SIZE = 5000
ALBUM_SETTLE_SECONDS = 10

Download = Callable[[str], Awaitable[Optional[bytes]]]


def _seconds_since(iso: str) -> float:
    try:
        then = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - then).total_seconds()


def _batch_rows(conn: Any, batch_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT message_id, file_id, dhash, created_at FROM xray_photos"
        " WHERE batch_id = ? ORDER BY message_id",
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]


async def batch_hashes(conn: Any, batch_id: str, download: Download) -> List[str]:
    """The dHash of every photo of the batch; a photo is downloaded once, then its hash is kept."""
    rows = _batch_rows(conn, batch_id)
    if rows:
        # Telegram sends an album as one message per photo: wait until the newest one has had
        # time to reach D1, then read again.
        wait = ALBUM_SETTLE_SECONDS - min(_seconds_since(r["created_at"]) for r in rows)
        if wait > 0:
            await asyncio.sleep(wait)
            rows = _batch_rows(conn, batch_id)
    hashes: List[str] = []
    for row in rows:
        h = row["dhash"]
        if not h:
            data = await download(row["file_id"])
            h = await asyncio.to_thread(_decode_dhash_sync, data) if data else None
            if h:
                conn.execute(
                    "UPDATE xray_photos SET dhash = ? WHERE batch_id = ? AND message_id = ?",
                    (h, batch_id, row["message_id"]),
                )
                conn.commit()
        if h:
            hashes.append(h)
    return hashes


def load_listing_hashes(conn: Any, page_size: int = HASH_PAGE_SIZE) -> Dict[str, List[str]]:
    """Every hash in `image_hashes`, by listing. Keyset paging on the primary key, so a page
    boundary may split one listing's photos. About 10k rows (D1 row reads) per call: never call
    this in a loop."""
    out: Dict[str, List[str]] = {}
    last_id, last_index = "", -1
    while True:
        rows = conn.execute(
            "SELECT listing_id, image_index, phash FROM image_hashes"
            " WHERE listing_id > ? OR (listing_id = ? AND image_index > ?)"
            " ORDER BY listing_id, image_index LIMIT ?",
            (last_id, last_id, last_index, page_size),
        ).fetchall()
        for r in rows:
            out.setdefault(r["listing_id"], []).append(r["phash"])
        if len(rows) < page_size:
            return out
        last_id, last_index = rows[-1]["listing_id"], rows[-1]["image_index"]


def load_properties(conn: Any, ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = sorted(set(ids))
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(ids), 50):  # D1 allows 100 bound parameters; AGENTS.md says 50
        chunk = ids[i : i + 50]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            "SELECT id, source, url, price_usd, area_m2, floor, total_floors, district"
            f" FROM properties WHERE id IN ({marks})",
            tuple(chunk),
        ).fetchall()
        for r in rows:
            out[r["id"]] = dict(r)
    return out


def flat_hashes(
    post: List[str], per_hash: Dict[str, List[str]], rows: Dict[str, Dict[str, Any]]
) -> Tuple[List[str], int]:
    """The post hashes to count, and how many were dropped.

    A photo found in the listings of two different districts is a logo or a render: a flat is
    in one district.
    """
    kept: List[str] = []
    dropped = 0
    for h in post:
        districts = {
            rows[lid].get("district")
            for lid in per_hash.get(h, [])
            if lid in rows and rows[lid].get("district")
        }
        if len(districts) >= 2:
            dropped += 1
        else:
            kept.append(h)
    return kept, dropped


def photo_report(
    post_count: int,
    scanned: int,
    shown: List[Dict[str, Any]],
    counts: Dict[str, int],
    dropped: int,
) -> Tuple[str, List[Dict[str, Any]]]:
    """The follow-up text and its candidates, in the order of the numbered lines."""
    lines = [f"📸 <b>ფოტოებით შედარება</b> ({post_count} ფოტო)", ""]
    candidates: List[Dict[str, Any]] = []
    if shown:
        lines.append("✅ <b>იგივე ფოტოები:</b>")
        for n, row in enumerate(shown, 1):
            src = "MyHome" if row.get("source") == "myhome" else "SS.ge"
            parts = [
                f'{n}. <a href="{row.get("url") or "#"}">{src}</a>'
                f" — <b>${int(float(row.get('price_usd') or 0)):,}</b>"
            ]
            if row.get("area_m2"):
                parts.append(f"{float(row['area_m2']):g} მ²")
            floor = _clean_floor(row.get("floor"))
            if floor:
                total = row.get("total_floors")
                parts.append(f"{floor}{f'/{total}' if total else ''} სართ.")
            if row.get("district"):
                parts.append(str(row["district"]))
            parts.append(f"ემთხვევა {counts.get(row['id'], 0)} ფოტო")
            lines.append(" | ".join(parts))
            candidates.append({**snapshot_fields(row), "photo_matches": counts.get(row["id"], 0)})
        lines += ["", "მონიშნე ქვემოთ: ✅ იგივე ბინაა, ❌ სხვაა, 🤷 ვერ ვხვდები."]
    else:
        lines.append("ℹ️ ჩვენს ბაზაში ამ ფოტოებით განცხადება ვერ მოიძებნა.")
    if dropped:
        lines.append(
            f"ℹ️ {dropped} ფოტო სხვადასხვა უბნის განცხადებებშია (ლოგო ან რენდერი), "
            "ამიტომ არ ჩავთვალე."
        )
    lines.append(
        f"ℹ️ შევადარე ჩვენი ბაზის {scanned} განცხადების ფოტოებს (ფოტოები ყველა განცხადებას არ აქვს)."
    )
    return "\n".join(lines), candidates


def _caption_flat(conn: Any, batch_id: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT caption FROM xray_photo_batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    caption = row["caption"] if row else None
    parsed = parse_xray_input(caption) if caption else None
    return parsed if parsed and parsed.get("kind") == "fb_text" else {}


async def lookup_photos(conn: Any, batch_id: str, download: Download) -> Dict[str, Any]:
    post = await batch_hashes(conn, batch_id, download)
    if not post:
        return {"ok": False, "rows": [], "full": False}
    listing_hashes = load_listing_hashes(conn)
    per_hash = {
        h: [
            lid
            for lid, hs in listing_hashes.items()
            if any(hamming(h, x) <= HASH_THRESHOLD for x in hs)
        ]
        for h in post
    }
    matched_ids = sorted({lid for ids in per_hash.values() for lid in ids})
    rows = load_properties(conn, matched_ids)
    kept, dropped = flat_hashes(post, per_hash, rows)
    counts = {lid: count_image_matches(kept, listing_hashes[lid]) for lid in matched_ids}
    counts = {lid: n for lid, n in counts.items() if n >= 1 and lid in rows}
    flat = _caption_flat(conn, batch_id)
    confirmed = [
        lid
        for lid in counts
        if is_photo_duplicate(
            counts[lid],
            flat.get("price_usd"),
            rows[lid].get("price_usd"),
            flat.get("area_m2"),
            rows[lid].get("area_m2"),
        )
    ]
    confirmed.sort(key=lambda lid: (-counts[lid], float(rows[lid].get("price_usd") or 0), lid))
    shown = [rows[lid] for lid in confirmed[:PHOTO_MAX_SHOWN]]
    text, candidates = photo_report(len(post), len(listing_hashes), shown, counts, dropped)
    return {
        "ok": True,
        "rows": shown,
        "full": False,
        "result": {"text": text, "candidates": candidates, "target": {"photo_batch": batch_id}},
    }
