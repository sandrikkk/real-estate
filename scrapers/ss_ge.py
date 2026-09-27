import json
import re
import urllib.parse
from typing import List, Optional, Dict, Any
from curl_cffi.requests import AsyncSession
from core.models import PropertyListing, SearchFilters
from scrapers.base import BaseScraper
from scrapers.myhome import (
    METRO_STATIONS,
    CONDITIONS,
    BUILDING_STATUSES,
    _safe_int,
    _extract_phone_number,
)


class SSGeScraper(BaseScraper):
    API_BASE = "https://api-statements.tnet.ge/v1/statements"
    API_HEADERS = {
        "locale": "ka",
        "X-Website-Key": "ss",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    }

    def __init__(self, timeout: int = 15, max_pages: int = 3):
        super().__init__(name="SS.ge", timeout=timeout)
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
        deal_path = "iyideba" if filters.deal_type == "sale" else "qiravdeba"
        url = f"https://home.ss.ge/ka/udzravi-qoneba/l/bina/{deal_path}?city=1&priceType=1&page={page}"

        params = []
        min_p = (filters.rent_price_min_usd if filters.deal_type == "rent" and filters.rent_price_min_usd is not None else filters.price_min_usd)
        max_p = (filters.rent_price_max_usd if filters.deal_type == "rent" and filters.rent_price_max_usd is not None else filters.price_max_usd)
        if min_p is not None:
            params.append(f"priceFrom={int(min_p)}")
        if max_p is not None:
            params.append(f"priceTo={int(max_p)}")
        if filters.area_min_m2 is not None:
            params.append(f"totalAreaFrom={int(filters.area_min_m2)}")
        if filters.area_max_m2 is not None:
            params.append(f"totalAreaTo={int(filters.area_max_m2)}")
        if filters.owner_type:
            ot = str(filters.owner_type).lower().strip()
            if ot in ["owner", "physical", "მესაკუთრე"]:
                params.append("individualType=1")
            elif ot in ["agent", "agency", "სააგენტო"]:
                params.append("individualType=2")

        if params:
            url += "&" + "&".join(params)
        return url

    async def _fetch_api_page(self, url: str) -> Optional[List[dict]]:
        """Directly queries the TNET statements JSON API for SS.ge using impersonated TLS session."""
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
                    print(f"[SS.ge API Warning]: Status {response.status_code} for API query")
        except Exception as e:
            print(f"[SS.ge API Error]: {e}")
        return None

    def _extract_listings_from_html(self, html_text: str) -> List[dict]:
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.DOTALL)
        if not match:
            print("[SS.ge Error]: __NEXT_DATA__ script tag not found in HTML")
            return []

        try:
            data = json.loads(match.group(1))
            page_props = data.get("props", {}).get("pageProps", {})
            app_list = page_props.get("applicationList", {})
            if isinstance(app_list, dict):
                items = app_list.get("realStateItemModel") or app_list.get("applications") or []
                if isinstance(items, list):
                    return items
            elif isinstance(app_list, list):
                return app_list
        except Exception as e:
            print(f"[SS.ge JSON Parsing Error]: {e}")

        return []

    async def fetch_statement_details(self, source_id_or_url: str) -> Optional[dict]:
        """
        Enriches an SS.ge listing with verified details from TNET statements API or Next.js dehydrated state:
        is_owner, agency_id, agency_name, user_entity_type, phone_number, condition_id, condition, price_label.
        """
        # Extract numeric statement ID safely without query parameters or hash fragments
        clean_source = str(source_id_or_url).split("?")[0].split("#")[0].rstrip("/")
        digits = re.findall(r"\d+", clean_source)
        stmt_id = digits[-1] if digits else str(source_id_or_url)

        # 1. Primary Strategy: TNET Statement Details JSON API
        if stmt_id.isdigit():
            api_url = f"{self.API_BASE}/{stmt_id}?locale=ka"
            req_timeout = min(self.timeout, 8)
            try:
                async with AsyncSession(impersonate="chrome124") as session:
                    response = await session.get(api_url, headers=self.API_HEADERS, timeout=req_timeout)
                    if response.status_code == 200:
                        payload = response.json()
                        stmt_data = payload.get("data", {}).get("statement") or {}
                        if stmt_data:
                            user_type_data = stmt_data.get("user_type") or {}
                            user_type_str = (
                                user_type_data.get("type")
                                if isinstance(user_type_data, dict)
                                else str(user_type_data or "").lower()
                            )
                            agency_id = stmt_data.get("agency_id") or stmt_data.get("externalCompanyId")
                            agency_name = stmt_data.get("agency_name") or stmt_data.get("companyName")
                            app_count = stmt_data.get("user_statements_count") or 0

                            is_owner = None
                            if (
                                agency_id
                                or agency_name
                                or user_type_str in ["agent", "agency", "broker", "developer", "company"]
                                or (isinstance(app_count, int) and app_count > 10)
                            ):
                                is_owner = False
                            elif user_type_str == "physical" and not agency_id and not agency_name:
                                is_owner = True

                            phone_raw = stmt_data.get("user_phone_number")
                            comment = stmt_data.get("comment") or ""
                            clean_phone = _extract_phone_number(phone_raw, comment)

                            cond_obj = stmt_data.get("condition")
                            cond_name = cond_obj.get("name") if isinstance(cond_obj, dict) else (cond_obj or None)

                            res = dict(stmt_data)
                            res.update({
                                "is_owner": is_owner,
                                "agency_id": agency_id,
                                "agency_name": agency_name,
                                "user_entity_type": user_type_str,
                                "phone_number": clean_phone,
                                "user_phone_number": clean_phone,
                                "condition_id": stmt_data.get("condition_id"),
                                "condition": stmt_data.get("condition"),
                                "condition_name": cond_name,
                                "status_id": stmt_data.get("status_id"),
                                "price_label": stmt_data.get("price_label"),
                                "metro_station_id": stmt_data.get("metro_station_id"),
                                "comment": comment,
                            })
                            return res
            except Exception as e:
                print(f"[SS.ge API Statement Detail Error for {stmt_id}]: {e}")

        # 2. Fallback Strategy: SSR HTML Scraping
        if str(source_id_or_url).startswith("http"):
            url = str(source_id_or_url)
        else:
            url = f"https://home.ss.ge/ka/udzravi-qoneba/{source_id_or_url}"

        try:
            async with AsyncSession(impersonate="chrome124") as session:
                response = await session.get(url, timeout=self.timeout)
                if response.status_code == 200:
                    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', response.text, re.DOTALL)
                    if match:
                        data = json.loads(match.group(1))
                        props = data.get("props", {}).get("pageProps", {})
                        app_data = props.get("applicationData") or {}
                        agent_info = props.get("initialAgentInfoData") or {}

                        agency_id = app_data.get("agencyId") or app_data.get("externalCompanyId") or agent_info.get("companyId")
                        agency_name = app_data.get("agencyName") or app_data.get("companyName") or agent_info.get("companyName")
                        user_entity_type = str(app_data.get("userEntityType") or "").lower()
                        company_type = agent_info.get("companyType")
                        app_count = app_data.get("userApplicationCount") or 0

                        is_owner = None
                        if (
                            agency_id
                            or agency_name
                            or "broker" in user_entity_type
                            or "agent" in user_entity_type
                            or "agency" in user_entity_type
                            or "company" in user_entity_type
                            or company_type == 2
                            or (isinstance(app_count, int) and app_count > 10)
                        ):
                            is_owner = False
                        elif "individual" in user_entity_type and not agency_id and not agency_name:
                            is_owner = True
                        elif app_data.get("isOwner") is not None:
                            is_owner = bool(app_data.get("isOwner"))
                        elif app_data.get("isAgency") is not None:
                            is_owner = not bool(app_data.get("isAgency"))

                        # Phone extraction
                        phones = app_data.get("applicationPhones") or []
                        phone_number = None
                        if phones and isinstance(phones, list) and len(phones) > 0:
                            first_p = phones[0]
                            if isinstance(first_p, dict) and first_p.get("phoneNumber"):
                                phone_number = first_p.get("phoneNumber")
                            elif isinstance(first_p, str):
                                phone_number = first_p

                        return {
                            "is_owner": is_owner,
                            "agency_id": agency_id,
                            "agency_name": agency_name,
                            "user_entity_type": user_entity_type,
                            "phone_number": phone_number,
                            "user_phone_number": phone_number,
                        }
        except Exception as e:
            print(f"[SS.ge Statement Detail Error for {source_id_or_url}]: {e}")
        return None

    def _normalize_item(self, item: dict, filters: Optional[SearchFilters] = None) -> Optional[PropertyListing]:
        try:
            source_id = str(item.get("id") or item.get("statement_id") or item.get("applicationId") or "")
            if not source_id:
                return None

            # Deal type validation
            deal_type_id = item.get("deal_type_id")
            if deal_type_id is not None:
                deal_type = "rent" if str(deal_type_id) in ["2", "4"] else "sale"
            else:
                detail_url_check = str(item.get("dynamic_slug") or item.get("detailUrl") or "")
                title_check = str(item.get("dynamic_title") or item.get("title") or "")
                deal_type = "rent" if "qiravdeba" in (detail_url_check + " " + title_check).lower() else "sale"

            if filters:
                if filters.deal_type == "sale" and deal_type != "sale":
                    return None
                if filters.deal_type == "rent" and deal_type != "rent":
                    return None

            # Price extraction (currency 2 is USD, currency 1 is GEL)
            price_usd = 0.0
            price_gel = None
            price_obj = item.get("price") or {}

            if isinstance(price_obj, dict):
                # TNET JSON API structure
                usd_info = price_obj.get("2") or price_obj.get(2)
                if isinstance(usd_info, dict) and "price_total" in usd_info:
                    try:
                        price_usd = float(usd_info["price_total"] or 0)
                    except (ValueError, TypeError):
                        pass
                elif "priceUsd" in price_obj:
                    try:
                        price_usd = float(price_obj["priceUsd"] or 0)
                    except (ValueError, TypeError):
                        pass

                gel_info = price_obj.get("1") or price_obj.get(1)
                if isinstance(gel_info, dict) and "price_total" in gel_info:
                    try:
                        price_gel = float(gel_info["price_total"] or 0)
                    except (ValueError, TypeError):
                        pass
                elif "priceGeo" in price_obj:
                    try:
                        price_gel = float(price_obj["priceGeo"] or 0)
                    except (ValueError, TypeError):
                        pass

            if price_usd <= 0 and "priceUsd" in item:
                try:
                    price_usd = float(item["priceUsd"] or 0)
                except (ValueError, TypeError):
                    pass
            if price_usd <= 0 and "price_usd" in item:
                try:
                    price_usd = float(item["price_usd"] or 0)
                except (ValueError, TypeError):
                    pass
            if price_usd <= 0 and "total_price" in item:
                try:
                    price_usd = float(item["total_price"] or 0)
                except (ValueError, TypeError):
                    pass
            if price_usd <= 0 and price_gel and price_gel > 0:
                price_usd = round(price_gel / 2.65, 2)

            # Area extraction
            try:
                area_m2 = float(item.get("area") or item.get("totalArea") or item.get("area_size") or 0)
            except (ValueError, TypeError):
                area_m2 = 0.0

            if price_usd <= 0 or area_m2 <= 0:
                return None

            # Location extraction
            addr_obj = item.get("address") if isinstance(item.get("address"), dict) else {}
            city = item.get("city_name") or addr_obj.get("cityTitle") or "თბილისი"
            specific_loc = item.get("urban_name") or addr_obj.get("subdistrictTitle")
            parent_loc = item.get("district_name") or addr_obj.get("districtTitle")
            district = specific_loc or parent_loc

            parent_dual_districts = {
                "ვაკე-საბურთალო", "გლდანი-ნაძალადევი", "დიდუბე-ჩუღურეთი",
                "ისანი-სამგორი", "ძველი თბილისი", "თბილისის შემოგარენი"
            }
            subdistrict = (
                parent_loc
                if specific_loc and parent_loc != specific_loc and parent_loc not in parent_dual_districts
                else None
            )

            if isinstance(item.get("address"), str):
                street = item.get("address")
            elif addr_obj:
                st_title = addr_obj.get("streetTitle") or ""
                st_num = addr_obj.get("streetNumber") or ""
                street = f"{st_title} {st_num}".strip() or None
            else:
                street = item.get("street_address") or None

            title = item.get("dynamic_title") or item.get("title") or item.get("shortTitle") or item.get("user_title") or "ბინა SS.ge-ზე"
            description = item.get("comment") or item.get("description") or ""

            floor = str(item.get("floor")) if item.get("floor") is not None else (str(item.get("floorNumber")) if item.get("floorNumber") is not None else None)
            total_floors = _safe_int(item.get("total_floors")) or _safe_int(item.get("totalAmountOfFloor"))
            rooms = _safe_int(item.get("room")) or _safe_int(item.get("numberOfRooms"))
            if not rooms and title:
                room_match = re.search(r"(\d+)\s*(?:-|–)?\s*ოთახ", title)
                if room_match:
                    rooms = int(room_match.group(1))
            bedrooms = _safe_int(item.get("bedroom")) or _safe_int(item.get("numberOfBedrooms"))

            # Images
            images = []
            raw_images = item.get("images") or item.get("appImages") or []
            if isinstance(raw_images, list):
                for img in raw_images:
                    if isinstance(img, dict):
                        url = img.get("large") or img.get("thumb") or img.get("fileName") or img.get("url")
                        if url:
                            images.append(url)
                    elif isinstance(img, str):
                        images.append(img)

            # URL construction
            slug = item.get("dynamic_slug") or item.get("detailUrl")
            if slug:
                slug_str = str(slug).lstrip("/")
                if slug_str.startswith("http"):
                    url = slug_str
                elif str(source_id) in slug_str:
                    url = f"https://home.ss.ge/ka/udzravi-qoneba/{slug_str}"
                else:
                    url = f"https://home.ss.ge/ka/udzravi-qoneba/{slug_str}-{source_id}"
            else:
                url = f"https://home.ss.ge/ka/udzravi-qoneba/{source_id}"

            published_at = item.get("last_updated") or item.get("created_at") or item.get("orderDate") or item.get("createDate")

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
            elif user_type_str in ["agency", "developer", "agent", "broker", "company"]:
                is_owner = False
            elif item.get("isAgency") is not None:
                is_owner = not bool(item.get("isAgency"))
            elif item.get("userType"):
                ut = str(item.get("userType")).lower()
                if "physic" in ut or "owner" in ut:
                    is_owner = True
                elif "agent" in ut or "agency" in ut or "company" in ut:
                    is_owner = False
            elif item.get("agency") or item.get("agent") or item.get("agencyId") or item.get("companyName"):
                is_owner = False

            # Phone extraction
            phone_number = _extract_phone_number(item.get("user_phone_number"), description)

            return PropertyListing(
                id=f"ss_ge_{source_id}",
                source="ss_ge",
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
                url=url,
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
            print(f"[SS.ge Normalization Error for item {item.get('id') or item.get('applicationId')}]: {e}")
            return None

    async def fetch_listings(self, filters: SearchFilters) -> List[PropertyListing]:
        all_listings: List[PropertyListing] = []
        for page in range(1, self.max_pages + 1):
            raw_items = None
            # 1. Primary Strategy: Direct JSON API (TNET api-statements.tnet.ge)
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
