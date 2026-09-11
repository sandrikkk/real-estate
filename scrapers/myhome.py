import json
import re
import urllib.parse
from typing import List, Optional, Dict, Any
from curl_cffi.requests import AsyncSession
from core.models import PropertyListing, SearchFilters
from scrapers.base import BaseScraper
from config import settings


METRO_STATIONS: Dict[int, str] = {
    1: "სახელმწიფო უნივერსიტეტი",
    2: "ვაჟა-ფშაველა",
    3: "დელისი",
    4: "სამედიცინო უნივერსიტეტი",
    5: "ტექნიკური უნივერსიტეტი",
    6: "წერეთელი",
    7: "სადგურის მოედანი",
    8: "მარჯანიშვილი",
    9: "სამგორი",
    10: "ისანი",
    11: "300 არაგველი",
    12: "ავლაბარი",
    13: "თავისუფლების მოედანი",
    14: "რუსთაველი",
    15: "პირველი რესპუბლიკა",
    16: "ნაძალადევი",
    17: "გოცირიძე",
    18: "დიდუბე",
    19: "ღრმაღელე",
    20: "გურამიშვილი",
    21: "სარაჯიშვილი",
    22: "ახმეტელის თეატრი",
    23: "ვარკეთილი",
}

CONDITIONS: Dict[int, str] = {
    1: "ახალი გარემონტებული",
    2: "ძველი გარემონტებული",
    3: "მიმდინარე რემონტი",
    4: "სარემონტო",
    5: "თეთრი კარკასი",
    6: "შავი კარკასი",
    7: "მწვანე კარკასი",
    8: "თეთრი პლიუსი",
}

BUILDING_STATUSES: Dict[int, str] = {
    1: "ძველი აშენებული",
    2: "ახალი აშენებული",
    3: "მშენებარე",
}


def _safe_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        digits = re.search(r"\d+", str(val))
        return int(digits.group(0)) if digits else None


def _extract_phone_number(raw_phone: Optional[str], comment: Optional[str]) -> Optional[str]:
    """
    Extracts phone number from comment (unmasked seller contact) or falls back to user_phone_number.
    Standardizes format to '+995 XXX XX XX XX'.
    """
    if comment:
        # Match Georgian 9-digit numbers starting with 5, optionally with +995 or 0 prefix
        matches = re.findall(r"(?:\+?995\s*|0)?(5\d{2}[\s.-]?\d{2}[\s.-]?\d{2}[\s.-]?\d{2}|5\d{8})", comment)
        for m in matches:
            clean = re.sub(r"\D", "", m)
            if len(clean) == 9 and clean.startswith("5"):
                return f"+995 {clean[:3]} {clean[3:5]} {clean[5:7]} {clean[7:]}"

    if raw_phone:
        raw_str = str(raw_phone).strip()
        if "*" not in raw_str:
            clean = re.sub(r"\D", "", raw_str)
            if len(clean) == 9 and clean.startswith("5"):
                return f"+995 {clean[:3]} {clean[3:5]} {clean[5:7]} {clean[7:]}"
            elif len(clean) == 12 and clean.startswith("995"):
                return f"+{clean[:3]} {clean[3:6]} {clean[6:8]} {clean[8:10]} {clean[10:]}"
            return raw_str
        return raw_str
    return None


class MyHomeScraper(BaseScraper):
    API_BASE = "https://api-statements.tnet.ge/v1/statements"
    API_HEADERS = {
        "locale": "ka",
        "X-Website-Key": "myhome",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    }

    def __init__(self, timeout: int = 15, max_pages: int = 3):
        super().__init__(name="MyHome.ge", timeout=timeout)
        self.max_pages = max_pages

    def _build_api_url(self, filters: SearchFilters, page: int = 1) -> str:
        deal_type_val = "1" if filters.deal_type == "sale" else "2"
        params = [
            "locale=ka",
            f"deal_types={deal_type_val}",
            "real_estate_type_id=1",
            "currency_id=2",
            "cities=1",
            "statuses=1,2",           # Exclude status 3 (under construction) at API level
            f"page={page}",
        ]
        if filters.deal_type != "rent":
            params.append("conditions=1,2,3,5,8")     # Exclude condition 6 (black frame) and 4 at API level

        min_p = (filters.rent_price_min_usd if filters.deal_type == "rent" and filters.rent_price_min_usd is not None else filters.price_min_usd)
        max_p = (filters.rent_price_max_usd if filters.deal_type == "rent" and filters.rent_price_max_usd is not None else filters.price_max_usd)
        if min_p is not None:
            params.append(f"price_from={int(min_p)}")
        if max_p is not None:
            params.append(f"price_to={int(max_p)}")
        if filters.area_min_m2 is not None:
            params.append(f"area_from={int(filters.area_min_m2)}")
        if filters.area_max_m2 is not None:
            params.append(f"area_to={int(filters.area_max_m2)}")
        if filters.owner_type:
            ot = str(filters.owner_type).lower().strip()
            if ot in ["owner", "physical", "მესაკუთრე"]:
                params.append("owner_type=physical")
            elif ot in ["agent", "agency", "სააგენტო"]:
                params.append("owner_type=agent")

        return f"{self.API_BASE}?{'&'.join(params)}"

    def _build_search_url(self, filters: SearchFilters, page: int = 1) -> str:
        if filters.myhome_url:
            parsed = urllib.parse.urlsplit(filters.myhome_url)
            query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            query_params["page"] = [str(page)]
            new_query = urllib.parse.urlencode(query_params, doseq=True, safe=",")
            return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))

        deal_path = "iyideba" if filters.deal_type == "sale" else "qiravdeba"
        deal_type_val = "1" if filters.deal_type == "sale" else "2"
        url = f"https://www.myhome.ge/ka/s/{deal_path}-bina-tbilisi/?"
        params = [
            f"deal_types={deal_type_val}",
            "real_estate_types=1",
            "currency_id=2",
            "CardView=1",
            "area_types=1",
            "cities=1",
            "statuses=1,2",
            f"page={page}",
        ]
        if filters.deal_type != "rent":
            params.append("conditions=1,2,3,5,8")

        min_p = (filters.rent_price_min_usd if filters.deal_type == "rent" and filters.rent_price_min_usd is not None else filters.price_min_usd)
        max_p = (filters.rent_price_max_usd if filters.deal_type == "rent" and filters.rent_price_max_usd is not None else filters.price_max_usd)
        if min_p is not None:
            params.append(f"price_from={int(min_p)}")
        if max_p is not None:
            params.append(f"price_to={int(max_p)}")
        if filters.area_min_m2 is not None:
            params.append(f"area_from={int(filters.area_min_m2)}")
        if filters.area_max_m2 is not None:
            params.append(f"area_to={int(filters.area_max_m2)}")
        if filters.owner_type:
            ot = str(filters.owner_type).lower().strip()
            if ot in ["owner", "physical", "მესაკუთრე"]:
                params.append("owner_type=physical")
            elif ot in ["agent", "agency", "სააგენტო"]:
                params.append("owner_type=agent")

        return url + "&".join(params)

    async def _fetch_api_page(self, url: str) -> Optional[List[dict]]:
        """Directly queries the TNET statements JSON API using impersonated TLS session."""
        try:
            async with AsyncSession(impersonate="chrome124") as session:
                response = await session.get(url, headers=self.API_HEADERS, timeout=self.timeout)
                if response.status_code == 200:
                    payload = response.json()
                    data = payload.get("data", {})
                    items = data.get("data") if isinstance(data, dict) else None
                    if isinstance(items, list):
                        return items
                else:
                    print(f"[MyHome.ge API Warning]: Status {response.status_code} for API query")
        except Exception as e:
            print(f"[MyHome.ge API Error]: {e}")
        return None

    def _extract_listings_from_html(self, html_text: str) -> List[dict]:
        """Fallback SSR HTML parser using __NEXT_DATA__ dehydrated state."""
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.DOTALL)
        if not match:
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

    async def fetch_statement_details(self, source_id: str) -> Optional[dict]:
        """
        Enriches a listing with exact detail fields from the statement details endpoint:
        condition_id, condition, status_id, metro_station_id, user_phone_number, price_label.
        """
        url = f"{self.API_BASE}/{source_id}?locale=ka"
        try:
            async with AsyncSession(impersonate="chrome124") as session:
                response = await session.get(url, headers=self.API_HEADERS, timeout=10)
                if response.status_code == 200:
                    payload = response.json()
                    return payload.get("data", {}).get("statement")
        except Exception as e:
            print(f"[MyHome.ge Statement Detail Error for {source_id}]: {e}")
        return None

    def _normalize_item(self, item: dict, filters: Optional[SearchFilters] = None) -> Optional[PropertyListing]:
        try:
            source_id = str(item.get("id") or item.get("statement_id") or "")
            if not source_id:
                return None

            # Deal type validation
            deal_type_id = item.get("deal_type_id")
            deal_type = "rent" if str(deal_type_id) in ["2", "4"] else "sale"
            if filters:
                if filters.deal_type == "sale" and deal_type != "sale":
                    return None
                if filters.deal_type == "rent" and deal_type != "rent":
                    return None

            # Price extraction (USD is currency 2, GEL is currency 1)
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

            # Fallback GEL -> USD conversion if only GEL is available
            if price_usd <= 0 and price_gel and price_gel > 0:
                price_usd = round(price_gel / 2.65, 2)

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

            # Metadata
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

            # Metro Station
            metro_id = _safe_int(item.get("metro_station_id"))
            metro_name = METRO_STATIONS.get(metro_id) if metro_id else None

            # Condition & Status
            condition_id = _safe_int(item.get("condition_id"))
            condition_name = CONDITIONS.get(condition_id) if condition_id else None
            status_id = _safe_int(item.get("status_id"))

            # User Type & Ownership
            raw_user_type = item.get("user_type")
            user_type_str = None
            if isinstance(raw_user_type, dict):
                user_type_str = raw_user_type.get("type")
            elif isinstance(raw_user_type, str):
                user_type_str = raw_user_type

            is_owner = None
            if "is_owner" in item and item["is_owner"] is not None:
                is_owner = bool(item["is_owner"])
            elif user_type_str == "physical":
                is_owner = True
            elif user_type_str in ["agency", "developer", "agent", "broker"]:
                is_owner = False

            # Phone Number
            phone_number = _extract_phone_number(item.get("user_phone_number"), description)

            return PropertyListing(
                id=f"myhome_{source_id}",
                source="myhome",
                source_id=source_id,
                deal_type=deal_type,
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
                metro_station_id=metro_id,
                metro_station_name=metro_name,
                condition_id=condition_id,
                condition_name=condition_name,
                status_id=status_id,
                user_type=user_type_str,
                is_owner=is_owner,
                phone_number=phone_number,
            )
        except Exception as e:
            print(f"[MyHome Normalization Error for item {item.get('id')}]: {e}")
            return None

    async def fetch_listings(self, filters: SearchFilters) -> List[PropertyListing]:
        all_listings: List[PropertyListing] = []

        for page in range(1, self.max_pages + 1):
            raw_items = None
            # 1. Primary Strategy: Direct JSON API
            api_url = self._build_api_url(filters, page=page)
            raw_items = await self._fetch_api_page(api_url)

            # 2. Fallback Strategy: SSR HTML Parsing
            if not raw_items:
                search_url = self._build_search_url(filters, page=page)
                html = await self.fetch_html(search_url)
                if html:
                    raw_items = self._extract_listings_from_html(html)

            if not raw_items:
                break

            for raw in raw_items:
                normalized = self._normalize_item(raw, filters=filters)
                if normalized:
                    all_listings.append(normalized)

        return all_listings

    async def fetch_price_label(self, source_id: str) -> Optional[dict]:
        """Fetches the official MyHome price valuation scale metadata directly from details API."""
        details = await self.fetch_statement_details(source_id)
        if details and isinstance(details, dict):
            return details.get("price_label")
        return None
