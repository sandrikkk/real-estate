import json
import re
from typing import List, Optional
from curl_cffi.requests import AsyncSession
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


class SSGeScraper(BaseScraper):
    def __init__(self, timeout: int = 15, max_pages: int = 3):
        super().__init__(name="SS.ge", timeout=timeout)
        self.max_pages = max_pages

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
        Enriches an SS.ge listing with verified details from the Next.js dehydrated state:
        is_owner, agency_id, agency_name, user_entity_type, phone_number.
        """
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
                            "phone_number": phone_number
                        }
        except Exception as e:
            print(f"[SS.ge Statement Detail Error for {source_id_or_url}]: {e}")
        return None

    def _normalize_item(self, item: dict, filters: Optional[SearchFilters] = None) -> Optional[PropertyListing]:
        try:
            source_id = str(item.get("applicationId") or item.get("id") or "")
            if not source_id:
                return None

            price_obj = item.get("price") or {}
            price_usd = float(price_obj.get("priceUsd") or item.get("priceUsd") or 0)
            price_gel = float(price_obj.get("priceGeo") or item.get("priceGeo") or 0) if (price_obj.get("priceGeo") or item.get("priceGeo")) else None

            area_m2 = float(item.get("totalArea") or item.get("area") or 0)
            if price_usd <= 0 or area_m2 <= 0:
                return None

            addr_obj = item.get("address") or {}
            city = addr_obj.get("cityTitle") or "თბილისი"
            specific_loc = addr_obj.get("subdistrictTitle")
            parent_loc = addr_obj.get("districtTitle")
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
            
            street_title = addr_obj.get("streetTitle") or ""
            street_num = addr_obj.get("streetNumber") or ""
            street = f"{street_title} {street_num}".strip() or None

            title = item.get("title") or item.get("shortTitle") or "ბინა SS.ge-ზე"
            description = item.get("description") or ""

            floor = str(item.get("floorNumber")) if item.get("floorNumber") is not None else None
            total_floors = _safe_int(item.get("totalAmountOfFloor"))
            bedrooms = _safe_int(item.get("numberOfBedrooms"))

            # Extract image URLs
            images = []
            raw_images = item.get("appImages") or []
            if isinstance(raw_images, list):
                for img in raw_images:
                    if isinstance(img, dict) and "fileName" in img:
                        images.append(img["fileName"])
                    elif isinstance(img, str):
                        images.append(img)

            detail_url = item.get("detailUrl")
            deal_type = "rent" if "qiravdeba" in (str(detail_url) + " " + title).lower() else "sale"
            if filters:
                if filters.deal_type == "sale" and deal_type != "sale":
                    return None
                if filters.deal_type == "rent" and deal_type != "rent":
                    return None

            if detail_url:
                url = f"https://home.ss.ge/ka/udzravi-qoneba/{detail_url}"
            else:
                url = f"https://home.ss.ge/ka/udzravi-qoneba/l/{source_id}"

            published_at = item.get("orderDate") or item.get("createDate")

            # Owner vs Agent extraction
            is_owner = None
            if item.get("isOwner") is not None:
                is_owner = bool(item.get("isOwner"))
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
                bedrooms=bedrooms,
                url=url,
                images=images,
                is_owner=is_owner,
                published_at=str(published_at) if published_at else None,
            )
        except Exception as e:
            print(f"[SS.ge Normalization Error for item {item.get('applicationId')}]: {e}")
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
