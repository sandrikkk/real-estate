import json
import re
from pathlib import Path
from typing import List, Optional
from core.models import PropertyListing, SearchFilters


class ListingFilter:
    def __init__(self, filters: SearchFilters):
        self.filters = filters
        self._compiled_stop_words = [
            re.compile(re.escape(w), re.IGNORECASE) for w in filters.stop_words if w.strip()
        ]
        self._compiled_blacklist = [
            re.compile(re.escape(w), re.IGNORECASE) for w in filters.blacklist_keywords if w.strip()
        ]

    @classmethod
    def from_json(cls, file_path: str) -> "ListingFilter":
        path = Path(file_path)
        if not path.exists():
            return cls(SearchFilters())
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(SearchFilters(**data))
        except Exception as e:
            print(f"[Warning]: Failed to load {file_path}, using defaults: {e}")
            return cls(SearchFilters())

    def matches(self, listing: PropertyListing) -> bool:
        """
        Executes strict client-side validation against business rules:
        1. Price & Area ranges
        2. Status & Condition (excludes under construction & black frame; checks white frame price limit)
        3. Stop-words in title/description
        4. Minimum 2 rooms (excludes 1-room studios)
        5. Strict location blacklist and metro/district whitelist
        6. Flags HOT DEAL if price_per_sqm <= 1350 and renovated
        """
        # Ensure price_per_m2 is computed
        if listing.area_m2 > 0 and listing.price_usd > 0:
            listing.price_per_m2 = round(listing.price_usd / listing.area_m2, 2)

        # 1. Price Range Check
        if self.filters.price_min_usd is not None and listing.price_usd < self.filters.price_min_usd:
            return False
        if self.filters.price_max_usd is not None and listing.price_usd > self.filters.price_max_usd:
            return False

        # 2. Area Range Check
        if self.filters.area_min_m2 is not None and listing.area_m2 < self.filters.area_min_m2:
            return False
        if self.filters.area_max_m2 is not None and listing.area_m2 > self.filters.area_max_m2:
            return False

        # 3. Price per m² Ceiling Check
        if self.filters.price_per_m2_max_usd is not None and listing.price_per_m2 > self.filters.price_per_m2_max_usd:
            return False

        # 4. Images Check
        if self.filters.require_images and not listing.images:
            return False

        # Text corpus for keyword and stop-word analysis
        text_corpus = f"{listing.title} {listing.description or ''}"

        # 5. Building Status Check: Exclude "Under Construction" (მშენებარე)
        # status_id 3 is "მშენებარე"
        if listing.status_id == 3:
            return False
        if "მშენებარე" in text_corpus:
            return False

        # 6. Condition Check
        # condition_id 6 is "შავი კარკასი" (Black Frame)
        if listing.condition_id == 6 or "შავი კარკასი" in text_corpus:
            return False

        # White Frame condition: condition_id 5 (თეთრი კარკასი) or 8 (თეთრი პლიუსი)
        is_white_frame = (
            listing.condition_id in [5, 8]
            or (listing.condition_name and "თეთრი კარკასი" in listing.condition_name)
            or ("თეთრი კარკასი" in text_corpus)
        )
        if is_white_frame and listing.price_usd > self.filters.white_frame_max_price:
            # White Frame allowed ONLY if price <= $55,000
            return False

        # 7. Stop-words in Title / Description
        for pattern in self._compiled_stop_words:
            if pattern.search(text_corpus):
                return False

        # 8. Minimum Rooms Check (Min 2 rooms: 1 bedroom + living room/studio; exclude single-room studio layouts)
        if listing.rooms is not None and listing.rooms < self.filters.rooms_min:
            return False
        if listing.bedrooms is not None and listing.bedrooms < 1:
            if listing.rooms is not None and listing.rooms < 2:
                return False

        # 9. Location Filtering: Strict Blacklist
        location_corpus = f"{listing.district or ''} {listing.subdistrict or ''} {listing.street or ''} {text_corpus}"
        for pattern in self._compiled_blacklist:
            if pattern.search(location_corpus):
                return False

        # 10. Location Priority: Target Metro Stations or Whitelist Districts / Target Districts
        if listing.metro_station_id is not None and listing.metro_station_id > 0:
            if self.filters.target_metro_ids and listing.metro_station_id not in self.filters.target_metro_ids:
                return False
        else:
            # Fallback to target_districts or whitelist_districts if metro_station_id is not available
            allowed_districts = self.filters.target_districts or self.filters.whitelist_districts
            if allowed_districts:
                primary_loc = (listing.district or "").lower()
                matched_district = False
                for target in allowed_districts:
                    target_low = target.lower()
                    if target_low in primary_loc:
                        matched_district = True
                        break
                    if listing.street and target_low in listing.street.lower():
                        matched_district = True
                        break
                if not matched_district:
                    return False

        # 11. HOT DEAL Flagging: price_per_sqm <= 1350 and condition is renovated
        is_renovated = (
            listing.condition_id in [1, 2]
            or (listing.condition_name and ("გარემონტებული" in listing.condition_name or "ახალი გარემონტებული" in listing.condition_name))
            or ("ახალი გარემონტებული" in text_corpus or "გარემონტებული" in text_corpus)
        )
        if listing.price_per_m2 <= self.filters.hot_deal_price_per_sqm and is_renovated:
            listing.is_hot_deal = True
            listing.is_bargain = True

        # 12. Owner vs Agent Tagging
        if listing.user_type == "physical" or listing.is_owner is True:
            listing.is_owner = True
        elif listing.user_type in ["agency", "developer"]:
            listing.is_owner = False

        return True
