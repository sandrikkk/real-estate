"""Photo match for the X-Ray (ADR 0005): a post's photos against the photos of the listings
in the database.

The Worker stores the photos a user sends (D1 `xray_photos`, one batch per album) and queues a
"photo" job. This module runs in that job (scripts/xray_lookup.py): it hashes the photos with
the duplicate finder's own dHash, compares them with every hash in `image_hashes`, applies the
duplicate finder's own rule (two photos, or one photo that price and area confirm), and builds
the follow-up text with its candidates for the ✅/❌ buttons.

The threshold and the rule are core/dedup.py's. One thing is new and needs no number: a photo
that is found in the listings of two different districts is a logo or a render, because a flat
is in one district, so it is not counted. Two names of one district (an alias in core/xray.py
DISTRICT_ALIASES) count as one district.

Nothing here prints a batch id (it holds the chat id) or a photo link.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from core.dedup import (
    HASH_THRESHOLD,
    _decode_dhash_sync,
    count_image_matches,
    hamming,
    is_photo_duplicate,
)
from core.xray import _clean_floor, _expand_district_aliases, parse_xray_input, snapshot_fields

if TYPE_CHECKING:
    from core.owner_search import Sources

# Budgets, not matching thresholds: how many listings to show, how many hash rows to read per
# D1 request, and how long to wait so that every photo of an album has reached D1.
PHOTO_MAX_SHOWN = 5
HASH_PAGE_SIZE = 5000
ALBUM_SETTLE_SECONDS = 10
# How many MyHome and SS.ge listings the live part downloads photos of: a work cap, the owner
# search's own (core/owner_search.py MAX_PHOTO_CANDIDATES).
LIVE_MAX_HASHED = 24

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


def _districts_conflict(districts: Iterable[str]) -> bool:
    """Two of these names are different districts: neither is in the other's alias list."""
    names = sorted({str(d).strip() for d in districts if d and str(d).strip()})
    return any(
        b not in _expand_district_aliases(a) and a not in _expand_district_aliases(b)
        for i, a in enumerate(names)
        for b in names[i + 1 :]
    )


def flat_hashes(
    post: List[str], per_hash: Dict[str, List[str]], rows: Dict[str, Dict[str, Any]]
) -> Tuple[List[str], int]:
    """The post hashes to count, and how many were dropped.

    A photo found in the listings of two different districts is a logo or a render: a flat is
    in one district. Aliases of one district are one district.
    """
    kept: List[str] = []
    dropped = 0
    for h in post:
        districts = {
            rows[lid].get("district")
            for lid in per_hash.get(h, [])
            if lid in rows and rows[lid].get("district")
        }
        if _districts_conflict(districts):
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
    live: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """The follow-up text and its candidates, in the order of the numbered lines. `live` is
    live_matches' answer, or None when the portals were not part of this search."""
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
    elif live and live["asked"]:
        lines.append("ℹ️ ამ ფოტოებით განცხადება ვერ მოიძებნა.")
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
    if live is not None:
        lines.append(_live_line(live))
    return "\n".join(lines), candidates


def _live_line(live: Dict[str, Any]) -> str:
    if not live["asked"]:
        return "ℹ️ myhome-სა და ss.ge-ზეც რომ მოვძებნო, ფოტოებს წარწერად დაურთე უბანი და ფართი."
    names = {"myhome": "myhome", "ss.ge": "ss.ge"}
    failed = [names[f] for f in live["failed"]]
    line = f"ℹ️ myhome-სა და ss.ge-ზე ახლა შევამოწმე {live['checked']} განცხადების ფოტოები."
    if failed:
        line += f" {' და '.join(failed)} ახლა ვერ წავიკითხე."
    return line


def _caption(conn: Any, batch_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT caption FROM xray_photo_batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    return row["caption"] if row else None


def _caption_flat(conn: Any, batch_id: str) -> Dict[str, Any]:
    caption = _caption(conn, batch_id)
    parsed = parse_xray_input(caption) if caption else None
    return parsed if parsed and parsed.get("kind") == "fb_text" else {}


def _gap(a: Any, b: Any) -> float:
    try:
        a, b = float(a or 0), float(b or 0)
    except (TypeError, ValueError):
        return float("inf")
    return abs(a - b) if a > 0 and b > 0 else float("inf")


async def live_matches(
    flat: Dict[str, Any], kept: List[str], shown_ids: set, sources: "Sources"
) -> Dict[str, Any]:
    """The post's photos against MyHome's and SS.ge's listings of the caption's district and
    area window, from every seller: the owner search's queries without its owner filter.

    Asks nothing without a district. Downloads the first photos of at most LIVE_MAX_HASHED
    candidates, the closest area (then price) first, skipping listings already shown. Shows a
    listing by the duplicate finder's rule (core/dedup.py is_photo_duplicate)."""
    from core.owner_search import _image_urls, _myhome_candidates, _ssge_candidates

    out: Dict[str, Any] = {"rows": [], "counts": {}, "checked": 0, "failed": [], "asked": False}
    if not flat.get("district") or not kept or sources.image_hashes is None:
        return out
    out["asked"] = True
    (myhome, _), (ssge, _) = await asyncio.gather(
        _myhome_candidates(flat, sources, owners_only=False),
        _ssge_candidates(flat, sources, owners_only=False),
    )
    out["failed"] = [name for name, rows in (("myhome", myhome), ("ss.ge", ssge)) if rows is None]
    pool: Dict[str, Dict[str, Any]] = {}
    for c in [*(myhome or []), *(ssge or [])]:
        if c.get("id") and c["id"] not in shown_ids and c["id"] not in pool and _image_urls(c):
            pool[c["id"]] = c
    ranked = sorted(
        pool.values(),
        key=lambda c: (
            _gap(c.get("area_m2"), flat.get("area_m2")),
            _gap(c.get("price_usd"), flat.get("price_usd")),
            c["id"],
        ),
    )[:LIVE_MAX_HASHED]
    sem = asyncio.Semaphore(4)

    async def _hashes(c: Dict[str, Any]) -> List[str]:
        async with sem:
            return (await sources.image_hashes(_image_urls(c)[:3])) or []

    all_hashes = await asyncio.gather(*(_hashes(c) for c in ranked))
    out["checked"] = len(ranked)
    for c, hashes in zip(ranked, all_hashes):
        n = count_image_matches(kept, hashes)
        if n >= 1 and is_photo_duplicate(
            n, flat.get("price_usd"), c.get("price_usd"), flat.get("area_m2"), c.get("area_m2")
        ):
            out["rows"].append(c)
            out["counts"][c["id"]] = n
    return out


async def lookup_photos(
    conn: Any, batch_id: str, download: Download, sources: Optional["Sources"] = None
) -> Dict[str, Any]:
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
    found = [rows[lid] for lid in confirmed]
    live = None
    if sources is not None:
        live_flat = dict(flat)
        if "ქირავ" in (_caption(conn, batch_id) or ""):
            live_flat["deal_type"] = "rent"
        live = await live_matches(live_flat, kept, set(confirmed), sources)
        found += live["rows"]
        counts = {**counts, **live["counts"]}
    found.sort(key=lambda r: (-counts[r["id"]], float(r.get("price_usd") or 0), r["id"]))
    shown = found[:PHOTO_MAX_SHOWN]
    text, candidates = photo_report(len(post), len(listing_hashes), shown, counts, dropped, live)
    return {
        "ok": True,
        "rows": shown,
        "full": False,
        "result": {"text": text, "candidates": candidates, "target": {"photo_batch": batch_id}},
    }
