import json
import re
from typing import List, Optional
from core.models import PropertyListing, SearchFilters
from scrapers.base import BaseScraper


class AreaGeScraper(BaseScraper):
    def __init__(self, timeout: int = 8):
        super().__init__(name="Area.ge", timeout=timeout)

    def _build_search_url(self, filters: SearchFilters) -> str:
        offer_type = "1" if filters.deal_type == "sale" else "2"
        url = f"https://area.ge/ka/search?offer_types={offer_type}&property_types=1"
        params = []
        if filters.price_min_usd is not None:
            params.append(f"price_from={int(filters.price_min_usd)}")
        if filters.price_max_usd is not None:
            params.append(f"price_to={int(filters.price_max_usd)}")
        if params:
            url += "&" + "&".join(params)
        return url

    def _extract_listings_from_html(self, html_text: str) -> List[dict]:
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.DOTALL)
        if not match:
            return []

        try:
            data = json.loads(match.group(1))
            page_props = data.get("props", {}).get("pageProps", {})
            # Look for common statement containers
            statements = (
                page_props.get("statements")
                or page_props.get("items")
                or page_props.get("initialStatements", {}).get("data", [])
            )
            if isinstance(statements, list):
                return statements
        except Exception as e:
            print(f"[Area.ge JSON Parsing Error]: {e}")

        return []

    def _normalize_item(self, item: dict) -> Optional[PropertyListing]:
        try:
            source_id = str(item.get("id") or item.get("statement_id") or "")
            if not source_id:
                return None

            price_usd = float(item.get("price_usd") or item.get("price") or 0)
            area_m2 = float(item.get("area") or item.get("total_area") or 0)
            if price_usd <= 0 or area_m2 <= 0:
                return None

            title = item.get("title") or "ბინა Area.ge-ზე"
            description = item.get("description") or ""
            district = item.get("district_title") or item.get("district")
            street = item.get("street_title") or item.get("address")
            url = f"https://area.ge/ka/pr/{source_id}"

            images = []
            raw_images = item.get("images") or item.get("photos") or []
            if isinstance(raw_images, list):
                for img in raw_images:
                    if isinstance(img, dict) and "url" in img:
                        images.append(img["url"])
                    elif isinstance(img, str):
                        images.append(img)

            return PropertyListing(
                id=f"area_ge_{source_id}",
                source="area_ge",
                source_id=source_id,
                title=title,
                description=description,
                price_usd=price_usd,
                area_m2=area_m2,
                district=district,
                street=street,
                url=url,
                images=images,
            )
        except Exception as e:
            print(f"[Area.ge Normalization Error]: {e}")
            return None

    async def fetch_listings(self, filters: SearchFilters) -> List[PropertyListing]:
        try:
            url = self._build_search_url(filters)
            html = await self.fetch_html(url)
            if not html:
                return []

            raw_items = self._extract_listings_from_html(html)
            listings: List[PropertyListing] = []
            for raw in raw_items:
                normalized = self._normalize_item(raw)
                if normalized:
                    listings.append(normalized)
            return listings
        except Exception as e:
            print(f"[Area.ge Fetch Error]: {e}")
            return []
