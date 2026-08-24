from datetime import datetime, timezone
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator


PortalType = Literal["myhome", "ss_ge", "area_ge"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SearchFilters(BaseModel):
    city: str = "თბილისი"
    deal_type: Literal["sale", "rent"] = "sale"
    price_min_usd: Optional[float] = 45000
    price_max_usd: Optional[float] = 65000
    area_min_m2: Optional[float] = 40
    area_max_m2: Optional[float] = 60
    price_per_m2_max_usd: Optional[float] = None
    rooms: Optional[List[int]] = None
    owner_type: Optional[str] = None
    myhome_url: Optional[str] = None
    target_districts: List[str] = Field(default_factory=list)
    stop_words: List[str] = Field(default_factory=list)
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
