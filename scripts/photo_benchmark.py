#!/usr/bin/env python3
"""Measures how well the photo hash (core/dedup.py dHash) finds an edited copy of a photo.

Four parts, all read-only:

1. Edits. The first photo of recent listings gets the edits a re-poster makes (mirror, crop,
   watermark, Telegram-like recompression). For each method and threshold: the share of edited
   copies still within the threshold of the original, and the share of pairs of photos from
   different listings that match (false matches).
2. Mirrored hash. A mirrored photo's dHash can be computed from the stored one (each row's bits
   reversed and inverted). This part measures how far that is from hashing the mirrored photo.
3. Search. An album of up to 3 photos of a listing, edited, is searched against every stored
   hash with the X-Ray photo rule (core/photo_match.py): is the listing found, and how many
   other listings are shown, with and without a caption giving price and area.
4. Mirrored re-posts. Every pair of listings in the database whose photos match only once one
   side is mirrored: how often re-posters mirror photos.

Nothing is written to the database. Nothing prints a photo link or a listing id.

Usage:
    python scripts/photo_benchmark.py [--limit N] [--albums N]
"""

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dedup import (  # noqa: E402
    HASH_THRESHOLD,
    MAX_IMAGE_BYTES,
    current_photo_url,
    dhash,
    hamming,
    is_photo_duplicate,
)

BENCHMARK_LISTINGS = 300
SEARCH_ALBUMS = 100
ALBUM_PHOTOS = 3  # listings have their first 3 photos hashed (core/dedup.py)
THRESHOLDS = (HASH_THRESHOLD, 12, 14)
TELEGRAM_MAX_SIDE = 1280  # Telegram's longer side for a sent photo (ADR 0005)
PAGE_SIZE = 5000

# One listing: (listing id, photo group id, its first photos).
Listing = Tuple[str, Optional[str], List[Image.Image]]


# --- Edits ---


def _jpeg(img: Image.Image, quality: int = 85) -> Image.Image:
    """The picture after a JPEG save, as any re-posted copy is."""
    buf = BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return Image.open(BytesIO(buf.getvalue()))


def _telegram(img: Image.Image) -> Image.Image:
    copy = img.copy()
    copy.thumbnail((TELEGRAM_MAX_SIDE, TELEGRAM_MAX_SIDE))
    return _jpeg(copy, quality=80)


def _crop_center(share: float) -> Callable[[Image.Image], Image.Image]:
    """Cuts `share` of the width and of the height away, evenly from both sides."""

    def crop(img: Image.Image) -> Image.Image:
        w, h = img.size
        dx, dy = int(w * share / 2), int(h * share / 2)
        return _jpeg(img.crop((dx, dy, w - dx, h - dy)))

    return crop


def _crop_bottom(img: Image.Image) -> Image.Image:
    """Cuts the bottom tenth away, where portals put their logo."""
    w, h = img.size
    return _jpeg(img.crop((0, 0, w, int(h * 0.9))))


def _watermark(img: Image.Image) -> Image.Image:
    """A semi-transparent band across the middle and a solid logo box in a corner."""
    base = img.convert("RGBA")
    w, h = base.size
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rectangle(
        (int(w * 0.2), int(h * 0.45), int(w * 0.8), int(h * 0.55)), fill=(255, 255, 255, 110)
    )
    draw.rectangle((int(w * 0.78), int(h * 0.88), w, h), fill=(255, 255, 255, 255))
    return _jpeg(Image.alpha_composite(base, layer))


def _mirror(img: Image.Image) -> Image.Image:
    return _jpeg(ImageOps.mirror(img))


EDITS: Dict[str, Callable[[Image.Image], Image.Image]] = {
    "telegram": _telegram,
    "mirror": _mirror,
    "crop 5%": _crop_center(0.05),
    "crop 10%": _crop_center(0.10),
    "crop 20%": _crop_center(0.20),
    "crop bottom 10%": _crop_bottom,
    "watermark": _watermark,
}

SEARCH_EDITS = ("telegram", "mirror", "crop 10%", "crop bottom 10%")


# --- Hashes ---


def mirror_hash(h: str) -> str:
    """The dHash of the mirrored photo, from the photo's own dHash.

    dHash bit (row, col) says pixel col is brighter than pixel col + 1. Mirroring reverses the
    row, so the bit becomes the opposite comparison at col 7 - col. Equal pixels are the only
    case where this differs from hashing the mirrored photo."""
    bits = int(h, 16)
    out = 0
    for r in range(8):
        row = (bits >> (8 * (7 - r))) & 0xFF
        out = (out << 8) | (int(f"{row:08b}"[::-1], 2) ^ 0xFF)
    return f"{out:016x}"


def _query_hashes(img: Image.Image, mirrored: bool) -> List[str]:
    """The hashes a method compares for one query photo."""
    h = dhash(img)
    return [h, mirror_hash(h)] if mirrored else [h]


METHODS: Dict[str, bool] = {"dHash": False, "dHash + mirrored query": True}


def _distance(stored: str, query: List[str]) -> int:
    return min(hamming(stored, q) for q in query)


def _to_u64(hashes: List[str]) -> np.ndarray:
    return np.array([int(h, 16) for h in hashes], dtype=np.uint64)


def _popcount(x: np.ndarray) -> np.ndarray:
    return np.bitwise_count(x)


# --- 1. Edits ---


def evaluate(listings: List[Listing]) -> Dict[str, Dict[str, float]]:
    """Per method and threshold ("dHash ≤ 10"): the share of edited copies found, per edit,
    and under "false matches" the share of pairs of first photos from different listings that
    the method calls the same photo. Two listings in one photo group are one flat, so their
    pair is not counted."""
    firsts = [(group, photos[0]) for _, group, photos in listings]
    stored = [dhash(img) for _, img in firsts]
    found: Dict[Tuple[str, str], List[int]] = {}
    pair_dist: Dict[str, List[int]] = {}
    for method, mirrored in METHODS.items():
        for edit_name, edit in EDITS.items():
            found[(method, edit_name)] = [
                _distance(h, _query_hashes(edit(img), mirrored))
                for h, (_, img) in zip(stored, firsts)
            ]
        queries = [_query_hashes(img, mirrored) for _, img in firsts]
        pair_dist[method] = [
            _distance(stored[i], queries[j])
            for i in range(len(firsts))
            for j in range(i + 1, len(firsts))
            if not (firsts[i][0] and firsts[i][0] == firsts[j][0])
        ]
    report: Dict[str, Dict[str, float]] = {}
    for method in METHODS:
        for t in THRESHOLDS:
            row = {
                edit_name: sum(d <= t for d in found[(method, edit_name)]) / len(firsts)
                for edit_name in EDITS
            }
            pairs = pair_dist[method]
            row["false matches"] = sum(d <= t for d in pairs) / len(pairs) if pairs else 0.0
            report[f"{method} ≤ {t}"] = row
    return report


def format_report(report: Dict[str, Dict[str, float]], photo_count: int) -> str:
    columns = [*EDITS, "false matches"]
    lines = [
        f"### 1. Edits: {photo_count} photos",
        "",
        "| method | " + " | ".join(columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]
    for name, row in report.items():
        cells = [f"{row[c]:.2%}" if c == "false matches" else f"{row[c]:.0%}" for c in columns]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


# --- 2. Mirrored hash ---


def mirror_hash_check(listings: List[Listing]) -> str:
    gaps = [
        hamming(mirror_hash(dhash(photos[0])), dhash(ImageOps.mirror(photos[0])))
        for _, _, photos in listings
    ]
    within = sum(g <= 3 for g in gaps) / len(gaps)
    return (
        "### 2. Mirrored hash computed from the stored hash\n\n"
        f"Bits apart from hashing the mirrored photo: mean {sum(gaps) / len(gaps):.1f}, "
        f"max {max(gaps)}, at most 3 bits for {within:.0%} of {len(gaps)} photos."
    )


# --- 3. Search ---


class HashIndex:
    """Every stored hash with its listing, and the listing facts the photo rule reads."""

    def __init__(self, listing_hashes: Dict[str, List[str]], facts: Dict[str, Dict]):
        self.ids = [lid for lid, hs in listing_hashes.items() for _ in hs]
        self.hashes = [h for hs in listing_hashes.values() for h in hs]
        self.u64 = _to_u64(self.hashes)
        self.listing_hashes = listing_hashes
        self.facts = facts

    def hits(self, query: List[str], threshold: int) -> List[int]:
        """Indexes of the stored hashes within `threshold` of any of the query hashes."""
        mask = np.zeros(len(self.u64), dtype=bool)
        for q in _to_u64(query):
            mask |= _popcount(self.u64 ^ q) <= threshold
        return np.nonzero(mask)[0].tolist()


def _matches(post: List[List[str]], stored: List[str], threshold: int) -> int:
    """core/dedup.py count_image_matches, with each post photo given as its query hashes:
    each post photo and each stored photo counts once."""
    used = set()
    count = 0
    for query in post:
        for j, h in enumerate(stored):
            if j not in used and _distance(h, query) <= threshold:
                count += 1
                used.add(j)
                break
    return count


def search(
    index: HashIndex,
    post: List[List[str]],
    caption: Optional[Dict],
    threshold: int = HASH_THRESHOLD,
) -> List[str]:
    """The listings core/photo_match.lookup_photos would show for this album: a post photo
    found in two districts is dropped (logo or render), then core/dedup.py's rule."""
    kept: List[List[str]] = []
    candidates = set()
    for query in post:
        ids = {index.ids[i] for i in index.hits(query, threshold)}
        districts = {index.facts[lid].get("district") for lid in ids if lid in index.facts}
        districts.discard(None)
        districts.discard("")
        if len(districts) < 2:
            kept.append(query)
            candidates |= ids
    caption = caption or {}
    shown = []
    for lid in candidates:
        if lid not in index.facts:
            continue
        n = _matches(kept, index.listing_hashes[lid], threshold)
        row = index.facts[lid]
        if n >= 1 and is_photo_duplicate(
            n,
            caption.get("price_usd"),
            row.get("price_usd"),
            caption.get("area_m2"),
            row.get("area_m2"),
        ):
            shown.append(lid)
    return shown


def evaluate_search(index: HashIndex, albums: List[Listing]) -> str:
    lines = [
        f"### 3. Search: {len(albums)} albums against {len(index.listing_hashes)} listings"
        f" ({len(index.hashes)} photos), threshold {HASH_THRESHOLD}",
        "",
        "found = the listing is shown; others = other listings shown per search"
        " (same photo group not counted).",
        "",
        "| method | caption | " + " | ".join(f"{e}: found / others" for e in SEARCH_EDITS) + " |",
        "|---|---|" + "---:|" * len(SEARCH_EDITS),
    ]
    edited = {
        edit_name: [[EDITS[edit_name](img) for img in photos] for _, _, photos in albums]
        for edit_name in SEARCH_EDITS
    }
    for method, mirrored in METHODS.items():
        for with_caption in (False, True):
            cells = []
            for edit_name in SEARCH_EDITS:
                found = others = 0
                for (lid, group, _), photos in zip(albums, edited[edit_name]):
                    post = [_query_hashes(img, mirrored) for img in photos]
                    caption = index.facts.get(lid) if with_caption else None
                    shown = search(index, post, caption)
                    found += lid in shown
                    others += sum(
                        1
                        for s in shown
                        if s != lid and not (group and index.facts[s].get("group_id") == group)
                    )
                cells.append(f"{found / len(albums):.0%} / {others / len(albums):.2f}")
            label = "price + area" if with_caption else "none"
            lines.append(f"| {method} | {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


# --- 4. Mirrored re-posts ---


CHUNK = 200  # rows of the all-pairs comparison held in memory at once


def render_mask(index: HashIndex, threshold: int = HASH_THRESHOLD) -> np.ndarray:
    """True for a stored photo found in the listings of two different districts: a logo or a
    render, which the photo search drops (core/photo_match.py flat_hashes)."""
    names = sorted({str(f.get("district")) for f in index.facts.values() if f.get("district")})
    code = {n: i for i, n in enumerate(names)}
    dcode = np.array(
        [code.get(str(index.facts.get(lid, {}).get("district")), -1) for lid in index.ids]
    )
    out = np.zeros(len(index.u64), dtype=bool)
    for start in range(0, len(index.u64), CHUNK):
        near = _popcount(index.u64[start : start + CHUNK, None] ^ index.u64[None, :]) <= threshold
        for r, row in enumerate(near):
            codes = dcode[row]
            out[start + r] = len(np.unique(codes[codes >= 0])) >= 2
    return out


def listing_pair_matches(
    index: HashIndex, mirrored: bool, keep: np.ndarray, threshold: int = HASH_THRESHOLD
) -> Dict[Tuple[str, str], int]:
    """For each pair of different listings, how many photos of the first match a photo of the
    second; with `mirrored`, the first listing's photos are mirrored. Only `keep` photos count."""
    left = _to_u64([mirror_hash(h) for h in index.hashes]) if mirrored else index.u64
    ids = np.array(index.ids)
    counts: Dict[Tuple[str, str], set] = defaultdict(set)
    for start in range(0, len(left), CHUNK):
        near = _popcount(left[start : start + CHUNK, None] ^ index.u64[None, :]) <= threshold
        near &= keep[None, :]
        near &= keep[start : start + CHUNK, None]
        for i, j in zip(*np.nonzero(near)):
            a, b = ids[start + i], ids[j]
            if a != b:
                counts[(a, b)].add(start + i)
    return {pair: len(photos) for pair, photos in counts.items()}


def mirrored_reposts(index: HashIndex) -> str:
    """Listing pairs that share 2+ photos plainly, and pairs that share 2+ photos only once
    one side is mirrored. A pair in one photo group is already known to be one flat."""

    def same_group(a: str, b: str) -> bool:
        ga = index.facts.get(a, {}).get("group_id")
        return bool(ga) and ga == index.facts.get(b, {}).get("group_id")

    keep = ~render_mask(index)
    plain = listing_pair_matches(index, mirrored=False, keep=keep)
    flipped = listing_pair_matches(index, mirrored=True, keep=keep)
    plain_pairs = {tuple(sorted(p)) for p, n in plain.items() if n >= 2}
    mirror_only = {
        tuple(sorted(p))
        for p, n in flipped.items()
        if n >= 2 and tuple(sorted(p)) not in plain_pairs
    }
    plain_new = sum(1 for a, b in plain_pairs if not same_group(a, b))
    return (
        f"### 4. Mirrored re-posts among {len(index.listing_hashes)} listings, threshold "
        f"{HASH_THRESHOLD}\n\n"
        f"| listing pairs sharing 2+ photos | count |\n|---|---:|\n"
        f"| as they are | {len(plain_pairs)} |\n"
        f"| as they are, not in one photo group | {plain_new} |\n"
        f"| only when one side is mirrored | {len(mirror_only)} |\n\n"
        f"{int((~keep).sum())} photos found in two districts (logos, renders) are left out."
    )


# --- Loading ---


def _first_urls(images_json: Optional[str]) -> List[str]:
    try:
        urls = json.loads(images_json or "[]")
    except ValueError:
        return []
    return [str(u) for u in urls[:ALBUM_PHOTOS]] if isinstance(urls, list) else []


async def load_listings(db, limit: int) -> List[Listing]:
    """The first photos of the newest `limit` listings that have photos. One bounded read."""
    from curl_cffi.requests import AsyncSession

    with db._connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, group_id, images_json FROM properties"
            " WHERE images_json IS NOT NULL AND images_json NOT IN ('', '[]')"
            " ORDER BY scraped_at DESC LIMIT ?",
            (limit,),
        )
        rows = [(r["id"], r["group_id"], _first_urls(r["images_json"])) for r in cursor.fetchall()]

    sem = asyncio.Semaphore(3)

    async def fetch(session, url) -> Optional[Image.Image]:
        async with sem:
            try:
                resp = await session.get(current_photo_url(url), timeout=10)
                if resp.status_code == 200 and 1000 < len(resp.content) <= MAX_IMAGE_BYTES:
                    img = Image.open(BytesIO(resp.content))
                    img.load()
                    return img
            except Exception:
                pass
            return None

    async with AsyncSession(impersonate="chrome124") as session:

        async def one(lid, group, urls) -> Optional[Listing]:
            if not urls:
                return None
            # The first photo decides: the edits part compares first photos.
            first = await fetch(session, urls[0])
            if first is None:
                return None
            rest = await asyncio.gather(*(fetch(session, u) for u in urls[1:]))
            return lid, group, [first, *(img for img in rest if img)]

        got = await asyncio.gather(*(one(*r) for r in rows))
    return [g for g in got if g]


def load_index(db) -> HashIndex:
    """Every stored hash, and the facts of the listings that have one. Keyset paging on the
    primary key: run once per benchmark, never in a loop."""
    from core.photo_match import load_listing_hashes

    with db._connection() as conn:
        listing_hashes = load_listing_hashes(conn, page_size=PAGE_SIZE)
        facts: Dict[str, Dict] = {}
        last = ""
        while True:
            rows = conn.execute(
                "SELECT p.id, p.district, p.group_id, p.price_usd, p.area_m2 FROM properties p"
                " WHERE p.id > ? AND EXISTS (SELECT 1 FROM image_hashes h"
                " WHERE h.listing_id = p.id) ORDER BY p.id LIMIT ?",
                (last, PAGE_SIZE),
            ).fetchall()
            for r in rows:
                facts[r["id"]] = dict(r)
            if len(rows) < PAGE_SIZE:
                break
            last = rows[-1]["id"]
    return HashIndex(listing_hashes, facts)


def main():
    parser = argparse.ArgumentParser(description="Benchmark the photo hash against edited copies.")
    parser.add_argument(
        "--limit",
        type=int,
        default=BENCHMARK_LISTINGS,
        help=f"Listings to take photos from (default {BENCHMARK_LISTINGS})",
    )
    parser.add_argument(
        "--albums",
        type=int,
        default=SEARCH_ALBUMS,
        help=f"Albums to search with (default {SEARCH_ALBUMS})",
    )
    args = parser.parse_args()

    from core.database import DatabaseEngine

    db = DatabaseEngine()
    listings = asyncio.run(load_listings(db, args.limit))
    if len(listings) < 2:
        print("Fewer than 2 photos could be downloaded: nothing to measure.")
        sys.exit(1)
    index = load_index(db)
    # An album can only be found if its listing's photos are stored.
    albums = [
        x
        for x in listings
        if x[0] in index.listing_hashes and x[0] in index.facts and len(x[2]) >= 2
    ][: args.albums]

    parts = [
        f"## Photo hash benchmark (threshold today: {HASH_THRESHOLD})",
        format_report(evaluate(listings), len(listings)),
        mirror_hash_check(listings),
        evaluate_search(index, albums) if albums else "### 3. Search: no album to search with",
        mirrored_reposts(index),
    ]
    text = "\n\n".join(parts)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
