"""
Duplicate Listing & Agent Network Detector for Georgian real estate portals.

Architecture:
- SQL-based candidate lookup (no in-memory state beyond phone index)
- Lazy image hashing: only downloads images when ≥2 candidates exist in a block
- Custom dHash using Pillow only (no ImageHash/scipy/numpy dependency)
- Group ID = first listing's ID; groups merge when new listing bridges them
- Matching: ≥2 photo matches, or 1 photo + price/area confirmation
"""

import json
from collections import defaultdict
from io import BytesIO
from typing import Dict, List, Optional, Set

from PIL import Image
from curl_cffi.requests import AsyncSession

from core.database import DatabaseEngine
from core.models import PropertyListing


# --- dHash (Pillow only, 64-bit) ---


def dhash(img: Image.Image, size: int = 8) -> str:
    """Difference hash. Returns 16-char hex string (64 bits)."""
    px = list(img.convert("L").resize((size + 1, size)).getdata())
    bits = 0
    for r in range(size):
        for c in range(size):
            i = r * (size + 1) + c
            bits = (bits << 1) | (px[i] > px[i + 1])
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    """Hamming distance between two hex hash strings."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# --- Image downloading ---


async def _download_and_hash(url: str, session: AsyncSession, timeout: int = 10) -> Optional[str]:
    """Downloads image, returns dHash hex string or None."""
    try:
        resp = await session.get(url, timeout=timeout)
        if resp.status_code == 200 and len(resp.content) > 1000:
            return dhash(Image.open(BytesIO(resp.content)))
    except Exception:
        pass
    return None


async def _compute_hashes(image_urls: List[str], max_images: int = 3) -> List[str]:
    """Downloads first N images via single session, returns list of dHash hex strings."""
    hashes = []
    try:
        async with AsyncSession(impersonate="chrome124") as session:
            for url in image_urls[:max_images]:
                h = await _download_and_hash(url, session)
                if h:
                    hashes.append(h)
    except Exception:
        pass
    return hashes


class DuplicateDetector:
    """
    SQL-based duplicate detector. No in-memory group state.

    Pipeline:
    1. SQL blocking query: district + rooms + area (±15%) + optional floor/total_floors
    2. Lazy image dHash comparison within candidates
    3. Match criteria: ≥2 photos match OR 1 photo + price/area similar
    4. Group assignment: group_id = first listing's ID, with merge support
    """

    def __init__(self, db: DatabaseEngine, hash_threshold: int = 10):
        self.db = db
        self.threshold = hash_threshold
        # Phone -> listing IDs (for agent network analysis)
        self._phone_index: Dict[str, Set[str]] = defaultdict(set)
        self._load_phone_index()

    def _load_phone_index(self):
        """Populates phone -> listing_ids from DB."""
        with self.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, phone_number FROM properties "
                "WHERE phone_number IS NOT NULL AND phone_number != ''"
            )
            for row in cursor.fetchall():
                self._phone_index[row["phone_number"]].add(row["id"])
        total = sum(len(v) for v in self._phone_index.values())
        if total:
            print(
                f"[Dedup]: Loaded phone index ({len(self._phone_index)} phones, {total} listings)"
            )

    # --- SQL candidate lookup ---

    def _find_candidates(self, listing: PropertyListing) -> List[dict]:
        """Finds candidate duplicates via SQL blocking: district + rooms + area range."""
        if not listing.district or listing.area_m2 <= 0:
            return []

        # ±15% area tolerance for agent manipulation
        area_lo = listing.area_m2 * 0.85
        area_hi = listing.area_m2 * 1.15

        with self.db._connection() as conn:
            cursor = conn.cursor()
            sql = """
                SELECT id, source, price_usd, area_m2, url, phone_number,
                       floor, rooms, total_floors, images_json, group_id,
                       published_at
                FROM properties
                WHERE id != ? AND district = ? AND area_m2 BETWEEN ? AND ?
            """
            params: list = [listing.id, listing.district, area_lo, area_hi]

            if listing.rooms:
                sql += " AND (rooms = ? OR rooms IS NULL)"
                params.append(listing.rooms)

            if listing.total_floors:
                sql += " AND (total_floors = ? OR total_floors IS NULL)"
                params.append(listing.total_floors)

            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    # --- Image hash persistence ---

    def _get_hashes(self, listing_id: str) -> List[str]:
        """Loads cached dHash hex strings from DB."""
        with self.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT phash FROM image_hashes WHERE listing_id = ? ORDER BY image_index",
                (listing_id,),
            )
            return [row["phash"] for row in cursor.fetchall()]

    def _save_hashes(self, listing_id: str, hashes: List[str]):
        """Saves dHash hex strings to DB."""
        if not hashes:
            return
        with self.db._connection() as conn:
            cursor = conn.cursor()
            for idx, h in enumerate(hashes):
                cursor.execute(
                    "INSERT OR REPLACE INTO image_hashes (listing_id, image_index, phash) "
                    "VALUES (?, ?, ?)",
                    (listing_id, idx, h),
                )
            conn.commit()

    async def _ensure_hashes(
        self,
        listing_id: str,
        images: Optional[List[str]] = None,
        images_json: Optional[str] = None,
    ) -> List[str]:
        """Load hashes from DB cache, or compute lazily and persist."""
        hashes = self._get_hashes(listing_id)
        if hashes:
            return hashes

        urls = images or []
        if not urls and images_json:
            try:
                urls = json.loads(images_json or "[]")
            except Exception:
                urls = []

        if urls:
            hashes = await _compute_hashes(urls, max_images=3)
            self._save_hashes(listing_id, hashes)
        return hashes

    # --- Image matching ---

    def _count_image_matches(self, hashes_a: List[str], hashes_b: List[str]) -> int:
        """Counts unique matching image pairs within Hamming distance threshold."""
        count = 0
        used_b: set = set()
        for ha in hashes_a:
            for j, hb in enumerate(hashes_b):
                if j not in used_b and hamming(ha, hb) <= self.threshold:
                    count += 1
                    used_b.add(j)
                    break
        return count

    # --- Group management ---

    def _assign_group(self, listing_id: str, group_id: str):
        """Sets group_id on a properties row."""
        with self.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE properties SET group_id = ? WHERE id = ?",
                (group_id, listing_id),
            )
            conn.commit()

    def _merge_groups(self, group_ids: Set[str], canonical: str):
        """Merges multiple groups into one canonical group."""
        with self.db._connection() as conn:
            cursor = conn.cursor()
            for old in group_ids:
                if old != canonical:
                    cursor.execute(
                        "UPDATE properties SET group_id = ? WHERE group_id = ?",
                        (canonical, old),
                    )
            conn.commit()

    def _get_group_info(self, group_id: str, current_listing_id: str) -> Optional[dict]:
        """Computes group stats from DB: count, best price, probable owner, is_new_best."""
        with self.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, price_usd, url, phone_number, published_at "
                "FROM properties WHERE group_id = ? ORDER BY price_usd ASC",
                (group_id,),
            )
            rows = [dict(r) for r in cursor.fetchall()]

        if len(rows) < 2:
            return None

        best = rows[0]  # sorted ASC

        # Is the current listing the new best price?
        other_prices = [r["price_usd"] for r in rows if r["id"] != current_listing_id]
        current_price = next((r["price_usd"] for r in rows if r["id"] == current_listing_id), None)
        is_new_best = current_price is not None and (
            not other_prices or current_price <= min(other_prices)
        )

        # Owner heuristic: phone appearing on fewest total listings → likely owner
        owner_phone = None
        phones_with_freq = []
        for r in rows:
            p = r.get("phone_number")
            if p:
                freq = len(self._phone_index.get(p, set()))
                phones_with_freq.append((p, freq))

        if phones_with_freq:
            phones_with_freq.sort(key=lambda x: x[1])
            owner_phone = phones_with_freq[0][0]

        # Fallback: earliest published_at with phone
        if not owner_phone:
            for r in sorted(rows, key=lambda x: x.get("published_at") or "9999"):
                if r.get("phone_number"):
                    owner_phone = r["phone_number"]
                    break

        prices = [r["price_usd"] for r in rows]
        return {
            "group_id": group_id,
            "duplicate_count": len(rows),
            "best_price_usd": best["price_usd"],
            "best_price_url": best["url"],
            "probable_owner_phone": owner_phone,
            "price_spread": max(prices) - min(prices),
            "is_new_best_price": is_new_best,
        }

    # --- Main entry point ---

    # Max candidates to download images for per listing (prevents cascade in CI)
    MAX_CANDIDATES_TO_HASH = 5

    async def check_listing(self, listing: PropertyListing) -> Optional[dict]:
        """
        Checks a listing for duplicates. Returns group info dict or None.

        Always registers the listing's phone in the network index.
        Uses lazy hashing: only downloads images when candidates exist.
        Matching: ≥2 photo matches, or 1 photo + price/area confirmation (AND, not OR).
        """
        # Always register phone
        if listing.phone_number:
            self._phone_index[listing.phone_number].add(listing.id)

        try:
            return await self._check_listing_inner(listing)
        except Exception as e:
            print(f"[Dedup]: Error checking {listing.id}: {e}")
            return None

    async def _check_listing_inner(self, listing: PropertyListing) -> Optional[dict]:
        candidates = self._find_candidates(listing)
        if not candidates:
            return None

        # Lazy hashing: compute only now that we have candidates
        listing_hashes = await self._ensure_hashes(listing.id, images=listing.images)

        matched_group_ids: Set[str] = set()

        # Prioritize candidates that already have hashes (no download needed)
        cands_with_hash = [c for c in candidates if self._get_hashes(c["id"])]
        cands_without_hash = [c for c in candidates if not self._get_hashes(c["id"])]
        ordered = cands_with_hash + cands_without_hash[: self.MAX_CANDIDATES_TO_HASH]

        for cand in ordered:
            cand_hashes = await self._ensure_hashes(cand["id"], images_json=cand.get("images_json"))

            if not listing_hashes or not cand_hashes:
                continue

            match_count = self._count_image_matches(listing_hashes, cand_hashes)

            is_dup = False
            if match_count >= 2:
                is_dup = True
            elif match_count == 1:
                # 1 photo + price/area similarity as confirmation (AND)
                price_diff = abs(listing.price_usd - cand["price_usd"]) / max(
                    listing.price_usd, cand["price_usd"], 1
                )
                area_diff = abs(listing.area_m2 - cand["area_m2"]) / max(
                    listing.area_m2, cand["area_m2"], 1
                )
                if price_diff <= 0.25 and area_diff <= 0.10:
                    is_dup = True

            if is_dup:
                gid = cand.get("group_id")
                if gid:
                    matched_group_ids.add(gid)
                else:
                    # Candidate has no group yet — create one named after it
                    self._assign_group(cand["id"], cand["id"])
                    matched_group_ids.add(cand["id"])

        if not matched_group_ids:
            return None

        # Pick canonical group_id (alphabetically first for stability)
        group_id = min(matched_group_ids)

        # Merge if this listing bridges multiple groups
        if len(matched_group_ids) > 1:
            self._merge_groups(matched_group_ids, group_id)

        # Assign this listing to the group
        self._assign_group(listing.id, group_id)
        listing.duplicate_group_id = group_id

        info = self._get_group_info(group_id, listing.id)
        if info:
            print(
                f"[Dedup]: ⚠️ Duplicate detected! {listing.id} → group {group_id} "
                f"({info['duplicate_count']} listings, spread: ${info['price_spread']:,.0f})"
            )
        return info

    # --- Agent network ---

    def get_agent_network(self) -> Dict[str, List[str]]:
        """Returns phone -> listing_ids for phones with multiple listings."""
        return {phone: sorted(ids) for phone, ids in self._phone_index.items() if len(ids) > 1}

    def get_duplicate_stats(self) -> dict:
        """Returns summary statistics about detected duplicates."""
        with self.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT group_id, COUNT(*) as cnt FROM properties "
                "WHERE group_id IS NOT NULL GROUP BY group_id HAVING cnt > 1"
            )
            groups = [dict(r) for r in cursor.fetchall()]

        agent_network = self.get_agent_network()
        return {
            "duplicate_groups": len(groups),
            "total_duplicate_listings": sum(r["cnt"] for r in groups),
            "agents_with_multiple_listings": len(agent_network),
            "top_agents": sorted(
                [(phone, len(ids)) for phone, ids in agent_network.items()],
                key=lambda x: x[1],
                reverse=True,
            )[:10],
        }
