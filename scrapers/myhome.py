import json
import re
import urllib.parse
from typing import List, Optional, Dict, Any, Tuple
from curl_cffi.requests import AsyncSession
from config import settings
from core.models import PropertyListing, SearchFilters
from scrapers.base import BaseScraper


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

# Mapping of Georgian district/neighborhood names to TNET statement API urban_ids
MYHOME_URBAN_MAP: Dict[str, List[int]] = {
    # 1. დიდუბე
    "დიდუბე": [23],
    "წერეთელი": [23],
    "წერეთლის გამზ.": [23],
    # 2. დიღმის მასივი
    "დიღმის მასივი": [24],
    "დიღმის მას.": [24],
    # 3. ჩუღურეთი
    "ჩუღურეთი": [27, 25, 26],
    "კუკია": [25],
    "სვანეთის უბანი": [26],
    "ივერთუბანი": [27],
    "მარჯანიშვილი": [27],
    "ვორონცოვი": [27],
    # 4. ნაძალადევი
    "ნაძალადევი": [10, 11, 6],
    "სან. ზონა": [11],
    "სანზონა": [11],
    "თემქა": [6],
    "ლოტკინი": [10],
    # 5. გლდანი
    "გლდანი": [2],
    "გლდანულა": [2],
    "ავჭალა": [2],
    # 6. ვაკე
    "ვაკე": [38, 28, 64],
    "ბაგები": [28],
    "კუს ტბა": [38],
    "ვერა": [64],
    "წყნეთი": [38],
    # 7. საბურთალო
    "საბურთალო": [47, 39, 46, 43, 101],
    "ვაშლიჯვარი": [39],
    "ვეძისი": [47],
    "თხინვალი": [47],
    "ლისი": [43],
    "ლისის მიმდებარედ": [101],
    "ნუცუბიძის ფერდობი": [46],
    "ნუცუბიძე": [46],
    "დელისი": [47],
    # 8. დიდი დიღომი
    "დიდი დიღომი": [29, 30, 48],
    "დიღომი 1-9": [30],
    "სოფ. დიღომი": [48],
    "დიღომი": [24, 29, 30, 48],
    # 9. ისანი
    "ისანი": [53, 62],
    "ავლაბარი": [62],
    "ნავთლუღი": [53],
    # 10. სამგორი
    "სამგორი": [59, 52, 51, 55, 78],
    "ვარკეთილი": [52],
    "ვაზისუბანი": [51],
    "მესამე მასივი": [55],
    "მოსკოვის გამზირი": [78],
    "მოსკოვის გამზ.": [78],
    # 11. მთაწმინდა
    "მთაწმინდა": [66, 67],
    "სოლოლაკი": [67],
    "რუსთაველი": [66],
    "თავისუფლება": [66],
    # 12. კრწანისი
    "კრწანისი": [65, 57],
    "ორთაჭალა": [57],
    # Parent dual districts
    "ვაკე-საბურთალო": [38, 28, 64, 47, 39, 46, 43, 101],
    "ისანი-სამგორი": [53, 62, 59, 52, 51, 55, 78],
    "გლდანი-ნაძალადევი": [2, 10, 11, 6],
    "დიდუბე-ჩუღურეთი": [23, 27, 25, 26],
    "ძველი თბილისი": [66, 67, 65, 57, 62, 64],
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
        matches = re.findall(
            r"(?:\+?995\s*|0)?(5\d{2}[\s.-]?\d{2}[\s.-]?\d{2}[\s.-]?\d{2}|5\d{8})", comment
        )
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
        self._api_disabled = False

    def _build_api_url(self, filters: SearchFilters, page: int = 1) -> str:
        if filters.deal_type == "both":
            deal_type_val = "1,2"
        elif filters.deal_type == "sale":
            deal_type_val = "1"
        else:
            deal_type_val = "2"
        params = [
            "locale=ka",
            f"deal_types={deal_type_val}",
            "real_estate_types=1",
            "currency_id=2",
            "cities=1",
            "statuses=1,2",  # Exclude status 3 (under construction) at API level
            f"page={page}",
        ]
        if filters.deal_type != "rent":
            params.append(
                "conditions=1,2,3,5,8"
            )  # Exclude condition 6 (black frame) and 4 at API level

        min_p = (
            filters.rent_price_min_usd
            if filters.deal_type == "rent" and filters.rent_price_min_usd is not None
            else filters.price_min_usd
        )
        max_p = (
            filters.rent_price_max_usd
            if filters.deal_type == "rent" and filters.rent_price_max_usd is not None
            else filters.price_max_usd
        )
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

        target_districts = filters.whitelist_districts or filters.target_districts
        if target_districts:
            urban_ids = set()
            for d in target_districts:
                d_clean = str(d).strip()
                if d_clean in MYHOME_URBAN_MAP:
                    urban_ids.update(MYHOME_URBAN_MAP[d_clean])
                else:
                    for k, uids in MYHOME_URBAN_MAP.items():
                        if k.lower() == d_clean.lower():
                            urban_ids.update(uids)
                            break
            if urban_ids:
                params.append(f"urbans={','.join(str(u) for u in sorted(urban_ids))}")

        return f"{self.API_BASE}?{'&'.join(params)}"

    def _build_search_url(self, filters: SearchFilters, page: int = 1) -> str:
        if filters.myhome_url:
            parsed = urllib.parse.urlsplit(filters.myhome_url)
            query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            query_params["page"] = [str(page)]
            new_query = urllib.parse.urlencode(query_params, doseq=True, safe=",")
            return urllib.parse.urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment)
            )

        deal_path = "qiravdeba" if filters.deal_type == "rent" else "iyideba"
        if filters.deal_type == "both":
            deal_type_val = "1,2"
        elif filters.deal_type == "sale":
            deal_type_val = "1"
        else:
            deal_type_val = "2"
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

        min_p = (
            filters.rent_price_min_usd
            if filters.deal_type == "rent" and filters.rent_price_min_usd is not None
            else filters.price_min_usd
        )
        max_p = (
            filters.rent_price_max_usd
            if filters.deal_type == "rent" and filters.rent_price_max_usd is not None
            else filters.price_max_usd
        )
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

        target_districts = filters.whitelist_districts or filters.target_districts
        if target_districts:
            urban_ids = set()
            for d in target_districts:
                d_clean = str(d).strip()
                if d_clean in MYHOME_URBAN_MAP:
                    urban_ids.update(MYHOME_URBAN_MAP[d_clean])
                else:
                    for k, uids in MYHOME_URBAN_MAP.items():
                        if k.lower() == d_clean.lower():
                            urban_ids.update(uids)
                            break
            if urban_ids:
                params.append(f"urbans={','.join(str(u) for u in sorted(urban_ids))}")

        return url + "&".join(params)

    async def _fetch_api_page(self, url: str) -> Optional[List[dict]]:
        """Queries the TNET statements JSON API, routing through Cloudflare proxy when configured."""
        target_url = url
        req_headers = dict(self.API_HEADERS)
        timeout = self.timeout

        if settings.CLOUDFLARE_PROXY_URL:
            target_url = (
                f"{settings.CLOUDFLARE_PROXY_URL.rstrip('/')}/?url={urllib.parse.quote_plus(url)}"
            )
            req_headers = {}
            timeout = max(self.timeout, 30)

        try:
            async with AsyncSession(impersonate="chrome124") as session:
                response = await session.get(
                    target_url, headers=req_headers if req_headers else None, timeout=timeout
                )
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
                if (
                    isinstance(key, list)
                    and len(key) > 0
                    and str(key[0]) in ["statements", "search", "statementList", "listings"]
                ):
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

    def extract_statement_from_html(self, html: Optional[str]) -> Optional[dict]:
        """Extracts the statement detail dictionary from Next.js SSR dehydratedState."""
        if not html:
            return None
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
            page_props = data.get("props", {}).get("pageProps", {})
            queries = page_props.get("dehydratedState", {}).get("queries", [])
            for q in queries:
                st_data = q.get("state", {}).get("data", {})
                if isinstance(st_data, dict):
                    inner = st_data.get("data", {})
                    if isinstance(inner, dict):
                        statement = inner.get("statement")
                        if isinstance(statement, dict) and statement.get("id"):
                            return statement
            if "statement" in page_props and isinstance(page_props["statement"], dict):
                return page_props["statement"]
        except Exception as e:
            print(f"[MyHome Statement HTML Extraction Error]: {e}")
        return None

    @staticmethod
    def _extract_prices(item: dict) -> Tuple[float, Optional[float]]:
        """
        Robustly extracts (price_usd, price_gel) from MyHome listing or detail dict.
        Respects currency 2 (USD) and currency 1 (GEL), and handles currency_id field correctly.
        """
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

        if "total_price" in item:
            try:
                raw_val = float(item.get("total_price") or 0)
                cid = item.get("currency_id") or item.get("statement_currency_id")
                if str(cid) == "2" and price_usd <= 0:
                    price_usd = raw_val
                elif str(cid) == "1" and not price_gel:
                    price_gel = raw_val
                elif str(cid) == "3" and price_usd <= 0:
                    price_usd = round(raw_val * 1.08, 2)
                elif not cid and price_usd <= 0:
                    price_usd = raw_val
            except (ValueError, TypeError):
                pass

        # Fallback GEL -> USD conversion if only GEL is available
        if price_usd <= 0 and price_gel and price_gel > 0:
            price_usd = round(price_gel / 2.65, 2)

        return price_usd, price_gel

    async def fetch_statement_details(self, source_id: str) -> Optional[dict]:
        """
        Enriches a listing with exact detail fields from statement page or details endpoint:
        condition_id, condition, status_id, metro_station_id, user_phone_number, price_label,
        and authoritative live price (price_usd, price_gel).
        """
        st_data = None
        # Primary Strategy: Fetch listing detail page via HTML SSR (contains full dehydrated state)
        detail_page_url = f"https://www.myhome.ge/ka/pr/{source_id}"
        html = await self.fetch_html(detail_page_url)
        if html:
            st = self.extract_statement_from_html(html)
            if st:
                st_data = st

        if not st_data:
            # Fallback Strategy: JSON API endpoint
            url = f"{self.API_BASE}/{source_id}?locale=ka"
            target_url = url
            req_headers = dict(self.API_HEADERS)
            timeout = 10

            if settings.CLOUDFLARE_PROXY_URL:
                target_url = f"{settings.CLOUDFLARE_PROXY_URL.rstrip('/')}/?url={urllib.parse.quote_plus(url)}"
                req_headers = {}
                timeout = 30

            try:
                async with AsyncSession(impersonate="chrome124") as session:
                    response = await session.get(
                        target_url, headers=req_headers if req_headers else None, timeout=timeout
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        st_data = payload.get("data", {}).get("statement")
            except Exception as e:
                print(f"[MyHome.ge Statement Detail Error for {source_id}]: {e}")

        if st_data and isinstance(st_data, dict):
            usd, gel = self._extract_prices(st_data)
            st_data["price_usd"] = usd
            st_data["price_gel"] = gel

        return st_data

    def _normalize_item(
        self, item: dict, filters: Optional[SearchFilters] = None
    ) -> Optional[PropertyListing]:
        try:
            source_id = str(item.get("id") or item.get("statement_id") or "")
            if not source_id:
                return None

            # Real estate type validation (1 is apartment/flat)
            ret_id = item.get("real_estate_type_id")
            if ret_id is not None and str(ret_id) != "1":
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
            price_usd, price_gel = self._extract_prices(item)

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
            title = (
                item.get("dynamic_title")
                or item.get("title")
                or item.get("user_title")
                or "ბინა MyHome-ზე"
            )
            description = item.get("comment") or item.get("description") or ""
            city = item.get("city_name") or "თბილისი"
            urban_name = item.get("urban_name")
            district_name = item.get("district_name")
            district = urban_name or district_name

            parent_dual_districts = {
                "ვაკე-საბურთალო",
                "გლდანი-ნაძალადევი",
                "დიდუბე-ჩუღურეთი",
                "ისანი-სამგორი",
                "ძველი თბილისი",
                "თბილისის შემოგარენი",
            }
            subdistrict = (
                district_name
                if urban_name
                and district_name
                and district_name != urban_name
                and district_name not in parent_dual_districts
                else None
            )
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

    # MyHome returns all promoted (Super-VIP / VIP+ / VIP) listings before regular
    # ones, so keep paging past max_pages until enough regular listings are seen.
    MIN_REGULAR_ITEMS = 15
    MAX_PAGES_HARD = 5

    async def fetch_listings(self, filters: SearchFilters) -> List[PropertyListing]:
        all_listings: List[PropertyListing] = []
        regular_seen = 0
        use_api = not getattr(self, "_api_disabled", False)

        for page in range(1, self.MAX_PAGES_HARD + 1):
            raw_items = None
            # 1. Primary Strategy: Direct JSON API (disable for remaining pages/envelopes if it fails)
            if use_api:
                api_url = self._build_api_url(filters, page=page)
                raw_items = await self._fetch_api_page(api_url)
                if not raw_items:
                    use_api = False
                    self._api_disabled = True

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

            regular_seen += sum(
                1
                for raw in raw_items
                if not (raw.get("is_super_vip") or raw.get("is_vip_plus") or raw.get("is_vip"))
            )
            if page >= self.max_pages and regular_seen >= self.MIN_REGULAR_ITEMS:
                break

        return all_listings

    async def fetch_price_label(self, source_id: str) -> Optional[dict]:
        """Fetches the official MyHome price valuation scale metadata directly from details API."""
        details = await self.fetch_statement_details(source_id)
        if details and isinstance(details, dict):
            return details.get("price_label")
        return None
