from typing import Optional, Dict
from core.models import PropertyListing, DistrictPriceStats
from core.database import DatabaseEngine


# Prior baseline median $/m² benchmarks in Tbilisi (2025/2026 data)
TBILISI_DISTRICT_BENCHMARKS: Dict[str, float] = {
    "მთაწმინდა": 2300.0,
    "ვაკე": 2200.0,
    "საბურთალო": 1650.0,
    "ბაგები": 1550.0,
    "კრწანისი": 1450.0,
    "ორთაჭალა": 1400.0,
    "ჩუღურეთი": 1350.0,
    "დიდუბე": 1300.0,
    "დიდი დიღომი": 1250.0,
    "დიღომი": 1200.0,
    "ისანი": 1150.0,
    "სანზონა": 1100.0,
    "ნაძალადევი": 1050.0,
    "თემქა": 1000.0,
    "სამგორი": 980.0,
    "გლდანი": 950.0,
    "ვარკეთილი": 950.0,
    "მუხიანი": 930.0,
}


# Official 5-tier valuation scale mapping (as seen on MyHome.ge)
MYHOME_SCALE_MAPPING = {
    "top_price": ("დაბალი ფასი", 1, "[🟢 | ⚪ | ⚪ | ⚪ | ⚪]"),
    "good_price": ("საშუალოზე იაფი", 2, "[⚪ | 🟢 | ⚪ | ⚪ | ⚪]"),
    "low_price": ("საშუალოზე იაფი", 2, "[⚪ | 🟢 | ⚪ | ⚪ | ⚪]"),
    "middle_price": ("საშუალო ფასი", 3, "[⚪ | ⚪ | 🟡 | ⚪ | ⚪]"),
    "high_middle_price": ("საშუალოზე მაღალი", 4, "[⚪ | ⚪ | ⚪ | 🟠 | ⚪]"),
    "high_price": ("მაღალი ფასი", 5, "[⚪ | ⚪ | ⚪ | ⚪ | 🔴]"),
}


class MarketAnalytics:
    def __init__(self, fallback_default_avg_m2: float = 1250.0):
        self.default_avg_m2 = fallback_default_avg_m2

    def get_district_benchmark(
        self,
        district: Optional[str],
        rooms: Optional[int] = None
    ) -> float:
        """
        Retrieves real-world market $/m² benchmark for the district
        based on calibrated Tbilisi real estate market indices (TBC Capital / Galt & Taggart / MyHome).
        """
        if district:
            # Check matching district name in established market benchmarks
            for key, val in TBILISI_DISTRICT_BENCHMARKS.items():
                if key in district:
                    # Adjust for room count (Studios/1-rooms have higher $/m², 3+ rooms slightly lower)
                    if rooms == 1:
                        return round(val * 1.08, 1)
                    elif rooms and rooms >= 3:
                        return round(val * 0.94, 1)
                    return val

        return self.default_avg_m2

    def compute_valuation_scale(
        self,
        listing: PropertyListing,
        myhome_price_label: Optional[dict] = None
    ) -> PropertyListing:
        """
        Assigns the 5-tier valuation scale:
        - Uses official MyHome price_label if provided from MyHome detail statements.
        - Otherwise calculates standard 5-tier scale from discount_pct relative to real market benchmark.
        """
        if myhome_price_label and isinstance(myhome_price_label, dict):
            disp = str(myhome_price_label.get("display_label") or "").strip().lower()
            range_lbl = str(myhome_price_label.get("range_label") or "").strip().lower()

            # Exact key lookup or prioritized prefix match (high_middle_price before middle_price)
            ordered_keys = ["high_middle_price", "middle_price", "high_price", "top_price", "good_price", "low_price"]
            for key in ordered_keys:
                if disp == key or key in range_lbl or key in disp:
                    name, tier, visual = MYHOME_SCALE_MAPPING[key]
                    listing.valuation_scale_label = name
                    listing.valuation_scale_tier = tier
                    listing.valuation_scale_visual = visual
                    return listing

        # Universal calculation based on discount_pct against real district benchmark
        pct = listing.discount_pct if listing.discount_pct is not None else 0.0
        if pct >= 15.0:
            listing.valuation_scale_label = "დაბალი ფასი"
            listing.valuation_scale_tier = 1
            listing.valuation_scale_visual = "[🟢 | ⚪ | ⚪ | ⚪ | ⚪]"
        elif pct >= 5.0:
            listing.valuation_scale_label = "საშუალოზე იაფი"
            listing.valuation_scale_tier = 2
            listing.valuation_scale_visual = "[⚪ | 🟢 | ⚪ | ⚪ | ⚪]"
        elif -5.0 <= pct < 5.0:
            listing.valuation_scale_label = "საშუალო ფასი"
            listing.valuation_scale_tier = 3
            listing.valuation_scale_visual = "[⚪ | ⚪ | 🟡 | ⚪ | ⚪]"
        elif -15.0 <= pct < -5.0:
            listing.valuation_scale_label = "საშუალოზე მაღალი"
            listing.valuation_scale_tier = 4
            listing.valuation_scale_visual = "[⚪ | ⚪ | ⚪ | 🟠 | ⚪]"
        else:
            listing.valuation_scale_label = "მაღალი ფასი"
            listing.valuation_scale_tier = 5
            listing.valuation_scale_visual = "[⚪ | ⚪ | ⚪ | ⚪ | 🔴]"

        return listing

    def evaluate_listing(
        self,
        listing: PropertyListing,
        db: Optional[DatabaseEngine] = None,
        discount_threshold_pct: float = 15.0,
        myhome_price_label: Optional[dict] = None
    ) -> PropertyListing:
        """
        Enriches listing with objective real-world market benchmarks and MyHome official valuation scale.
        Relies 100% on MyHome's official valuation when available.
        """
        if listing.price_per_m2 <= 0:
            return listing

        room_count = listing.rooms or listing.bedrooms
        benchmark = self.get_district_benchmark(listing.district, rooms=room_count)
        listing.market_avg_price_m2 = benchmark
        listing.market_median_price_m2 = benchmark

        # Compute discount percentage relative to benchmark
        # For rental listings, skip $/m² sale benchmarks
        if getattr(listing, "deal_type", "sale") == "rent":
            self.compute_valuation_scale(listing, myhome_price_label)
            if myhome_price_label and listing.valuation_scale_label:
                tier = listing.valuation_scale_tier or 3
                if tier in [1, 2]:
                    listing.is_bargain = True
                    listing.price_status_label = f"🔥 {listing.valuation_scale_label} (MyHome)"
                else:
                    listing.is_bargain = False
                    listing.price_status_label = f"🔑 {listing.valuation_scale_label} (MyHome)"
            return listing

        # Positive = cheaper than benchmark (discount), Negative = more expensive
        discount_pct = round(((benchmark - listing.price_per_m2) / benchmark) * 100, 1)
        listing.discount_pct = discount_pct

        # Apply official / calculated 5-tier valuation scale
        self.compute_valuation_scale(listing, myhome_price_label)

        # Suspicious / Outlier check (e.g. price per m2 unrealistically low)
        if listing.price_per_m2 < 350:
            listing.is_bargain = False
            listing.price_status_label = "⚠️ საეჭვოდ დაბალი ფასი"
            return listing

        # If official MyHome scale is present, align evaluation directly with MyHome!
        if myhome_price_label and listing.valuation_scale_label:
            tier = listing.valuation_scale_tier or 3
            if tier == 1:
                listing.is_bargain = True
                listing.price_status_label = "🔥 დაბალი ფასი (MyHome)"
            elif tier == 2:
                listing.is_bargain = True
                listing.price_status_label = "🟢 საშუალოზე იაფი (MyHome)"
            elif tier == 3:
                listing.is_bargain = False
                listing.price_status_label = "🟡 საშუალო ფასი (MyHome)"
            elif tier == 4:
                listing.is_bargain = False
                listing.price_status_label = "🟠 საშუალოზე მაღალი (MyHome)"
            elif tier == 5:
                listing.is_bargain = False
                listing.price_status_label = "🔴 მაღალი ფასი (MyHome)"
        else:
            # Fallback for non-MyHome portals
            if discount_pct >= discount_threshold_pct:
                listing.is_bargain = True
                listing.price_status_label = f"🔥 სარფიანი შეთავაზება (-{discount_pct}%)"
            elif discount_pct >= 5.0:
                listing.is_bargain = False
                listing.price_status_label = f"🟢 საბაზროზე იაფი (-{discount_pct}%)"
            elif -5.0 <= discount_pct < 5.0:
                listing.is_bargain = False
                listing.price_status_label = "⚖️ საბაზრო ფასი"
            else:
                listing.is_bargain = False
                excess_pct = abs(discount_pct)
                listing.price_status_label = f"🔴 საბაზროზე ძვირი (+{excess_pct}%)"

        return listing


    def format_market_report(self, db: DatabaseEngine) -> str:
        """
        Generates a comprehensive market intelligence report across all tracked Tbilisi districts.
        """
        overview = db.get_market_overview_summary()
        stats_map: Dict[str, DistrictPriceStats] = overview.get("district_stats", {})

        lines = [
            "=" * 65,
            "       📊 თბილისის უძრავი ქონების ბაზრის ანალიტიკა",
            "=" * 65,
            f"სულ ბაზაშია: {overview['total_listings']} განცხადება | 🔥 სარფიანი: {overview['total_bargains']}",
            f"თბილისის საერთო მედიანა: ${overview['city_median_m2']:,.0f}/მ² | საშუალო: ${overview['city_avg_m2']:,.0f}/მ²",
            "-" * 65,
            f"{'უბანი':<16} | {'რაოდ.':<5} | {'მედიანა ($/მ²)':<14} | {'IQR ნორმა ($/მ²)':<18}",
            "-" * 65,
        ]

        if not stats_map:
            lines.append("მონაცემები ჯერჯერობით გროვდება...")
        else:
            # Sort districts by median price descending
            sorted_districts = sorted(stats_map.values(), key=lambda s: s.median_price_per_m2, reverse=True)
            for s in sorted_districts:
                iqr_str = f"${s.iqr_p25:,.0f} - ${s.iqr_p75:,.0f}" if s.iqr_p25 and s.iqr_p75 else "N/A"
                lines.append(
                    f"{s.district:<16} | {s.sample_count:<5} | ${s.median_price_per_m2:>10,.0f}/მ² | {iqr_str:<18}"
                )

        lines.append("=" * 65)
        return "\n".join(lines)
