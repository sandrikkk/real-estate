from typing import Optional, Dict
from core.models import PropertyListing
from core.database import DatabaseEngine


# Baseline average $/m² priors in Tbilisi for instant outlier & bargain analysis
TBILISI_DISTRICT_BENCHMARKS: Dict[str, float] = {
    "ვაკე": 2100.0,
    "მთაწმინდა": 2200.0,
    "საბურთალო": 1600.0,
    "ბაგები": 1500.0,
    "ორთაჭალა": 1400.0,
    "ჩუღურეთი": 1350.0,
    "დიდუბე": 1300.0,
    "კრწანისი": 1300.0,
    "დიღომი": 1150.0,
    "ისანი": 1100.0,
    "სამგორი": 950.0,
    "ნაძალადევი": 950.0,
    "გლდანი": 900.0,
    "ვარკეთილი": 900.0,
}


class MarketAnalytics:
    def __init__(self, fallback_default_avg_m2: float = 1350.0):
        self.default_avg_m2 = fallback_default_avg_m2

    def get_district_benchmark(self, district: Optional[str], db: Optional[DatabaseEngine] = None) -> float:
        if district and db:
            db_stats = db.get_district_stats(district)
            if db_stats and db_stats.sample_count >= 5:
                return db_stats.median_price_per_m2

        if district:
            # Check matching district name in benchmarks
            for key, val in TBILISI_DISTRICT_BENCHMARKS.items():
                if key in district:
                    return val

        return self.default_avg_m2

    def evaluate_listing(
        self,
        listing: PropertyListing,
        db: Optional[DatabaseEngine] = None,
        discount_threshold_pct: float = 15.0
    ) -> PropertyListing:
        """
        Calculates market benchmark price/m² and flags high-value bargains or anomalies.
        """
        if listing.price_per_m2 <= 0:
            return listing

        benchmark = self.get_district_benchmark(listing.district, db=db)
        listing.market_avg_price_m2 = benchmark

        # Suspicious / Outlier check (e.g. price per m2 too low or data entry error)
        if listing.price_per_m2 < 300:
            listing.is_bargain = False
            return listing

        # Compute discount percentage relative to benchmark
        discount_pct = round(((benchmark - listing.price_per_m2) / benchmark) * 100, 1)
        listing.discount_pct = discount_pct

        if discount_pct >= discount_threshold_pct:
            listing.is_bargain = True
        else:
            listing.is_bargain = False

        return listing
