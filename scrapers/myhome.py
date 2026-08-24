import json
import re
import urllib.parse
from typing import List, Optional
from core.models import PropertyListing, SearchFilters
from scrapers.base import BaseScraper


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        digits = re.search(r"\d+", str(val))
        return int(digits.group(0)) if digits else None


class MyHomeScraper(BaseScraper):
    def __init__(self, timeout: int = 15, max_pages: int = 3):
        super().__init__(name="MyHome.ge", timeout=timeout)
        self.max_pages = max_pages

    def _build_search_url(self, filters: SearchFilters, page: int = 1) -> str:
        if filters.myhome_url:
            parsed = urllib.parse.urlsplit(filters.myhome_url)
            query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            query_params["page"] = [str(page)]
            new_query = urllib.parse.urlencode(query_params, doseq=True, safe=",")
            return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))

        deal_path = "iyideba" if filters.deal_type == "sale" else "qiravdeba"
        deal_type_val = "1" if filters.deal_type == "sale" else "3"
        url = f"https://www.myhome.ge/udzravi-qoneba/{deal_path}/bina/tbilisi/?"
        params = [
            f"deal_types={deal_type_val}",
            "real_estate_types=1",
            "currency_id=2",
            "CardView=1",
            "area_types=1",
            "cities=1",
            f"page={page}",
        ]
        if filters.price_min_usd is not None:
            params.append(f"price_from={int(filters.price_min_usd)}")
        if filters.price_max_usd is not None:
            params.append(f"price_to={int(filters.price_max_usd)}")
        if filters.area_min_m2 is not None:
            params.append(f"area_from={int(filters.area_min_m2)}")
        if filters.area_max_m2 is not None:
            params.append(f"area_to={int(filters.area_max_m2)}")
        if filters.rooms:
            params.append(f"room_types={','.join(str(r) for r in filters.rooms)}")
        if filters.owner_type:
            params.append(f"owner_type={filters.owner_type}")

        return url + "&".join(params)

    def _extract_listings_from_html(self, html_text: str) -> List[dict]:
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.DOTALL)
        if not match:
            print("[MyHome.ge Error]: __NEXT_DATA__ script tag not found in HTML")
            return []

        try:
            data = json.loads(match.group(1))
            page_props = data.get("props", {}).get("pageProps", {})
            queries = page_props.get("dehydratedState", {}).get("queries", [])

            for q in queries:
                key = q.get("queryKey", [])
                if isinstance(key, list) and len(key) > 0 and str(key[0]) in ["statements", "search", "statementList", "listings"]:
                    st = q.get("state", {}).get("data", {})
                    if isinstance(st, dict):
                        inner = st.get("data", {})
                        if isinstance(inner, dict):
                            items = inner.get("data") or inner.get("items")
                            if isinstance(items, list):
                                return items
                        elif isinstance(inner, list):
                            return inner

            if "items" in page_props and isinstance(page_props["items"], list):
                return page_props["items"]
        except Exception as e:
            print(f"[MyHome.ge JSON Parsing Error]: {e}")

        return []

    def _normalize_item(self, item: dict, filters: Optional[SearchFilters] = None) -> Optional[PropertyListing]:
        try:
            source_id = str(item.get("id") or item.get("statement_id") or "")
            if not source_id:
                return None

            # Filter out non-matching deal types (e.g. daily rent promo items with deal_type_id == 7)
            deal_type_id = item.get("deal_type_id")
            if filters and filters.deal_type == "sale" and deal_type_id and str(deal_type_id) not in ["1", "3"]:
                return None

            # Price extraction (currency 2 is USD, 1 is GEL)
            price_usd = 0.0
            price_gel = None
            price_obj = item.get("price")

            if isinstance(price_obj, dict):
                # 2 is USD
                usd_info = price_obj.get("2") or price_obj.get(2)
                if isinstance(usd_info, dict) and "price_total" in usd_info:
                    try:
                        price_usd = float(usd_info["price_total"] or 0)
                    except (ValueError, TypeError):
                        pass

                # 1 is GEL
                gel_info = price_obj.get("1") or price_obj.get(1)
                if isinstance(gel_info, dict) and "price_total" in gel_info:
                    try:
                        price_gel = float(gel_info["price_total"] or 0)
                    except (ValueError, TypeError):
                        pass

            if price_usd <= 0 and "price_usd" in item:
                try:
                    price_usd = float(item.get("price_usd") or 0)
                except (ValueError, TypeError):
                    pass
            if price_usd <= 0 and "total_price" in item:
                try:
                    price_usd = float(item.get("total_price") or 0)
                except (ValueError, TypeError):
                    pass

            # Area extraction
            try:
                area_m2 = float(item.get("area") or item.get("area_size") or 0)
            except (ValueError, TypeError):
                area_m2 = 0.0

            if area_m2 <= 0 or price_usd <= 0:
                return None

            # Images
            images = []
            raw_images = item.get("images") or []
            if isinstance(raw_images, list):
                for img in raw_images:
                    if isinstance(img, dict):
                        url = img.get("large") or img.get("thumb") or img.get("url")
                        if url:
                            images.append(url)
                    elif isinstance(img, str):
                        images.append(img)

            # Details
            title = item.get("dynamic_title") or item.get("title") or item.get("user_title") or "ბინა MyHome-ზე"
            description = item.get("comment") or item.get("description") or ""
            city = item.get("city_name") or "თბილისი"
            district = item.get("urban_name") or item.get("district_name")
            subdistrict = item.get("district_name") if item.get("urban_name") != item.get("district_name") else None
            street = item.get("address") or item.get("street_address")
            floor = str(item.get("floor")) if item.get("floor") is not None else None
            total_floors = _safe_int(item.get("total_floors"))
            rooms = _safe_int(item.get("room"))
            bedrooms = _safe_int(item.get("bedroom"))
            published_at = item.get("last_updated") or item.get("create_date")

            return PropertyListing(
                id=f"myhome_{source_id}",
                source="myhome",
                source_id=source_id,
                title=title,
                description=description,
                price_usd=price_usd,
                price_gel=price_gel,
                area_m2=area_m2,
                city=city,
                district=district,
                subdistrict=subdistrict,
                street=street,
                floor=floor,
                total_floors=total_floors,
                rooms=rooms,
                bedrooms=bedrooms,
                url=f"https://www.myhome.ge/ka/pr/{source_id}",
                images=images,
                published_at=str(published_at) if published_at else None,
            )
        except Exception as e:
            print(f"[MyHome Normalization Error for item {item.get('id')}]: {e}")
            return None

    async def fetch_listings(self, filters: SearchFilters) -> List[PropertyListing]:
        all_listings: List[PropertyListing] = []
        for page in range(1, self.max_pages + 1):
            url = self._build_search_url(filters, page=page)
            html = await self.fetch_html(url)
            if not html:
                break

            raw_items = self._extract_listings_from_html(html)
            if not raw_items:
                break

            for raw in raw_items:
                normalized = self._normalize_item(raw, filters=filters)
                if normalized:
                    all_listings.append(normalized)

        return all_listings

    async def fetch_price_label(self, source_id: str) -> Optional[dict]:
        """
        Fetches the official MyHome price valuation scale metadata for a listing
        directly from the detail statement payload.
        """
        try:
            url = f"https://www.myhome.ge/ka/pr/{source_id}"
            html = await self.fetch_html(url)
            if not html:
                return None
            match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group(1))
            queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
            for q in queries:
                if "details" in str(q.get("queryKey")):
                    statement = q.get("state", {}).get("data", {}).get("data", {}).get("statement", {})
                    return statement.get("price_label")
        except Exception as e:
            print(f"[MyHome Price Label Error for {source_id}]: {e}")
        return None
