#!/usr/bin/env python3
"""Measures how well the photo hash (core/dedup.py dHash) finds an edited copy of a photo.

It takes the first photo of recent listings, makes the edits a re-poster makes (mirror, crop,
watermark, Telegram-like recompression), and counts how often the edited copy is still within
HASH_THRESHOLD of its original (recall), and how often photos of different listings are
(false matches). Two methods are compared: the hash as it is today, and the same hash that
also tries the mirrored query.

Read-only: nothing is written to the database. Nothing prints a photo link or a listing id.

Usage:
    python scripts/photo_benchmark.py [--limit N]
"""

import argparse
import asyncio
import json
import os
import sys
from io import BytesIO
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dedup import HASH_THRESHOLD, MAX_IMAGE_BYTES, current_photo_url, dhash, hamming  # noqa: E402

BENCHMARK_LISTINGS = 300
TELEGRAM_MAX_SIDE = 1280  # Telegram's longer side for a sent photo (ADR 0005)

Photo = Tuple[Optional[str], Image.Image]  # (group_id, picture)


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


EDITS: Dict[str, Callable[[Image.Image], Image.Image]] = {
    "telegram": _telegram,
    "mirror": lambda img: _jpeg(ImageOps.mirror(img)),
    "crop 5%": _crop_center(0.05),
    "crop 10%": _crop_center(0.10),
    "crop 20%": _crop_center(0.20),
    "crop bottom 10%": _crop_bottom,
    "watermark": _watermark,
}


def _plain(stored: str, query: Image.Image) -> int:
    return hamming(stored, dhash(query))


def _with_mirror(stored: str, query: Image.Image) -> int:
    return min(hamming(stored, dhash(query)), hamming(stored, dhash(ImageOps.mirror(query))))


METHODS: Dict[str, Callable[[str, Image.Image], int]] = {
    "dHash": _plain,
    "dHash + mirrored query": _with_mirror,
}


def evaluate(photos: List[Photo], threshold: int = HASH_THRESHOLD) -> Dict[str, Dict[str, float]]:
    """Per method: the share of edited copies found, per edit, and under "false matches" the
    share of pairs of photos from different listings that the method calls the same photo.

    Two listings in one photo group are one flat, so their pair is not counted."""
    stored = [dhash(img) for _, img in photos]
    pairs = [
        (i, j)
        for i in range(len(photos))
        for j in range(i + 1, len(photos))
        if not (photos[i][0] and photos[i][0] == photos[j][0])
    ]
    report: Dict[str, Dict[str, float]] = {}
    for name, distance in METHODS.items():
        row: Dict[str, float] = {}
        for edit_name, edit in EDITS.items():
            found = sum(distance(h, edit(img)) <= threshold for h, (_, img) in zip(stored, photos))
            row[edit_name] = found / len(photos)
        false = sum(distance(stored[i], photos[j][1]) <= threshold for i, j in pairs)
        row["false matches"] = false / len(pairs) if pairs else 0.0
        report[name] = row
    return report


def format_report(report: Dict[str, Dict[str, float]], photo_count: int) -> str:
    columns = [*EDITS, "false matches"]
    lines = [
        f"Photo hash benchmark: {photo_count} photos, threshold {HASH_THRESHOLD}",
        "",
        "| method | " + " | ".join(columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]
    for name, row in report.items():
        cells = [f"{row[c]:.2%}" if c == "false matches" else f"{row[c]:.0%}" for c in columns]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _first_url(images_json: Optional[str]) -> Optional[str]:
    try:
        urls = json.loads(images_json or "[]")
    except ValueError:
        return None
    return str(urls[0]) if isinstance(urls, list) and urls else None


async def load_photos(db, limit: int) -> List[Photo]:
    """The first photo of the newest `limit` listings that have one. One bounded read."""
    from curl_cffi.requests import AsyncSession

    with db._connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT group_id, images_json FROM properties"
            " WHERE images_json IS NOT NULL AND images_json NOT IN ('', '[]')"
            " ORDER BY scraped_at DESC LIMIT ?",
            (limit,),
        )
        rows = [(r["group_id"], _first_url(r["images_json"])) for r in cursor.fetchall()]

    sem = asyncio.Semaphore(3)

    async def fetch(session, group_id, url) -> Optional[Photo]:
        async with sem:
            try:
                resp = await session.get(current_photo_url(url), timeout=10)
                if resp.status_code == 200 and 1000 < len(resp.content) <= MAX_IMAGE_BYTES:
                    img = Image.open(BytesIO(resp.content))
                    img.load()
                    return group_id, img
            except Exception:
                pass
            return None

    async with AsyncSession(impersonate="chrome124") as session:
        got = await asyncio.gather(*(fetch(session, g, u) for g, u in rows if u))
    return [p for p in got if p]


def main():
    parser = argparse.ArgumentParser(description="Benchmark the photo hash against edited copies.")
    parser.add_argument(
        "--limit",
        type=int,
        default=BENCHMARK_LISTINGS,
        help=f"Listings to take a photo from (default {BENCHMARK_LISTINGS})",
    )
    args = parser.parse_args()

    from core.database import DatabaseEngine

    photos = asyncio.run(load_photos(DatabaseEngine(), args.limit))
    if len(photos) < 2:
        print("Fewer than 2 photos could be downloaded: nothing to measure.")
        sys.exit(1)
    text = format_report(evaluate(photos), len(photos))
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
