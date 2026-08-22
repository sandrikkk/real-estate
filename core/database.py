import json
import sqlite3
import statistics
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Dict
from core.models import PropertyListing, DistrictPriceStats


class DatabaseEngine:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_db_dir()
        self.init_db()

    def _ensure_db_dir(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self):
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS properties (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    price_usd REAL NOT NULL,
                    price_gel REAL,
                    area_m2 REAL NOT NULL,
                    price_per_m2 REAL NOT NULL,
                    city TEXT DEFAULT 'თბილისი',
                    district TEXT,
                    subdistrict TEXT,
                    street TEXT,
                    floor TEXT,
                    total_floors INTEGER,
                    rooms INTEGER,
                    bedrooms INTEGER,
                    url TEXT NOT NULL,
                    images_json TEXT,
                    published_at TEXT,
                    scraped_at TEXT NOT NULL,
                    is_bargain INTEGER DEFAULT 0,
                    is_notified INTEGER DEFAULT 0
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_properties_district ON properties(district)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_properties_scraped_at ON properties(scraped_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_properties_is_notified ON properties(is_notified)")
            conn.commit()

    def is_seen(self, listing_id: str, listing: Optional[PropertyListing] = None) -> bool:
        with self._connection() as conn:
            cursor = conn.cursor()
            # 1. Exact ID check
            cursor.execute("SELECT 1 FROM properties WHERE id = ?", (listing_id,))
            if cursor.fetchone() is not None:
                return True

            # 2. Cross-portal / Reposted duplicate fingerprint check
            if listing and listing.district and listing.area_m2 and listing.price_usd:
                cursor.execute("""
                    SELECT 1 FROM properties 
                    WHERE district = ? 
                      AND abs(price_usd - ?) <= 200
                      AND abs(area_m2 - ?) <= 0.5
                      AND (floor = ? OR floor IS NULL OR ? = '')
                """, (
                    listing.district,
                    listing.price_usd,
                    listing.area_m2,
                    listing.floor or "",
                    listing.floor or ""
                ))
                if cursor.fetchone() is not None:
                    return True

        return False

    def save_listing(self, listing: PropertyListing) -> bool:
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO properties (
                        id, source, source_id, title, description,
                        price_usd, price_gel, area_m2, price_per_m2,
                        city, district, subdistrict, street,
                        floor, total_floors, rooms, bedrooms,
                        url, images_json, published_at, scraped_at,
                        is_bargain, is_notified
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    listing.id,
                    listing.source,
                    listing.source_id,
                    listing.title,
                    listing.description,
                    listing.price_usd,
                    listing.price_gel,
                    listing.area_m2,
                    listing.price_per_m2,
                    listing.city,
                    listing.district,
                    listing.subdistrict,
                    listing.street,
                    listing.floor,
                    listing.total_floors,
                    listing.rooms,
                    listing.bedrooms,
                    listing.url,
                    json.dumps(listing.images, ensure_ascii=False),
                    listing.published_at,
                    listing.scraped_at.isoformat(),
                    1 if listing.is_bargain else 0,
                    0
                ))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                print(f"[Database Error]: Failed to save listing {listing.id}: {e}")
                return False

    def mark_as_notified(self, listing_id: str):
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE properties SET is_notified = 1 WHERE id = ?", (listing_id,))
            conn.commit()

    def get_district_stats(self, district: str) -> Optional[DistrictPriceStats]:
        if not district:
            return None
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT price_per_m2 FROM properties 
                WHERE district = ? AND price_per_m2 > 100 AND price_per_m2 < 10000
            """, (district,))
            rows = cursor.fetchall()
            if not rows or len(rows) < 3:
                return None
            prices = [r["price_per_m2"] for r in rows]
            return DistrictPriceStats(
                district=district,
                sample_count=len(prices),
                avg_price_per_m2=round(sum(prices) / len(prices), 2),
                median_price_per_m2=round(statistics.median(prices), 2),
                min_price_per_m2=round(min(prices), 2),
                max_price_per_m2=round(max(prices), 2)
            )

    def get_all_district_stats(self) -> Dict[str, DistrictPriceStats]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT district FROM properties WHERE district IS NOT NULL AND district != ''")
            districts = [r["district"] for r in cursor.fetchall()]
        stats = {}
        for d in districts:
            s = self.get_district_stats(d)
            if s:
                stats[d] = s
        return stats
