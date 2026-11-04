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
    ("ბაგებ", "ბაგები"),
    ("ლის", "ლისი"),
]


def _format_georgian_phone(phone_9: str) -> str:
    return f"+995 {phone_9[0:3]} {phone_9[3:5]} {phone_9[5:7]} {phone_9[7:9]}"


def _extract_9digit_phone(text: str) -> Optional[str]:
    if not text:
        return None
    # Remove URLs first so 9-digit numbers inside URLs aren't mistaken for phones
    cleaned = re.sub(r"https?://\S+", " ", text)
    pattern = re.compile(
        r"(?:\+?995[\s\-\.]*)?(5\d{2}[\s\-\.]*\d{2}[\s\-\.]*\d{2}[\s\-\.]*\d{2}|\b5\d{8}\b)"
    )
    match = pattern.search(cleaned)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    if len(digits) == 9 and digits.startswith("5"):
        return digits
    return None


def _extract_fb_attributes(text: str) -> Dict[str, Any]:
    area_m2: Optional[float] = None
    floor: Optional[str] = None
    rooms: Optional[int] = None
    price_usd: Optional[float] = None
    district: Optional[str] = None

    # Area: e.g. "60 კვ.მ", "60.5 კვ", "60 m2", "60 მ²", "60მ2"
    area_match = re.search(
        r"\b(\d{2,3}(?:[\.,]\d+)?)\s*(?:კვ\.?\s*მ|კვადრატ\w*|მ²|მ2|m2|m²|sqm)\b",
        text,
        re.IGNORECASE,
    )
    if area_match:
        area_m2 = float(area_match.group(1).replace(",", "."))

    # Floor: e.g. "მე-7 სართული", "7 სართული", "სართული 7", "7/12 სართული"
    floor_match = re.search(
        r"(?:მე[\-\s]*)?(\d{1,2})(?:\s*/\s*\d{1,2})?\s*სართულ\w*|სართულ\w*\s*[:\-]?\s*(\d{1,2})",
        text,
        re.IGNORECASE,
    )
    if floor_match:
        floor = floor_match.group(1) or floor_match.group(2)

    # Rooms: e.g. "3 ოთახი", "3-ოთახიანი", "3 ოთახიანი"
    rooms_match = re.search(r"\b(\d)\s*[\-\s]*ოთახ\w*", text, re.IGNORECASE)
    if rooms_match:
        rooms = int(rooms_match.group(1))

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
        if stem in lower_text:
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
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[\-\s]\s*\d+(?:\.\d+)?", stripped):
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
            "formatted_phone": _format_georgian_phone(phone_9) if phone_9 else None,
            **fb_attrs,
        }

    if phone_9:
        return {
            "kind": "phone",
            "phone_9": phone_9,
            "formatted_phone": _format_georgian_phone(phone_9),
        }

    return None


def evaluate_broker_verdict(
    target: Optional[Dict[str, Any]], related_rows: List[Dict[str, Any]]
) -> Dict[str, Any]:
    all_rows: List[Dict[str, Any]] = []
    seen_ids = set()
    if target and target.get("id"):
        all_rows.append(target)
        seen_ids.add(target["id"])
    for r in related_rows:
        rid = r.get("id")
        if rid and rid not in seen_ids:
            all_rows.append(r)
            seen_ids.add(rid)

    # Count distinct apartments by (district, round(area_m2 / 2))
    apt_keys = set()
    max_author_count = 0
    any_non_owner = False
    any_agent_type = False

    if target:
        if target.get("is_owner") in (0, False):
            any_non_owner = True
        ut = str(target.get("user_type") or "").lower()
        if ut in ("agent", "agency", "broker", "developer"):
            any_agent_type = True
        cnt = target.get("author_listings_count")
        if cnt is not None:
            try:
                max_author_count = max(max_author_count, int(cnt))
            except (ValueError, TypeError):
                pass

    for r in all_rows:
        if r.get("is_owner") in (0, False):
            any_non_owner = True
        ut = str(r.get("user_type") or "").lower()
        if ut in ("agent", "agency", "broker", "developer"):
            any_agent_type = True
        cnt = r.get("author_listings_count")
        if cnt is not None:
            try:
                max_author_count = max(max_author_count, int(cnt))
            except (ValueError, TypeError):
                pass
        dist = (r.get("district") or "").strip()
        area = r.get("area_m2") or 0.0
        try:
            area_bucket = round(float(area) / 2.0)
        except (ValueError, TypeError):
            area_bucket = 0
        if dist or area_bucket:
            apt_keys.add((dist, area_bucket))

    unique_apartments = max(len(apt_keys), len(all_rows) if not apt_keys else len(apt_keys))
    total_listings = max(len(all_rows), max_author_count, unique_apartments)

    is_broker = (
        any_non_owner
        or any_agent_type
        or max_author_count >= 3
        or unique_apartments >= 2
    )

    if is_broker:
        reasons = []
        if unique_apartments >= 2 or len(all_rows) >= 2:
            reasons.append(f"ბაზაში ამ ნომრით/ავტორით იძებნება <b>{len(all_rows)} განცხადება</b> ({unique_apartments} სხვადასხვა ბინა)")
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

    return {
        "is_broker": False,
        "badge": "✅ რეალური მესაკუთრე (Owner)",
        "reason": "ბაზაში და პორტალზე ფიქსირდება მხოლოდ 1 ბინა (სააგენტოს ნიშნების გარეშე)",
        "unique_apartments": max(1, unique_apartments),
        "total_listings": max(1, total_listings),
    }


def _floors_match(target_floor: Any, cand_floor: Any) -> bool:
    tf = str(target_floor or "").strip().split("/")[0].strip()
    cf = str(cand_floor or "").strip().split("/")[0].strip()
    if not tf or not cf:
        return True
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


def find_exact_duplicates(
    conn: sqlite3.Connection, target: Dict[str, Any]
) -> List[Dict[str, Any]]:
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
        low_area = float(area_m2) - 1.5
        high_area = float(area_m2) + 1.5
        cur.execute(
            """
            SELECT * FROM properties
            WHERE district = ?
              AND area_m2 BETWEEN ? AND ?
              AND id != ?
            ORDER BY price_usd ASC
            LIMIT 25
            """,
            (district, low_area, high_area, target_id),
        )
        for row in cur.fetchall():
            d = dict(row)
            if not _floors_match(target.get("floor"), d.get("floor")):
                continue
            if not _rooms_match(target.get("rooms"), d.get("rooms")):
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
    if not row or not row[0] or int(row[1] or 0) < 1:
        return None
    return {"avg_m2": int(row[0]), "count": int(row[1])}


def _query_by_phone_or_author(
    conn: sqlite3.Connection,
    phone_9: Optional[str] = None,
    formatted_phone: Optional[str] = None,
    author_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    seen: Dict[str, Dict[str, Any]] = {}

    if phone_9:
        fmt = formatted_phone or _format_georgian_phone(phone_9)
        cur.execute(
            "SELECT * FROM properties WHERE phone_number IN (?, ?) ORDER BY scraped_at DESC LIMIT 30",
            (fmt, phone_9),
        )
        for row in cur.fetchall():
            d = dict(row)
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
            phone_9 = parsed["phone_9"]
            fmt_phone = parsed["formatted_phone"]
            related = _query_by_phone_or_author(conn, phone_9=phone_9, formatted_phone=fmt_phone)
            if not related:
                return (
                    f"🔎 <b>ტელეფონის რენტგენი:</b> <code>{fmt_phone}</code>\n\n"
                    "ℹ️ ჩვენს ბაზაში ამ ნომერზე განცხადებები ჯერ არ ფიქსირდება."
                )
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
                    f"• <a href=\"{url}\">{src}</a> — <b>${p_usd:,}</b> | {area:g} მ² | {dist}"
                )
            return "\n".join(lines)

        target: Optional[Dict[str, Any]] = None
        if kind == "url":
            cur.execute("SELECT * FROM properties WHERE id = ?", (parsed["listing_id"],))
            row = cur.fetchone()
            if row:
                target = dict(row)
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

        phone_9 = parsed.get("phone_9") or _extract_9digit_phone(str(target.get("phone_number") or ""))
        author_id = target.get("author_id")
        related = _query_by_phone_or_author(
            conn,
            phone_9=phone_9,
            formatted_phone=_format_georgian_phone(phone_9) if phone_9 else None,
            author_id=author_id,
        )
        verdict = evaluate_broker_verdict(target, related)
        duplicates = find_exact_duplicates(conn, target)
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

        # Price history section
        if curr_price > 0:
            lines.append("")
            lines.append(f"💰 <b>მიმდინარე ფასი:</b> ${int(curr_price):,}" + (f" (${price_m2:,}/მ²)" if price_m2 else ""))
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
                dup_owner = "მესაკუთრე ✅" if dup.get("is_owner") else "სააგენტო 🕵️‍♂️"
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
                    f"• <a href=\"{dup_url}\">{dup_src}</a> — <b>${int(dup_price):,}</b>{diff_str} | {dup_owner}"
                )
        else:
            lines.append("🎯 <b>იგივე ბინა სხვაგან:</b> დუბლიკატი სხვა პორტალზე არ იძებნება.")

        return "\n".join(lines)
    finally:
        conn.close()
