import re
import sqlite3
from typing import Any, Dict, List, Optional

from core.database import DatabaseEngine

# Georgian district stem -> canonical district name used in `properties.district`
DISTRICT_KEYWORDS: List[tuple[str, str]] = [
    ("საბურთალო", "საბურთალო"),
    ("ვაკე", "ვაკე"),
    ("ვაკის", "ვაკე"),
    ("დიდი დიღომ", "დიდი დიღომი"),
    ("დიღმის მასივ", "დიღმის მასივი"),
    ("დიღომ", "დიღომი"),
    ("ისან", "ისანი"),
    ("სამგორ", "სამგორი"),
    ("ვარკეთილ", "ვარკეთილი"),
    ("გლდან", "გლდანი"),
    ("მუხიან", "მუხიანი"),
    ("ნაძალადევ", "ნაძალადევი"),
    ("სანზონ", "სანზონა"),
    ("ჩუღურეთ", "ჩუღურეთი"),
    ("მთაწმინდ", "მთაწმინდა"),
    ("სოლოლაკ", "სოლოლაკი"),
    ("კრწანის", "კრწანისი"),
    ("ორთაჭალ", "ორთაჭალა"),
    ("ავლაბარ", "ავლაბარი"),
    ("ავლაბრ", "ავლაბარი"),
    ("დიდუბ", "დიდუბე"),
    ("წერეთელ", "წერეთელი"),
    ("წერეთლ", "წერეთელი"),
    ("თემქ", "თემქა"),
    ("ვაშლიჯვარ", "ვაშლიჯვარი"),
    ("ნუცუბიძ", "ნუცუბიძე"),
    ("ვაზისუბან", "ვაზისუბანი"),
    ("ვაზისუბნ", "ვაზისუბანი"),
    ("ბაგებ", "ბაგები"),
    ("ლის", "ლისი"),
]

DISTRICT_ALIASES: Dict[str, List[str]] = {
    "საბურთალო": [
        "საბურთალო",
        "ვაკე-საბურთალო",
        "ნუცუბიძე",
        "ნუცუბიძის ფერდობი",
        "ვაშლიჯვარი",
        "ვეძისი",
        "დელისი",
        "ლისი",
        "ლისის მიმდებარედ",
        "თხინვალი",
    ],
    "ვაკე": ["ვაკე", "ვაკე-საბურთალო", "ბაგები", "კუს ტბა", "ვერა", "წყნეთი"],
    "დიდი დიღომი": [
        "დიდი დიღომი",
        "დიღომი",
        "დიღომი 1-9",
        "სოფ. დიღომი",
        "სოფელი დიღომი",
        "დიღმის ჭალა",
        "ქოშიგორა",
        "ვაკე-საბურთალო",
    ],
    "დიღმის მასივი": ["დიღმის მასივი", "დიღმის მას.", "დიდუბე-ჩუღურეთი"],
    "დიღომი": [
        "დიღომი",
        "დიდი დიღომი",
        "დიღომი 1-9",
        "სოფ. დიღომი",
        "სოფელი დიღომი",
        "დიღმის ჭალა",
        "ქოშიგორა",
        "ვაკე-საბურთალო",
    ],
    "დიდუბე": ["დიდუბე", "წერეთელი", "წერეთლის გამზ.", "დიდუბე-ჩუღურეთი"],
    "ჩუღურეთი": [
        "ჩუღურეთი",
        "კუკია",
        "სვანეთის უბანი",
        "ივერთუბანი",
        "მარჯანიშვილი",
        "ვორონცოვი",
        "დიდუბე-ჩუღურეთი",
    ],
    "ნაძალადევი": [
        "ნაძალადევი",
        "სანზონა",
        "სან. ზონა",
        "სან.ზონა",
        "თემქა",
        "ლოტკინი",
        "გლდანი-ნაძალადევი",
    ],
    "გლდანი": [
        "გლდანი",
        "მუხიანი",
        "გლდანულა",
        "ავჭალა",
        "ზაჰესი",
        "თბილისის ზღვა",
        "კონიაკის დასახლება",
        "კონიაკის დას.",
        "გლდანი-ნაძალადევი",
    ],
    "ისანი": ["ისანი", "ავლაბარი", "ნავთლუღი", "მეტრომშენი", "300 არაგველი", "ისანი-სამგორი"],
    "სამგორი": [
        "სამგორი",
        "ვარკეთილი",
        "ვაზისუბანი",
        "მესამე მასივი",
        "მოსკოვის გამზირი",
        "მოსკოვის გამზ.",
        "აფრიკა",
        "ლილო",
        "ორხევი",
        "ფონიჭალა",
        "ისანი-სამგორი",
    ],
    "მთაწმინდა": ["მთაწმინდა", "სოლოლაკი", "რუსთაველი", "თავისუფლება", "ვერა", "ძველი თბილისი"],
    "კრწანისი": ["კრწანისი", "ორთაჭალა", "აბანოთუბანი", "ელია", "ფონიჭალა", "ძველი თბილისი"],
    "ნუცუბიძე": ["ნუცუბიძე", "ნუცუბიძის ფერდობი", "საბურთალო", "ვაკე-საბურთალო"],
    "ნუცუბიძის ფერდობი": ["ნუცუბიძის ფერდობი", "ნუცუბიძე", "საბურთალო", "ვაკე-საბურთალო"],
    "ვაშლიჯვარი": ["ვაშლიჯვარი", "საბურთალო", "ვაკე-საბურთალო"],
    "ლისი": ["ლისი", "ლისის მიმდებარედ", "საბურთალო", "ვაკე-საბურთალო"],
    "ბაგები": ["ბაგები", "ვაკე", "ვაკე-საბურთალო"],
    "ვარკეთილი": ["ვარკეთილი", "სამგორი", "ისანი-სამგორი"],
    "ვაზისუბანი": ["ვაზისუბანი", "სამგორი", "ისანი-სამგორი"],
    "მუხიანი": ["მუხიანი", "გლდანი", "გლდანი-ნაძალადევი"],
    "თემქა": ["თემქა", "ნაძალადევი", "გლდანი-ნაძალადევი"],
    "სანზონა": ["სანზონა", "სან. ზონა", "სან.ზონა", "ნაძალადევი", "გლდანი-ნაძალადევი"],
    "სოლოლაკი": ["სოლოლაკი", "მთაწმინდა", "ძველი თბილისი"],
    "ორთაჭალა": ["ორთაჭალა", "კრწანისი", "ძველი თბილისი"],
    "ავლაბარი": ["ავლაბარი", "ისანი", "ძველი თბილისი", "ისანი-სამგორი"],
    "წერეთელი": ["წერეთელი", "წერეთლის გამზ.", "დიდუბე", "დიდუბე-ჩუღურეთი"],
}


def _expand_district_aliases(district: Optional[str]) -> List[str]:
    if not district:
        return []
    d = district.strip()
    if not d:
        return []
    aliases = DISTRICT_ALIASES.get(d)
    if aliases:
        return list(dict.fromkeys([d, *aliases]))
    return [d]


def _format_owner_badge(is_owner: Any) -> str:
    if is_owner is True or (
        isinstance(is_owner, int) and not isinstance(is_owner, bool) and is_owner == 1
    ):
        return "მესაკუთრე ✅"
    if is_owner is False or (
        isinstance(is_owner, int) and not isinstance(is_owner, bool) and is_owner == 0
    ):
        return "სააგენტო 🕵️‍♂️"
    return "გადაუმოწმებელი ⚠️"


def _format_georgian_phone(phone_9: str) -> str:
    return f"+995 {phone_9[0:3]} {phone_9[3:5]} {phone_9[5:7]} {phone_9[7:9]}"


def _extract_9digit_phone(text: str) -> Optional[str]:
    if not text:
        return None
    # Remove URLs first so 9-digit numbers inside URLs aren't mistaken for phones
    cleaned = re.sub(r"https?://\S+", " ", text)
    pattern = re.compile(
        r"(?<!\d)(?:\+?995[\s\-\.]*)?(5\d{2}(?:[\s\-\.]*\d{2}[\s\-\.]*\d{2}[\s\-\.]*\d{2}|[\s\-\.]*\d{3}[\s\-\.]*\d{3}|[\s\-\.]*\d{6}))(?!\d)"
    )
    match = pattern.search(cleaned)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    if len(digits) == 9 and digits.startswith("5"):
        return digits
    return None


def _extract_masked_phone(text: str) -> Optional[str]:
    if not text:
        return None
    cleaned = re.sub(r"https?://\S+", " ", text)
    match = re.search(
        r"(?:\+?995[\s\-\.]*)?(5\d{2}[\s\-\.]*\d{2}[\s\-\.]*\d)\*{2,4}",
        cleaned,
    )
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    if len(digits) == 6 and digits.startswith("5"):
        return f"{digits}***"
    return None


def _extract_fb_attributes(text: str) -> Dict[str, Any]:
    area_m2: Optional[float] = None
    floor: Optional[str] = None
    rooms: Optional[int] = None
    price_usd: Optional[float] = None
    district: Optional[str] = None

    # Area: e.g. "60 კვ.მ", "60.5 კვ", "80 კვ²", "64 კვ/მ", "42 კვდრატი", "55 кв.м", "60 m2", "60 მ²"
    area_match = re.search(
        r"\b(\d{2,3}(?:[\.,]\d+)?)\s*(?:კვ(?:[./]?\s*მ)?(?:²|2)?(?![\u10A0-\u10FFa-zA-Z\u0400-\u04FF])|კვა?დრატ\w*|кв\.?\s*м|მ²|მ2|m2|m²|sqm)",
        text,
        re.IGNORECASE,
    )
    if area_match:
        area_m2 = float(area_match.group(1).replace(",", "."))

    # Floor: e.g. "მე-7 სართული", "7 სართული", "სართული 7", "7/12 სართული", "2/5", "პირველი სართული", "მეორე სართულზე"
    floor_word_map = {"პირველ": "1", "მეორე": "2"}
    floor_match = re.search(
        r"(?:(?:მე[\- \t]*)?([1-9]\d?)(?:[\- \t]*(?:ე|ლი))?(?:[ \t]*/[ \t]*\d{1,2})?|(?<![\u10A0-\u10FF])(პირველ|მეორე)ი?)[ \t]+სართულ(?:ი|ზე)?(?![\u10A0-\u10FF])|"
        r"(?:მე[\- \t]*)?([1-9]\d?)[ \t]*/[ \t]*\d{1,2}[ \t]*სართულ\w*|"
        r"სართულ\w*[ \t]*[:\-–—]?[ \t]*([1-9]\d?)(?:[ \t]*/[ \t]*\d{1,2})?(?![ \t]*(?:კვ|მ²|მ2|m2|m²|sqm)|\d)",
        text,
        re.IGNORECASE,
    )
    if floor_match:
        if floor_match.group(2):
            floor = floor_word_map[floor_match.group(2).lower()]
        else:
            floor = floor_match.group(1) or floor_match.group(3) or floor_match.group(4)
    else:
        for sm in re.finditer(
            r"(?<![\d/])(?<!\d[.,])(\d{1,2})[ \t]*/[ \t]*(\d{1,2})(?![\d/]|[.,]\d)", text
        ):
            f_val, total_val = int(sm.group(1)), int(sm.group(2))
            before = text[max(0, sm.start() - 3) : sm.start()].lower()
            after = text[sm.end() : sm.end() + 8].lower()
            if (f_val, total_val) == (24, 7):
                continue
            if re.search(r"[მm]\s*$", before):
                continue
            if re.match(r"^\s*წუთ", after):
                continue
            if 1 <= f_val <= total_val <= 40 and total_val >= 2:
                floor = str(f_val)
                break

    # Rooms: e.g. "3 ოთახი", "3-ოთახიანი", "3ოთახიანი", "ოროთახიანი" (excluding decimals like "1.5 ოთახიანი")
    rooms_match = re.search(r"(?<!\d)(?<!\d[.,])(\d)[ \t\-]*ოთახ\w*", text, re.IGNORECASE)
    if rooms_match:
        rooms = int(rooms_match.group(1))
    else:
        rooms_word_match = re.search(
            r"(?<![\u10A0-\u10FF])(ერთ|ორ|სამ|ოთხ|ხუთ)ი?[ \t\-]*ოთახ\w*",
            text,
            re.IGNORECASE,
        )
        if rooms_word_match:
            rooms_word_map = {"ერთ": 1, "ორ": 2, "სამ": 3, "ოთხ": 4, "ხუთ": 5}
            rooms = rooms_word_map[rooms_word_match.group(1).lower()]

    # Price USD: e.g. "85000$", "$85,000", "85 000 $", "ფასი 85000"
    price_match = re.search(
        r"(?:\$\s*(\d{2,3}(?:[,\s]\d{3})+|\d{4,6})|(\d{2,3}(?:[,\s]\d{3})+|\d{4,6})\s*(?:\$|დოლარ\w*|usd))",
        text,
        re.IGNORECASE,
    )
    if not price_match:
        price_match = re.search(
            r"ფასი\s*[:\-]?\s*(\d{2,3}(?:[,\s]\d{3})+|\d{4,6})\b",
            text,
            re.IGNORECASE,
        )
    if price_match:
        raw_p = next(g for g in price_match.groups() if g is not None)
        val = float(re.sub(r"[,\s]", "", raw_p))
        if 10000 <= val <= 2000000:
            price_usd = val

    # District matching
    lower_text = text.lower()
    for stem, canonical in DISTRICT_KEYWORDS:
        if re.search(rf"(?<![\u10A0-\u10FF]){re.escape(stem)}", lower_text):
            district = canonical
            break

    return {
        "area_m2": area_m2,
        "floor": floor,
        "rooms": rooms,
        "price_usd": price_usd,
        "district": district,
    }


def parse_xray_input(text: str) -> Optional[Dict[str, Any]]:
    if not text or not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped or stripped.startswith("/"):
        return None
    # Ignore pure 2-number filter wizard responses like "45000 70000" or "40 90"
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[\-\s]\s*\d+(?:\.\d+)?", stripped) and not re.fullmatch(
        r"5\d{2}[\s\-]+\d{6}", stripped
    ):
        return None

    # 1. Check MyHome.ge URL
    mh_match = re.search(
        r"https?://(?:www\.)?myhome\.ge/(?:[a-z]{2}/)?pr/(\d+)(?:/[^\s]*)?",
        stripped,
        re.IGNORECASE,
    )
    if mh_match:
        source_id = mh_match.group(1)
        phone_9 = _extract_9digit_phone(stripped)
        return {
            "kind": "url",
            "source": "myhome",
            "source_id": source_id,
            "listing_id": f"myhome_{source_id}",
            "url": mh_match.group(0),
            "phone_9": phone_9,
        }

    # 2. Check SS.ge URL
    ss_match = re.search(
        r"https?://(?:home\.|www\.)?ss\.ge/[^\s]*?-(\d{5,12})\b",
        stripped,
        re.IGNORECASE,
    )
    if ss_match:
        source_id = ss_match.group(1)
        phone_9 = _extract_9digit_phone(stripped)
        return {
            "kind": "url",
            "source": "ss_ge",
            "source_id": source_id,
            "listing_id": f"ss_ge_{source_id}",
            "url": ss_match.group(0),
            "phone_9": phone_9,
        }

    # 3. Extract FB text attributes and phone
    fb_attrs = _extract_fb_attributes(stripped)
    phone_9 = _extract_9digit_phone(stripped)
    masked_phone = f"{phone_9[:6]}***" if phone_9 else _extract_masked_phone(stripped)
    has_fb_signal = bool(
        fb_attrs["area_m2"] is not None
        and (
            fb_attrs["district"] is not None
            or fb_attrs["price_usd"] is not None
            or fb_attrs["floor"] is not None
            or fb_attrs["rooms"] is not None
        )
    )

    if has_fb_signal:
        return {
            "kind": "fb_text",
            "phone_9": phone_9,
            "formatted_phone": _format_georgian_phone(phone_9) if phone_9 else masked_phone,
            "masked_phone": masked_phone,
            **fb_attrs,
        }

    if phone_9 or masked_phone:
        return {
            "kind": "phone",
            "phone_9": phone_9,
            "formatted_phone": _format_georgian_phone(phone_9) if phone_9 else masked_phone,
            "masked_phone": masked_phone,
        }

    return None


def evaluate_broker_verdict(
    target: Optional[Dict[str, Any]], related_rows: List[Dict[str, Any]]
) -> Dict[str, Any]:
    all_rows: List[Dict[str, Any]] = []
    seen_ids = set()
    is_phoneless_fb = bool(
        target
        and target.get("id") == "fb_input"
        and not target.get("phone_number")
        and not target.get("author_id")
    )
    if target and target.get("id") and not is_phoneless_fb:
        all_rows.append(target)
        seen_ids.add(target["id"])
    for r in related_rows:
        rid = r.get("id")
        if rid and rid not in seen_ids:
            all_rows.append(r)
            seen_ids.add(rid)

    if is_phoneless_fb and not all_rows:
        return {
            "is_broker": False,
            "badge": "⚠️ სტატუსი დაუზუსტებელია (ნომერი არ არის მითითებული)",
            "reason": "განცხადებაში ტელეფონის ნომერი არ წერია და ბაზაში იდენტური ბინა ჯერ არ იძებნება",
            "unique_apartments": 0,
            "total_listings": 0,
        }

    # Count distinct apartments by (district, round(area_m2 / 2))
    max_author_count = 0
    any_non_owner = False
    any_agent_type = False
    any_verified_owner = False
    agent_tokens = (
        "agent",
        "agency",
        "broker",
        "developer",
        "company",
        "აგენტ",
        "მაკლერ",
        "სააგენტო",
    )

    def _inspect_row(r: Dict[str, Any]) -> None:
        nonlocal any_non_owner, any_agent_type, any_verified_owner, max_author_count
        is_own = r.get("is_owner")
        if is_own is False or (
            isinstance(is_own, int) and not isinstance(is_own, bool) and is_own == 0
        ):
            any_non_owner = True
        elif is_own is True or (
            isinstance(is_own, int) and not isinstance(is_own, bool) and is_own == 1
        ):
            any_verified_owner = True
        ut = str(r.get("user_type") or "").lower()
        if any(tok in ut for tok in agent_tokens):
            any_agent_type = True
        cnt = r.get("author_listings_count")
        if cnt is not None:
            try:
                max_author_count = max(max_author_count, int(cnt))
            except (ValueError, TypeError):
                pass

    if target and not is_phoneless_fb:
        _inspect_row(target)

    clusters: List[Dict[str, Any]] = []
    for r in all_rows:
        _inspect_row(r)
        r_gid = r.get("group_id")
        r_dist = (r.get("district") or "").strip()
        r_aliases = set(_expand_district_aliases(r_dist)) if r_dist else set()
        r_floor = _clean_floor(r.get("floor"))
        try:
            r_area = float(r.get("area_m2") or 0.0)
        except (ValueError, TypeError):
            r_area = 0.0
        try:
            r_rooms = int(r.get("rooms") or 0)
        except (ValueError, TypeError):
            r_rooms = 0

        matched_cluster = False
        for c in clusters:
            if r_gid and c["group_id"] and r_gid == c["group_id"]:
                matched_cluster = True
                break
            dist_compat = (
                not r_dist
                or not c["district"]
                or r_dist in c["aliases"]
                or c["district"] in r_aliases
            )
            area_compat = r_area > 0 and c["area_m2"] > 0 and abs(r_area - c["area_m2"]) <= 2.0
            floor_compat = not r_floor or not c["floor"] or r_floor == c["floor"]
            rooms_compat = r_rooms <= 0 or c["rooms"] <= 0 or r_rooms == c["rooms"]
            if dist_compat and area_compat and floor_compat and rooms_compat:
                matched_cluster = True
                break

        if not matched_cluster:
            clusters.append(
                {
                    "group_id": r_gid,
                    "district": r_dist,
                    "aliases": r_aliases,
                    "area_m2": r_area,
                    "floor": r_floor,
                    "rooms": r_rooms,
                }
            )

    unique_apartments = len(clusters)
    total_listings = max(len(all_rows), max_author_count, unique_apartments)

    is_broker = any_non_owner or any_agent_type or max_author_count >= 3 or unique_apartments >= 2

    if is_broker:
        reasons = []
        if unique_apartments >= 2 or len(all_rows) >= 2:
            reasons.append(
                f"ბაზაში ამ ნომრით/ავტორით იძებნება <b>{len(all_rows)} განცხადება</b> ({unique_apartments} სხვადასხვა ბინა)"
            )
        elif max_author_count >= 3:
            reasons.append(f"პორტალზე ავტორს უფიქსირდება <b>{max_author_count} განცხადება</b>")
        if any_agent_type or any_non_owner:
            reasons.append("პროფილი მონიშნულია როგორც სააგენტო/მაკლერი")
        return {
            "is_broker": True,
            "badge": "🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო",
            "reason": " • ".join(reasons) if reasons else "დაფიქსირდა სააგენტოს ნიშნები",
            "unique_apartments": unique_apartments,
            "total_listings": total_listings,
        }

    if not any_verified_owner:
        return {
            "is_broker": False,
            "badge": "⚠️ სტატუსი დაუზუსტებელია (გადაუმოწმებელი)",
            "reason": "ბაზაში ფიქსირდება 1 განცხადება, თუმცა პორტალზე მესაკუთრის სტატუსი დადასტურებული არ არის",
            "unique_apartments": max(1, unique_apartments),
            "total_listings": max(1, total_listings),
        }

    return {
        "is_broker": False,
        "badge": "✅ რეალური მესაკუთრე (Owner)",
        "reason": "ბაზაში და პორტალზე ფიქსირდება მხოლოდ 1 ბინა (სააგენტოს ნიშნების გარეშე)",
        "unique_apartments": max(1, unique_apartments),
        "total_listings": max(1, total_listings),
    }


def _clean_floor(floor_val: Any) -> str:
    return str(floor_val or "").strip().split("/")[0].strip()


def _floors_match(target_floor: Any, cand_floor: Any) -> bool:
    tf = _clean_floor(target_floor)
    cf = _clean_floor(cand_floor)
    if not tf or not cf:
        return False
    return tf == cf


def _rooms_match(target_rooms: Any, cand_rooms: Any) -> bool:
    try:
        tr = int(target_rooms) if target_rooms is not None else 0
        cr = int(cand_rooms) if cand_rooms is not None else 0
    except (ValueError, TypeError):
        return True
    if tr <= 0 or cr <= 0:
        return True
    return tr == cr


def _candidate_matches_target(target: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    if not _rooms_match(target.get("rooms"), cand.get("rooms")):
        return False
    tf = _clean_floor(target.get("floor"))
    cf = _clean_floor(cand.get("floor"))
    if tf:
        return cf == tf
    # When floor is omitted on target (e.g. in an FB post), require explicit matching rooms
    # AND price within ±10% so unrelated apartments on other floors do not match.
    try:
        tr = int(target.get("rooms") or 0)
        cr = int(cand.get("rooms") or 0)
        tp = float(target.get("price_usd") or 0.0)
        cp = float(cand.get("price_usd") or 0.0)
    except (ValueError, TypeError):
        return False
    if tr > 0 and cr > 0 and tr == cr and tp > 0 and cp > 0:
        return abs(cp - tp) / tp <= 0.10
    return False


def find_exact_duplicates(conn: sqlite3.Connection, target: Dict[str, Any]) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    target_id = target.get("id") or ""
    group_id = target.get("group_id")
    district = target.get("district")
    area_m2 = target.get("area_m2")

    candidates_by_id: Dict[str, Dict[str, Any]] = {}

    if group_id:
        cur.execute(
            "SELECT * FROM properties WHERE group_id = ? AND id != ?",
            (group_id, target_id),
        )
        for row in cur.fetchall():
            d = dict(row)
            candidates_by_id[d["id"]] = d

    if district and area_m2 and float(area_m2) > 0:
        aliases = _expand_district_aliases(district)
        placeholders = ", ".join("?" for _ in aliases)
        low_area = float(area_m2) - 1.5
        high_area = float(area_m2) + 1.5
        deal_type = target.get("deal_type") or "sale"
        tf = _clean_floor(target.get("floor"))
        try:
            tr = int(target.get("rooms") or 0)
        except (ValueError, TypeError):
            tr = 0
        try:
            tp = float(target.get("price_usd") or 0.0)
        except (ValueError, TypeError):
            tp = 0.0

        sql_parts = [
            f"SELECT * FROM properties WHERE district IN ({placeholders})",
            "AND area_m2 BETWEEN ? AND ?",
            "AND id != ?",
            "AND (deal_type IS NULL OR deal_type = ?)",
        ]
        params: List[Any] = [*aliases, low_area, high_area, target_id, deal_type]

        if deal_type == "sale":
            sql_parts.append("AND price_usd >= 10000")
        if tr > 0:
            sql_parts.append("AND (rooms IS NULL OR rooms <= 0 OR rooms = ?)")
            params.append(tr)
        if tf:
            sql_parts.append("AND (floor = ? OR floor LIKE ?)")
            params.extend([tf, f"{tf}/%"])
            sql_parts.append("ORDER BY price_usd ASC LIMIT 25")
        elif tp > 0:
            sql_parts.append("ORDER BY ABS(price_usd - ?) ASC LIMIT 25")
            params.append(tp)
        else:
            sql_parts.append("ORDER BY price_usd ASC LIMIT 25")

        cur.execute(" ".join(sql_parts), tuple(params))
        for row in cur.fetchall():
            d = dict(row)
            if not _candidate_matches_target(target, d):
                continue
            candidates_by_id[d["id"]] = d

    results = list(candidates_by_id.values())
    results.sort(key=lambda r: float(r.get("price_usd") or 0.0))
    return results


def get_district_avg_m2(
    conn: sqlite3.Connection, district: Optional[str]
) -> Optional[Dict[str, Any]]:
    if not district:
        return None
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ROUND(AVG(price_per_m2)), COUNT(*)
        FROM properties
        WHERE district = ? AND price_per_m2 > 300 AND price_per_m2 < 6000
        """,
        (district,),
    )
    row = cur.fetchone()
    if row and row[0] and int(row[1] or 0) >= 1:
        return {"avg_m2": int(row[0]), "count": int(row[1])}

    aliases = _expand_district_aliases(district)
    if len(aliases) > 1:
        placeholders = ", ".join("?" for _ in aliases)
        cur.execute(
            f"""
            SELECT ROUND(AVG(price_per_m2)), COUNT(*)
            FROM properties
            WHERE district IN ({placeholders}) AND price_per_m2 > 300 AND price_per_m2 < 6000
            """,
            tuple(aliases),
        )
        row = cur.fetchone()
        if row and row[0] and int(row[1] or 0) >= 1:
            return {"avg_m2": int(row[0]), "count": int(row[1])}
    return None


def _query_by_phone_or_author(
    conn: sqlite3.Connection,
    phone_9: Optional[str] = None,
    formatted_phone: Optional[str] = None,
    author_id: Optional[str] = None,
    masked_phone: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    seen: Dict[str, Dict[str, Any]] = {}

    if phone_9 or masked_phone:
        fmt = formatted_phone or (_format_georgian_phone(phone_9) if phone_9 else masked_phone)
        mp = masked_phone or (f"{phone_9[:6]}***" if phone_9 and len(phone_9) >= 6 else fmt)
        p9 = phone_9 or fmt
        cur.execute(
            "SELECT * FROM properties WHERE phone_number IN (?, ?, ?) ORDER BY scraped_at DESC LIMIT 30",
            (fmt, p9, mp),
        )
        fetched = [dict(row) for row in cur.fetchall()]
        if phone_9:
            exact_rows = [d for d in fetched if d.get("phone_number") in (fmt, phone_9)]
            chosen = exact_rows if exact_rows else fetched
        else:
            chosen = fetched
        for d in chosen:
            seen[d["id"]] = d

    if author_id:
        cur.execute(
            "SELECT * FROM properties WHERE author_id = ? ORDER BY scraped_at DESC LIMIT 30",
            (str(author_id),),
        )
        for row in cur.fetchall():
            d = dict(row)
            seen[d["id"]] = d

    return list(seen.values())


def _merge_live_into_row(row: Dict[str, Any], live: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not live:
        return row
    merged = dict(row)
    live_own = live.get("is_owner")
    if live_own is False or (
        isinstance(live_own, int) and not isinstance(live_own, bool) and live_own == 0
    ):
        merged["is_owner"] = 0
    elif merged.get("is_owner") is None and live_own is not None:
        merged["is_owner"] = 1 if live_own else 0
    if live.get("user_type") and (merged.get("is_owner") == 0 or not merged.get("user_type")):
        merged["user_type"] = live["user_type"]
    if not merged.get("phone_number") and live.get("phone_number"):
        merged["phone_number"] = live["phone_number"]
    if not merged.get("author_id") and live.get("author_id"):
        merged["author_id"] = str(live["author_id"])
    if live.get("author_listings_count") is not None:
        try:
            prev_cnt = int(merged.get("author_listings_count") or 0)
            live_cnt = int(live["author_listings_count"])
            merged["author_listings_count"] = max(prev_cnt, live_cnt)
        except (ValueError, TypeError):
            pass
    return merged


def run_xray_on_db(
    db: DatabaseEngine, text: str, live_details: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    parsed = parse_xray_input(text)
    if not parsed:
        return None

    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        kind = parsed["kind"]

        if kind == "phone":
            phone_9 = parsed.get("phone_9")
            fmt_phone = parsed["formatted_phone"]
            masked_phone = parsed.get("masked_phone")
            related = _query_by_phone_or_author(
                conn,
                phone_9=phone_9,
                formatted_phone=fmt_phone,
                masked_phone=masked_phone,
            )
            if not related:
                return (
                    f"🔎 <b>ტელეფონის რენტგენი:</b> <code>{fmt_phone}</code>\n\n"
                    "ℹ️ ჩვენს ბაზაში ამ ნომერზე განცხადებები ჯერ არ ფიქსირდება."
                )
            if live_details:
                related[0] = _merge_live_into_row(related[0], live_details)
            discovered_author = related[0].get("author_id")
            if discovered_author:
                by_author = _query_by_phone_or_author(
                    conn,
                    phone_9=phone_9,
                    formatted_phone=fmt_phone,
                    author_id=discovered_author,
                    masked_phone=masked_phone,
                )
                seen_r = {r["id"]: r for r in related}
                for ar in by_author:
                    if ar["id"] not in seen_r:
                        related.append(ar)
                        seen_r[ar["id"]] = ar
            verdict = evaluate_broker_verdict(related[0], related)
            lines = [
                f"🔎 <b>ტელეფონის რენტგენი:</b> <code>{fmt_phone}</code>",
                f"👤 <b>სტატუსი:</b> {verdict['badge']}",
                f"📌 {verdict['reason']}",
                "",
                f"📋 <b>ნაპოვნი განცხადებები ({len(related)}):</b>",
            ]
            for r in related[:5]:
                p_usd = int(float(r.get("price_usd") or 0))
                area = r.get("area_m2") or 0
                dist = r.get("district") or "უცნობი უბანი"
                src = "MyHome" if r.get("source") == "myhome" else "SS.ge"
                url = r.get("url") or "#"
                lines.append(
                    f'• <a href="{url}">{src}</a> — <b>${p_usd:,}</b> | {area:g} მ² | {dist}'
                )

            dup_by_id: Dict[str, tuple[Dict[str, Any], float]] = {}
            related_ids = {r["id"] for r in related if r.get("id")}
            for r in related[:5]:
                r_price = float(r.get("price_usd") or 0.0)
                for dup in find_exact_duplicates(conn, r):
                    did = dup.get("id")
                    if did and did not in related_ids and did not in dup_by_id:
                        dup_by_id[did] = (dup, r_price)
            if dup_by_id:
                lines.append("")
                lines.append("🎯 <b>იგივე ბინა სხვაგან:</b>")
                sorted_dups = sorted(
                    dup_by_id.values(),
                    key=lambda item: float(item[0].get("price_usd") or 0.0),
                )
                for dup, ref_price in sorted_dups[:4]:
                    dup_price = float(dup.get("price_usd") or 0.0)
                    dup_src = "MyHome.ge" if dup.get("source") == "myhome" else "SS.ge"
                    dup_owner = _format_owner_badge(dup.get("is_owner"))
                    dup_phone = dup.get("phone_number")
                    phone_part = f" | 📞 <code>{dup_phone}</code>" if dup_phone else ""
                    dup_url = dup.get("url") or "#"
                    diff_str = ""
                    if ref_price > 0 and dup_price > 0:
                        delta = round(dup_price - ref_price)
                        if delta < 0:
                            diff_str = f" (<b>-${abs(delta):,} უფრო იაფად!</b>)"
                        elif delta > 0:
                            diff_str = f" (+${delta:,} უფრო ძვირად)"
                        else:
                            diff_str = " (იგივე ფასი)"
                    lines.append(
                        f'• <a href="{dup_url}">{dup_src}</a> — <b>${int(dup_price):,}</b>{diff_str} | {dup_owner}{phone_part}'
                    )

            return "\n".join(lines)

        target: Optional[Dict[str, Any]] = None
        if kind == "url":
            cur.execute("SELECT * FROM properties WHERE id = ?", (parsed["listing_id"],))
            row = cur.fetchone()
            if row:
                target = _merge_live_into_row(dict(row), live_details)
            elif live_details:
                target = dict(live_details)
                target.setdefault("id", parsed["listing_id"])
                target.setdefault("source", parsed["source"])
                target.setdefault("url", parsed["url"])
        elif kind == "fb_text":
            target = {
                "id": "fb_input",
                "source": "facebook",
                "price_usd": parsed.get("price_usd") or 0.0,
                "area_m2": parsed.get("area_m2") or 0.0,
                "district": parsed.get("district") or "",
                "floor": parsed.get("floor") or "",
                "rooms": parsed.get("rooms") or 0,
                "phone_number": parsed.get("formatted_phone"),
                "is_owner": None,
                "user_type": None,
            }

        if not target:
            return (
                "🔎 <b>განცხადების რენტგენი</b>\n\n"
                "ℹ️ ეს განცხადება ჩვენს ბაზაში ჯერ არ იძებნება და პორტალიდან მონაცემების წამოღება ვერ მოხერხდა."
            )

        duplicates = find_exact_duplicates(conn, target)
        if kind == "fb_text" and live_details and duplicates:
            duplicates[0] = _merge_live_into_row(duplicates[0], live_details)

        phone_9 = parsed.get("phone_9") or _extract_9digit_phone(
            str(target.get("phone_number") or "")
        )
        masked_phone = (
            parsed.get("masked_phone")
            or (f"{phone_9[:6]}***" if phone_9 else None)
            or _extract_masked_phone(str(target.get("phone_number") or ""))
        )
        author_id = target.get("author_id")

        verdict_target = target
        if kind == "fb_text" and not phone_9 and not masked_phone and duplicates:
            verdict_target = duplicates[0]
            phone_9 = _extract_9digit_phone(str(verdict_target.get("phone_number") or ""))
            masked_phone = (f"{phone_9[:6]}***" if phone_9 else None) or _extract_masked_phone(
                str(verdict_target.get("phone_number") or "")
            )
            author_id = verdict_target.get("author_id")

        related = _query_by_phone_or_author(
            conn,
            phone_9=phone_9,
            formatted_phone=_format_georgian_phone(phone_9) if phone_9 else masked_phone,
            author_id=author_id,
            masked_phone=masked_phone,
        )
        verdict = evaluate_broker_verdict(verdict_target, related)
        bench = get_district_avg_m2(conn, target.get("district"))

        curr_price = float(target.get("price_usd") or 0.0)
        init_price = float(target.get("initial_price_usd") or curr_price)
        area_m2 = float(target.get("area_m2") or 0.0)
        price_m2 = round(curr_price / area_m2) if (curr_price > 0 and area_m2 > 0) else 0

        header_title = (
            "🔎 <b>Facebook განცხადების რენტგენი</b>"
            if kind == "fb_text"
            else "🔎 <b>განცხადების რენტგენი (X-Ray)</b>"
        )
        lines = [
            header_title,
            f"👤 <b>გამყიდველი:</b> {verdict['badge']}",
            f"📌 {verdict['reason']}",
        ]
        display_phone = target.get("phone_number") or next(
            (d.get("phone_number") for d in duplicates if d.get("phone_number")), None
        )
        if display_phone:
            lines.append(f"📞 <b>ტელეფონი:</b> <code>{display_phone}</code>")

        # Price history section
        if curr_price > 0:
            lines.append("")
            lines.append(
                f"💰 <b>მიმდინარე ფასი:</b> ${int(curr_price):,}"
                + (f" (${price_m2:,}/მ²)" if price_m2 else "")
            )
            if init_price > 0:
                lines.append(f"📉 პირვანდელი ფასი: <b>${int(init_price):,}</b>")
                diff = round(curr_price - init_price)
                if diff < 0:
                    pct = round(abs(diff) / init_price * 100, 1)
                    lines.append(f"✅ დაკლებულია: <b>-${abs(diff):,} (-{pct}%)</b>")
                elif diff > 0:
                    pct = round(diff / init_price * 100, 1)
                    lines.append(f"📈 მომატებულია: <b>+${diff:,} (+{pct}%)</b>")
                else:
                    lines.append("➖ ფასი არ შეცვლილა (პირვანდელი ფასია)")

        # District market benchmark
        if bench and price_m2 > 0:
            avg_m2 = bench["avg_m2"]
            diff_pct = round((price_m2 - avg_m2) / avg_m2 * 100)
            sign = f"+{diff_pct}%" if diff_pct > 0 else f"{diff_pct}%"
            lines.append(
                f"📊 <b>უბნის საშუალო ფასი ({target.get('district')}):</b> ${avg_m2:,}/მ² ({sign} საბაზროსთან)"
            )

        # Duplicates / Cheaper alternatives section
        lines.append("")
        if duplicates:
            lines.append("🎯 <b>იგივე ბინა სხვაგან:</b>")
            for dup in duplicates[:4]:
                dup_price = float(dup.get("price_usd") or 0.0)
                dup_src = "MyHome.ge" if dup.get("source") == "myhome" else "SS.ge"
                dup_owner = _format_owner_badge(dup.get("is_owner"))
                dup_phone = dup.get("phone_number")
                phone_part = f" | 📞 <code>{dup_phone}</code>" if dup_phone else ""
                dup_url = dup.get("url") or "#"
                diff_str = ""
                if curr_price > 0 and dup_price > 0:
                    delta = round(dup_price - curr_price)
                    if delta < 0:
                        diff_str = f" (<b>-${abs(delta):,} უფრო იაფად!</b>)"
                    elif delta > 0:
                        diff_str = f" (+${delta:,} უფრო ძვირად)"
                    else:
                        diff_str = " (იგივე ფასი)"
                lines.append(
                    f'• <a href="{dup_url}">{dup_src}</a> — <b>${int(dup_price):,}</b>{diff_str} | {dup_owner}{phone_part}'
                )
        else:
            lines.append("🎯 <b>იგივე ბინა სხვაგან:</b> დუბლიკატი სხვა პორტალზე არ იძებნება.")

        return "\n".join(lines)
    finally:
        conn.close()
