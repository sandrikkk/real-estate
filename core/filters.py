import json
import re
from pathlib import Path
from typing import List, Optional, Dict
from core.models import PropertyListing, SearchFilters, UserSubscription


DISTRICT_SYNONYMS: Dict[str, List[str]] = {
    # 1. დიდუბე
    "დიდუბე": ["დიდუბე", "წერეთელი", "წერეთლის გამზ."],
    # 2. დიღმის მასივი
    "დიღმის მასივი": ["დიღმის მასივი", "დიღმის მას."],
    # 3. ჩუღურეთი
    "ჩუღურეთი": ["ჩუღურეთი", "კუკია", "სვანეთის უბანი", "ივერთუბანი", "მარჯანიშვილი", "ვორონცოვი"],
    # 4. ნაძალადევი
    "ნაძალადევი": ["ნაძალადევი", "სან. ზონა", "სანზონა", "სან.ზონა", "თემქა", "ლოტკინი", "ნაძალადევის"],
    # 5. გლდანი
    "გლდანი": ["გლდანი", "გლდანულა", "თბილისის ზღვა", "კონიაკის დასახლება", "კონიაკის დას.", "კონიაკი", "მუხიანი", "ავშნიანი", "ავჭალა", "ზაჰესი", "სოფ. გლდანი", "გიორგიწმინდა"],
    # 6. ვაკე
    "ვაკე": ["ვაკე", "ბაგები", "კუს ტბა", "ვერა", "წყნეთი"],
    # 7. საბურთალო
    "საბურთალო": ["საბურთალო", "ვაშლიჯვარი", "ვეძისი", "თხინვალი", "ლისი", "ლისის მიმდებარედ", "მუხათგვერდი", "მუხათწყარო", "ნუცუბიძის ფერდობი", "ნუცუბიძე", "დელისი"],
    # 8. დიდი დიღომი
    "დიდი დიღომი": ["დიდი დიღომი", "დიღომი 1-9", "სოფ. დიღომი", "სოფელი დიღომი", "დიღმის ჭალა", "ქოშიგორა"],
    "დიღომი": ["დიდი დიღომი", "დიღმის მასივი", "დიღომი 1-9", "სოფ. დიღომი", "სოფელი დიღომი", "დიღმის ჭალა", "ქოშიგორა"],
    # 9. ისანი
    "ისანი": ["ისანი", "ნავთლუღი", "ავლაბარი", "მეტრომშენი", "300 არაგველი"],
    # 10. სამგორი
    "სამგორი": ["სამგორი", "ვარკეთილი", "ვაზისუბანი", "მესამე მასივი", "მოსკოვის გამზირი", "მოსკოვის გამზ.", "აფრიკა", "აეროპორტის უბანი", "აეროპორტი", "დამპალო", "ორხევი", "ლილო", "ფონიჭალა"],
    # 11. მთაწმინდა
    "მთაწმინდა": ["მთაწმინდა", "სოლოლაკი", "რუსთაველი", "თავისუფლება"],
    # 12. კრწანისი
    "კრწანისი": ["კრწანისი", "ორთაჭალა", "ფონიჭალა", "აბანოთუბანი", "ელია", "წავკისის ველი"],

    # Parent groups from MyHome modal
    "ვაკე-საბურთალო": [
        "ვაკე", "ვაშლიჯვარი", "ვეძისი", "თხინვალი", "კუს ტბა", "ლისი", "მუხათგვერდი", "მუხათწყარო",
        "საბურთალო", "ლისის მიმდებარედ", "ბაგები", "სოფ. დიღომი", "დიღმის ჭალა", "ქოშიგორა",
        "დიღომი 1-9", "დიდი დიღომი", "ნუცუბიძის ფერდობი"
    ],
    "ისანი-სამგორი": [
        "ვარკეთილი", "ისანი", "ლილო", "მესამე მასივი", "მოსკოვის გამზირი", "მოსკოვის გამზ.", "აფრიკა",
        "აეროპორტის უბანი", "აეროპორტი", "დამპალო", "ვაზისუბანი", "ნავთლუღი", "ორხევი", "სამგორი", "ფონიჭალა"
    ],
    "გლდანი-ნაძალადევი": [
        "გლდანი", "გლდანულა", "თბილისის ზღვა", "თემქა", "კონიაკის დასახლება", "კონიაკის დას.", "ლოტკინი",
        "მუხიანი", "ნაძალადევი", "სან. ზონა", "სანზონა", "ავშნიანი", "ავჭალა", "ზაჰესი", "სოფ. გლდანი", "გიორგიწმინდა"
    ],
    "დიდუბე-ჩუღურეთი": [
        "დიდუბე", "დიღმის მასივი", "კუკია", "ჩუღურეთი", "ივერთუბანი", "სვანეთის უბანი"
    ],
    "ძველი თბილისი": [
        "ელია", "ვერა", "კრწანისი", "მთაწმინდა", "სოლოლაკი", "წავკისის ველი", "აბანოთუბანი", "ავლაბარი", "ორთაჭალა"
    ],
    "თბილისის შემოგარენი": [
        "წავკისი", "ახალდაბა", "ბეთანია", "კაკლები", "კიკეთი", "კოჯორი", "ოქროყანა", "ტაბახმელა",
        "შინდისი", "წყნეთი", "მსხალდიდი", "წოდორეთი", "ზემო ლისი", "კვესეთი"
    ]
}


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

    def matches_hygiene(self, listing: PropertyListing) -> bool:
        """
        Executes baseline quality and fraud/junk filtering on a listing:
        - Excludes 'under construction' (მშენებარე)
        - Excludes black frame (შავი კარკასი)
        - Excludes stop words (e.g. 'იპოთეკური', 'გირავდება', etc.)
        - Excludes deep outskirts / blacklisted areas
        - Tags hot deals, frames, and owner/agency
        """
        # Ensure price_per_m2 is computed
        if listing.area_m2 > 0 and listing.price_usd > 0:
            listing.price_per_m2 = round(listing.price_usd / listing.area_m2, 2)

        if self.filters.require_images and not listing.images:
            return False

        text_corpus = f"{listing.title} {listing.description or ''}"

        # Building Status: Exclude Under Construction
        if listing.status_id == 3 or "მშენებარე" in text_corpus:
            return False

        # Condition Check: Exclude Black Frame
        if listing.condition_id == 6 or "შავი კარკასი" in text_corpus:
            return False

        # Green / White Frame condition
        is_frame = (
            listing.condition_id in [5, 7, 8]
            or (listing.condition_name and any(c in listing.condition_name for c in ["თეთრი კარკასი", "მწვანე კარკასი", "თეთრი პლიუსი"]))
            or any(c in text_corpus for c in ["თეთრი კარკასი", "მწვანე კარკასი", "თეთრი პლიუსი"])
        )
        if getattr(listing, "deal_type", "sale") != "rent" and is_frame and listing.price_usd > self.filters.white_frame_max_price:
            return False

        # Stop-words in Title / Description
        for pattern in self._compiled_stop_words:
            if pattern.search(text_corpus):
                return False

        # Location Filtering: Strict Blacklist
        location_corpus = f"{listing.district or ''} {listing.subdistrict or ''} {listing.street or ''} {text_corpus}"
        for pattern in self._compiled_blacklist:
            if pattern.search(location_corpus):
                return False

        # Gldani: Allow Micro-districts 1-2 only
        loc_lower = location_corpus.lower()
        if "გლდანი" in loc_lower or (listing.district and "გლდანი" in listing.district.lower()):
            gldani_deep_mr = [
                "3 მ/რ", "3-ე მ/რ", "მე-3 მ/რ", "3 მ/რაიონი", "iii მ/რ", "iii მ/რაიონი",
                "4 მ/რ", "4-ე მ/რ", "მე-4 მ/რ", "4 მ/რაიონი", "iv მ/რ", "iv მ/რაიონი",
                "5 მ/რ", "5-ე მ/რ", "მე-5 მ/რ", "5 მ/რაიონი", "v მ/რ", "v მ/რაიონი",
                "6 მ/რ", "6-ე მ/რ", "მე-6 მ/რ", "6 მ/რაიონი", "vi მ/რ", "vi მ/რაიონი",
                "7 მ/რ", "7-ე მ/რ", "მე-7 მ/რ", "7 მ/რაიონი", "vii მ/რ", "vii მ/რაიონი",
                "8 მ/რ", "8-ე მ/რ", "მე-8 მ/რ", "8 მ/რაიონი", "viii მ/რ", "viii მ/რაიონი"
            ]
            if any(mr in loc_lower for mr in gldani_deep_mr):
                return False

        # Deal Tagging
        if is_frame:
            if listing.price_per_m2 <= self.filters.value_frame_price_per_sqm:
                listing.is_hot_deal = True
                listing.deal_tag = "🔥 VALUE FRAME (<$54k)"
                listing.is_bargain = True
        else:
            is_renovated = (
                listing.condition_id in [1, 2, 3]
                or (listing.condition_name and ("გარემონტებული" in listing.condition_name or "ახალი გარემონტებული" in listing.condition_name))
                or ("ახალი გარემონტებული" in text_corpus or "გარემონტებული" in text_corpus)
            )
            if is_renovated and listing.price_per_m2 <= self.filters.hot_deal_price_per_sqm:
                listing.is_hot_deal = True
                listing.deal_tag = "🚨 HOT DEAL (RENOVATED)"
                listing.is_bargain = True

        # Owner vs Agent Tagging: explicit is_owner False or agent keywords always take precedence
        if listing.is_owner is False or listing.user_type in ["agency", "developer", "agent", "broker"]:
            listing.is_owner = False
        elif re.search(r'(?:ვარ\s+(?:აგენტი|მაკლერი)|სააგენტოდან|ჩვენი\s+სააგენტო|დაგვიკავშირდით\s+სააგენტო)', text_corpus, re.IGNORECASE):
            listing.is_owner = False
        elif listing.is_owner is True:
            listing.is_owner = True
        elif listing.user_type == "physical":
            listing.is_owner = True
        elif listing.is_owner is None:
            if re.search(r'(?:ვარ\s+(?:მეპატრონე|მესაკუთრე)|მესაკუთრისგან|სააგენტოებთან არ ვთანამშრომლობ|სააგენტოები ნუ|მაკლერები ნუ)', text_corpus, re.IGNORECASE):
                listing.is_owner = True

        return True

    def matches(self, listing: PropertyListing) -> bool:
        """
        Executes strict client-side validation against baseline business rules and static filters.
        """
        if not self.matches_hygiene(listing):
            return False

        # Deal Type Check
        if self.filters.deal_type != "both":
            if getattr(listing, "deal_type", "sale") != self.filters.deal_type:
                return False

        # 1. Price Range Check
        is_rent = getattr(listing, "deal_type", "sale") == "rent"
        min_p = self.filters.rent_price_min_usd if is_rent and self.filters.rent_price_min_usd is not None else self.filters.price_min_usd
        max_p = self.filters.rent_price_max_usd if is_rent and self.filters.rent_price_max_usd is not None else self.filters.price_max_usd

        if min_p is not None and listing.price_usd < min_p:
            return False
        if max_p is not None and listing.price_usd > max_p:
            return False

        # 2. Area Range Check
        if self.filters.area_min_m2 is not None and listing.area_m2 < self.filters.area_min_m2:
            return False
        if self.filters.area_max_m2 is not None and listing.area_m2 > self.filters.area_max_m2:
            return False

        # 3. Price per m² Ceiling Check
        if self.filters.price_per_m2_max_usd is not None and listing.price_per_m2 > self.filters.price_per_m2_max_usd:
            return False

        # 4. Minimum Rooms Check
        if listing.rooms is not None and listing.rooms < self.filters.rooms_min:
            return False
        if listing.bedrooms is not None and listing.bedrooms < 1:
            if listing.rooms is not None and listing.rooms < 2:
                return False

        # 5. Location Priority: Target Metro Stations or Whitelist Districts / Target Districts
        if listing.metro_station_id is not None and listing.metro_station_id > 0:
            if self.filters.target_metro_ids and listing.metro_station_id not in self.filters.target_metro_ids:
                return False
        else:
            allowed_districts = self.filters.target_districts or self.filters.whitelist_districts
            if allowed_districts and not self._matches_district_criteria(allowed_districts, listing):
                return False

        return True

    def _matches_district_criteria(self, allowed_districts: List[str], listing: PropertyListing) -> bool:
        """
        Validates whether a listing's location matches the allowed districts list.
        Uses explicit district/urban matching with mapped sub-neighborhood synonyms,
        preventing cross-district leakage from combined administrative names or random title mentions.
        """
        if not allowed_districts:
            return True

        listing_d_clean = (listing.district or "").strip().lower()
        listing_s_clean = (listing.street or "").strip().lower()
        listing_t_clean = (listing.title or "").strip().lower()

        # 1. Primary check on listing.district if present
        if listing_d_clean:
            for user_d in allowed_districts:
                ud_clean = user_d.strip().lower()
                synonyms = DISTRICT_SYNONYMS.get(user_d, [])
                allowed_targets = [ud_clean] + [syn.lower() for syn in synonyms]

                for target in allowed_targets:
                    if target == listing_d_clean or target in listing_d_clean or listing_d_clean in target:
                        return True
                    stem = target.rstrip("ი")
                    if len(stem) >= 3 and stem in listing_d_clean:
                        return True

            # If district is a recognized primary district but did not match allowed targets, reject immediately
            primary_districts = {
                "დიდუბე", "ჩუღურეთი", "ნაძალადევი", "გლდანი", "საბურთალო", "ვაკე",
                "დიდი დიღომი", "დიღმის მასივი", "ისანი", "სამგორი", "მთაწმინდა", "კრწანისი"
            }
            if listing_d_clean in primary_districts:
                return False

            # Otherwise, if district was custom/street name, check street as well
            if listing_s_clean:
                for user_d in allowed_districts:
                    ud_clean = user_d.strip().lower()
                    if ud_clean in listing_s_clean:
                        return True

            return False

        # 2. Fallback when listing.district is None (search street, title, and subdistrict)
        fallback_corpus = f"{listing_s_clean} {listing_t_clean} {(listing.subdistrict or '').lower()}".strip()
        if fallback_corpus:
            for user_d in allowed_districts:
                ud_clean = user_d.strip().lower()
                synonyms = DISTRICT_SYNONYMS.get(user_d, [])
                allowed_targets = [ud_clean] + [syn.lower() for syn in synonyms]
                for target in allowed_targets:
                    if target in fallback_corpus:
                        return True
                    stem = target.rstrip("ი")
                    if len(stem) >= 3 and stem in fallback_corpus:
                        return True

        return False

    def matches_user(self, user: UserSubscription, listing: PropertyListing) -> bool:
        """
        Validates whether a listing matches an active user's personalized subscription criteria:
        - Price range
        - Area range
        - Minimum rooms
        - District preferences
        """
        if not user.is_active:
            return False

        # Deal Type Check
        user_deal = getattr(user, "deal_type", "sale") or "sale"
        listing_deal = getattr(listing, "deal_type", "sale") or "sale"
        if user_deal != "both" and listing_deal != user_deal:
            return False

        # 1. Price
        is_rent = listing_deal == "rent"
        if is_rent and user_deal == "rent":
            min_p = user.price_min_usd if user.price_min_usd is not None else getattr(user, "rent_price_min_usd", 300)
            max_p = user.price_max_usd if user.price_max_usd is not None else getattr(user, "rent_price_max_usd", 1500)
        elif is_rent and getattr(user, "rent_price_min_usd", None) is not None:
            min_p = user.rent_price_min_usd
            max_p = getattr(user, "rent_price_max_usd", 1500)
        elif not is_rent:
            min_p = user.price_min_usd
            max_p = user.price_max_usd
        else:
            min_p = getattr(self.filters, "rent_price_min_usd", 300)
            max_p = getattr(self.filters, "rent_price_max_usd", 1500)

        if min_p is not None and listing.price_usd < min_p:
            return False
        if max_p is not None and listing.price_usd > max_p:
            return False

        # 2. Area
        if user.area_min_m2 is not None and listing.area_m2 < user.area_min_m2:
            return False
        if user.area_max_m2 is not None and listing.area_m2 > user.area_max_m2:
            return False

        # 3. Minimum Rooms
        if user.rooms_min:
            if listing.rooms is not None and listing.rooms < user.rooms_min:
                return False
            if user.rooms_min >= 2 and listing.bedrooms is not None and listing.bedrooms < 1:
                if listing.rooms is not None and listing.rooms < 2:
                    return False

        # 4. Districts
        if user.districts and len(user.districts) > 0:
            if not self._matches_district_criteria(user.districts, listing):
                return False

        # 5. Owner / Agent Preference Check
        user_owner_type = (getattr(user, "owner_type", "all") or "all").lower().strip()
        if user_owner_type in ["owner", "მესაკუთრე"]:
            if listing.is_owner is not True:
                return False
        elif user_owner_type in ["agent", "agency", "სააგენტო"]:
            if listing.is_owner is not False:
                return False

        return True


