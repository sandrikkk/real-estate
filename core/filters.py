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
        # Price range check
        if self.filters.price_min_usd is not None and listing.price_usd < self.filters.price_min_usd:
            return False
        if self.filters.price_max_usd is not None and listing.price_usd > self.filters.price_max_usd:
            return False

        # Area range check
        if self.filters.area_min_m2 is not None and listing.area_m2 < self.filters.area_min_m2:
            return False
        if self.filters.area_max_m2 is not None and listing.area_m2 > self.filters.area_max_m2:
            return False

        # Rooms check
        if self.filters.rooms and listing.rooms is not None:
            if listing.rooms not in self.filters.rooms:
                return False

        # Price per m² ceiling check
        if self.filters.price_per_m2_max_usd is not None and listing.price_per_m2 > self.filters.price_per_m2_max_usd:
            return False

        # Images check
        if self.filters.require_images and not listing.images:
            return False

        # District whitelist check
        if self.filters.target_districts and listing.district:
            matched_district = False
            for target in self.filters.target_districts:
                if target.lower() in listing.district.lower() or (
                    listing.subdistrict and target.lower() in listing.subdistrict.lower()
                ):
                    matched_district = True
                    break
            if not matched_district:
                return False

        # Stop words filter on title and description
        text_corpus = f"{listing.title} {listing.description or ''}"
        for pattern in self._compiled_stop_words:
            if pattern.search(text_corpus):
                return False

        return True
