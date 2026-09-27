import json
import re
from typing import List, Optional
from curl_cffi.requests import AsyncSession
from core.models import PropertyListing, SearchFilters
from scrapers.base import BaseScraper
from scrapers.myhome import (
    METRO_STATIONS,
    CONDITIONS,
    _safe_int,
    _extract_phone_number,
)


class SSGeScraper(BaseScraper):
    def __init__(self, timeout: int = 15, max_pages: int = 3):
        super().__init__(name="SS.ge", timeout=timeout)
        self.max_pages = max_pages

    def _build_search_url(self, filters: SearchFilters, page: int = 1) -> str:
        deal_path = "iyideba" if filters.deal_type == "sale" else "qiravdeba"
        url = f"https://home.ss.ge/ka/udzravi-qoneba/l/bina/{deal_path}?cityIdList=95&currencyId=2&order=1&page={page}"

        params = []
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
            params.append(f"priceFrom={int(min_p)}")
        if max_p is not None:
            params.append(f"priceTo={int(max_p)}")
        if filters.area_min_m2 is not None:
            params.append(f"areaFrom={int(filters.area_min_m2)}")
        if filters.area_max_m2 is not None:
            params.append(f"areaTo={int(filters.area_max_m2)}")
        if filters.rooms_min is not None and filters.rooms_min > 0:
            params.append(f"rooms={int(filters.rooms_min)}")
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
        Enriches an SS.ge listing with verified details from Next.js dehydrated state:
        is_owner, agency_id, agency_name, user_entity_type, phone_number, condition_id, condition_name, price_label.
        """
        clean_source = str(source_id_or_url).split("?")[0].split("#")[0].rstrip("/")
        if clean_source.startswith("http"):
            url = clean_source
        else:
            id_match = re.search(r"(\d{5,})", clean_source)
            stmt_id = (
                id_match.group(1)
                if id_match
                else (re.findall(r"\d+", clean_source) or [str(source_id_or_url)])[-1]
            )
            url = f"https://home.ss.ge/ka/udzravi-qoneba/{stmt_id}"
        try:
            async with AsyncSession(impersonate="chrome124") as session:
                response = await session.get(url, timeout=self.timeout)
                if response.status_code == 200:
                    match = re.search(
                        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', response.text, re.DOTALL
                    )
                    if match:
                        data = json.loads(match.group(1))
                        props = data.get("props", {}).get("pageProps", {})
                        app_data = props.get("applicationData") or {}
                        agent_info = props.get("initialAgentInfoData") or {}

                        agency_id = (
                            app_data.get("agencyId")
                            or app_data.get("externalCompanyId")
                            or agent_info.get("companyId")
                        )
                        agency_name = (
                            app_data.get("agencyName")
                            or app_data.get("companyName")
                            or agent_info.get("companyName")
                        )
                        user_entity_type = str(app_data.get("userEntityType") or "").lower()
                        app_count = app_data.get("userApplicationCount") or 0

                        is_owner = None
                        if (
                            agency_id
                            or agency_name
                            or "broker" in user_entity_type
                            or "agent" in user_entity_type
                            or "agency" in user_entity_type
                            or "company" in user_entity_type
                            or (agent_info.get("companyId") is not None)
                            or (agent_info.get("infoType") == 2)
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

                        raw_desc = app_data.get("comment") or app_data.get("description") or ""
                        if isinstance(raw_desc, dict):
                            comment = raw_desc.get("ka") or raw_desc.get("text") or str(raw_desc)
                        else:
                            comment = str(raw_desc or "")
                        clean_phone = _extract_phone_number(phone_number, comment)

                        cond_name = app_data.get("state")
                        status_id = app_data.get("realEstateStatusId")
                        price_label = app_data.get("priceLevel")

                        condition_id = None
                        if cond_name:
                            for cid, cname in CONDITIONS.items():
                                if (
                                    cname.lower() in cond_name.lower()
                                    or cond_name.lower() in cname.lower()
                                ):
                                    condition_id = cid
                                    break

                        return {
                            "is_owner": is_owner,
                            "agency_id": agency_id,
                            "agency_name": agency_name,
                            "user_entity_type": user_entity_type,
                            "phone_number": clean_phone,
                            "user_phone_number": clean_phone,
                            "condition_id": condition_id,
                            "condition_name": cond_name,
                            "status_id": status_id,
                            "price_label": price_label,
                            "comment": comment,
                        }
        except Exception as e:
            print(f"[SS.ge Statement Detail Error for {source_id_or_url}]: {e}")
        return None

    def _normalize_item(
        self, item: dict, filters: Optional[SearchFilters] = None
    ) -> Optional[PropertyListing]:
        try:
            source_id = str(
                item.get("id") or item.get("statement_id") or item.get("applicationId") or ""
            )
            if not source_id:
                return None

            # Real estate type validation (1 is apartment/flat)
            ret_id = item.get("real_estate_type_id")
            if ret_id is not None and str(ret_id) != "1":
                return None

            # Deal type validation
            deal_type_id = item.get("deal_type_id")
            if deal_type_id is not None:
                deal_type = "rent" if str(deal_type_id) in ["2", "4"] else "sale"
            else:
                detail_url_check = str(item.get("dynamic_slug") or item.get("detailUrl") or "")
                title_check = str(item.get("dynamic_title") or item.get("title") or "")
                deal_type = (
                    "rent"
                    if "qiravdeba" in (detail_url_check + " " + title_check).lower()
                    else "sale"
                )

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
                area_m2 = float(
                    item.get("area") or item.get("totalArea") or item.get("area_size") or 0
                )
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
                "ვაკე-საბურთალო",
                "გლდანი-ნაძალადევი",
                "დიდუბე-ჩუღურეთი",
                "ისანი-სამგორი",
                "ძველი თბილისი",
                "თბილისის შემოგარენი",
            }
            subdistrict = (
                parent_loc
                if specific_loc
                and parent_loc != specific_loc
                and parent_loc not in parent_dual_districts
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

            title = (
                item.get("dynamic_title")
                or item.get("title")
                or item.get("shortTitle")
                or item.get("user_title")
                or "ბინა SS.ge-ზე"
            )
            raw_desc = item.get("comment") or item.get("description") or ""
            if isinstance(raw_desc, dict):
                description = raw_desc.get("ka") or raw_desc.get("text") or str(raw_desc)
            else:
                description = str(raw_desc or "")

            floor = (
                str(item.get("floor"))
                if item.get("floor") is not None
                else (str(item.get("floorNumber")) if item.get("floorNumber") is not None else None)
            )
            total_floors = _safe_int(item.get("total_floors")) or _safe_int(
                item.get("totalAmountOfFloor")
            )
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
                        img_url = (
                            img.get("large")
                            or img.get("thumb")
                            or img.get("fileName")
                            or img.get("url")
                        )
                        if img_url:
                            images.append(img_url)
                    elif isinstance(img, str):
                        images.append(img)

            # URL construction
            slug = item.get("dynamic_slug") or item.get("detailUrl")
            if slug:
                slug_str = str(slug).lstrip("/")
                if slug_str.startswith("http"):
                    url = slug_str
                else:
                    slug_clean = re.sub(
                        r"^(?:(?:ka|en|ru)/)?(?:udzravi-qoneba/)?(?:l/)?", "", slug_str
                    )
                    if str(source_id) in slug_clean:
                        url = f"https://home.ss.ge/ka/udzravi-qoneba/{slug_clean}"
                    else:
                        url = f"https://home.ss.ge/ka/udzravi-qoneba/{slug_clean}-{source_id}"
            else:
                url = f"https://home.ss.ge/ka/udzravi-qoneba/{source_id}"

            published_at = (
                item.get("last_updated")
                or item.get("created_at")
                or item.get("orderDate")
                or item.get("createDate")
            )

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
            elif (
                item.get("agency")
                or item.get("agent")
                or item.get("agencyId")
                or item.get("companyName")
            ):
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
            print(
                f"[SS.ge Normalization Error for item {item.get('id') or item.get('applicationId')}]: {e}"
            )
            return None

    async def fetch_listings(self, filters: SearchFilters) -> List[PropertyListing]:
        all_listings: List[PropertyListing] = []
        deal_types = ["sale", "rent"] if filters.deal_type == "both" else [filters.deal_type]
        for dt in deal_types:
            dt_filters = filters.model_copy()
            dt_filters.deal_type = dt
            for page in range(1, self.max_pages + 1):
                search_url = self._build_search_url(dt_filters, page=page)
                html = await self.fetch_html(search_url)
                if not html:
                    break

                raw_items = self._extract_listings_from_html(html)
                if not raw_items:
                    break

                for raw in raw_items:
                    normalized = self._normalize_item(raw, filters=dt_filters)
                    if normalized:
                        all_listings.append(normalized)

        return all_listings
