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

            # Auto-migrate existing database schema with newly introduced columns
            cursor.execute("PRAGMA table_info(properties)")
            cols = {row["name"] for row in cursor.fetchall()}
            if "metro_station_id" not in cols:
                cursor.execute("ALTER TABLE properties ADD COLUMN metro_station_id INTEGER")
            if "condition_id" not in cols:
                cursor.execute("ALTER TABLE properties ADD COLUMN condition_id INTEGER")
            if "is_hot_deal" not in cols:
                cursor.execute("ALTER TABLE properties ADD COLUMN is_hot_deal INTEGER DEFAULT 0")
            if "phone_number" not in cols:
                cursor.execute("ALTER TABLE properties ADD COLUMN phone_number TEXT")

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
                        is_bargain, is_notified,
                        metro_station_id, condition_id, is_hot_deal, phone_number
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    0,
                    listing.metro_station_id,
                    listing.condition_id,
                    1 if listing.is_hot_deal else 0,
                    listing.phone_number
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
            # Clean filtering: only realistic $/m2 between 350 and 6000 USD
            cursor.execute("""
                SELECT price_per_m2, rooms, bedrooms FROM properties 
                WHERE (district = ? OR subdistrict = ?) 
                  AND price_per_m2 >= 350 AND price_per_m2 <= 6000
            """, (district, district))
            rows = cursor.fetchall()
            if not rows or len(rows) < 2:
                return None

            prices = [r["price_per_m2"] for r in rows]
            sorted_prices = sorted(prices)
            n = len(sorted_prices)

            # Robust IQR calculation
            if n >= 4:
                try:
                    q = statistics.quantiles(sorted_prices, n=4, method="inclusive")
                    p25, p75 = round(q[0], 2), round(q[2], 2)
                except Exception:
                    p25 = round(sorted_prices[int(n * 0.25)], 2)
                    p75 = round(sorted_prices[int(n * 0.75)], 2)
            else:
                p25 = round(sorted_prices[0], 2)
                p75 = round(sorted_prices[-1], 2)

            std_dev = round(statistics.stdev(prices), 2) if n >= 2 else 0.0

            # Compute medians by room count (e.g. 1-room, 2-room, 3-room)
            room_groups: Dict[int, list] = {}
            for r in rows:
                room_cnt = r["rooms"] or r["bedrooms"]
                if room_cnt and 1 <= room_cnt <= 6:
                    room_groups.setdefault(room_cnt, []).append(r["price_per_m2"])

            room_medians: Dict[int, float] = {}
            for r_cnt, r_prices in room_groups.items():
                if len(r_prices) >= 1:
                    room_medians[r_cnt] = round(statistics.median(r_prices), 2)

            return DistrictPriceStats(
                district=district,
                sample_count=n,
                avg_price_per_m2=round(sum(prices) / n, 2),
                median_price_per_m2=round(statistics.median(prices), 2),
                iqr_p25=p25,
                iqr_p75=p75,
                min_price_per_m2=round(min(prices), 2),
                max_price_per_m2=round(max(prices), 2),
                std_dev=std_dev,
                room_medians=room_medians
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

    def get_market_overview_summary(self) -> dict:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total, SUM(is_bargain) as bargains FROM properties")
            row = cursor.fetchone()
            total_count = row["total"] if row else 0
            bargains_count = row["bargains"] if row and row["bargains"] else 0

            cursor.execute("""
                SELECT price_per_m2 FROM properties 
                WHERE price_per_m2 >= 350 AND price_per_m2 <= 6000
            """)
            all_prices = [r["price_per_m2"] for r in cursor.fetchall()]

        city_median = round(statistics.median(all_prices), 2) if all_prices else 0.0
        city_avg = round(sum(all_prices) / len(all_prices), 2) if all_prices else 0.0

        return {
            "total_listings": total_count,
            "total_bargains": bargains_count,
            "city_median_m2": city_median,
            "city_avg_m2": city_avg,
            "district_stats": self.get_all_district_stats()
        }
