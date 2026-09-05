from datetime import datetime, timezone
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator


PortalType = Literal["myhome", "ss_ge", "area_ge"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SearchFilters(BaseModel):
    city: str = "თბილისი"
    deal_type: Literal["sale", "rent"] = "sale"
    price_min_usd: Optional[float] = 48000
    price_max_usd: Optional[float] = 72000
    area_min_m2: Optional[float] = 48
    area_max_m2: Optional[float] = 62
    price_per_m2_max_usd: Optional[float] = None
    rooms: Optional[List[int]] = None
    rooms_min: int = 2
    owner_type: Optional[str] = None
    myhome_url: Optional[str] = None
    target_metro_ids: List[int] = Field(
        default_factory=lambda: [1, 2, 3, 7, 9, 10, 16, 17, 18, 19, 21, 22]
    )
    whitelist_districts: List[str] = Field(
        default_factory=lambda: ["დიდუბე", "ნაძალადევი", "ჩუღურეთი", "ისანი", "გლდანი"]
    )
    blacklist_keywords: List[str] = Field(
        default_factory=lambda: [
            "დიდი დიღომი", "მუხიანი", "აფრიკა", "დამპალო",
            "ზემო პლატო", "3-ე პლატო", "მე-3 პლატო", "მე-4 პლატო",
            "ორთაჭალის ზემოთ", "ორთაჭალის გორა"
        ]
    )
    target_districts: List[str] = Field(default_factory=list)
    stop_words: List[str] = Field(
        default_factory=lambda: [
            "ჩაბარდება", "ბარდება", "2027", "2028", "2029",
            "სართული დაშენება", "დაშენების პერსპექტივით", "იტალიური ეზო",
            "ნახევარსარდაფი", "სარდაფი", "შავი კარკასი"
        ]
    )
    white_frame_max_price: float = 55000.0
    hot_deal_price_per_sqm: float = 1350.0
    require_images: bool = False


class PropertyListing(BaseModel):
    id: str
    source: PortalType
    source_id: str
    title: str
    description: Optional[str] = None
    price_usd: float
    price_gel: Optional[float] = None
    area_m2: float
    price_per_m2: float = 0.0
    city: str = "თბილისი"
    district: Optional[str] = None
    subdistrict: Optional[str] = None
    street: Optional[str] = None
    floor: Optional[str] = None
    total_floors: Optional[int] = None
    rooms: Optional[int] = None
    bedrooms: Optional[int] = None
    url: str
    images: List[str] = Field(default_factory=list)
    published_at: Optional[str] = None
    scraped_at: datetime = Field(default_factory=utc_now)
    metro_station_id: Optional[int] = None
    metro_station_name: Optional[str] = None
    condition_id: Optional[int] = None
    condition_name: Optional[str] = None
    status_id: Optional[int] = None
    user_type: Optional[str] = None
    is_owner: Optional[bool] = None
    phone_number: Optional[str] = None
    is_hot_deal: bool = False
    is_bargain: bool = False
    market_avg_price_m2: Optional[float] = None
    market_median_price_m2: Optional[float] = None
    market_iqr_p25: Optional[float] = None
    market_iqr_p75: Optional[float] = None
    market_sample_count: Optional[int] = None
    discount_pct: Optional[float] = None
    price_status_label: Optional[str] = None
    valuation_scale_label: Optional[str] = None
    valuation_scale_tier: Optional[int] = None
    valuation_scale_visual: Optional[str] = None

    @model_validator(mode="after")
    def compute_fields(self):
        if self.area_m2 > 0 and self.price_usd > 0 and self.price_per_m2 <= 0:
            self.price_per_m2 = round(self.price_usd / self.area_m2, 2)
        if not self.id:
            self.id = f"{self.source}_{self.source_id}"
        return self


class DistrictPriceStats(BaseModel):
    district: str
    sample_count: int
    avg_price_per_m2: float
    median_price_per_m2: float
    iqr_p25: Optional[float] = None
    iqr_p75: Optional[float] = None
    min_price_per_m2: float
    max_price_per_m2: float
    std_dev: Optional[float] = None
    room_medians: dict = Field(default_factory=dict)
