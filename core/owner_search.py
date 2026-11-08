"""Owner search for one flat: the X-Ray's "მეპატრონე" step (ADR 0003 stage 2, ADR 0004).

Runs in the GitHub Actions lookup job (scripts/xray_lookup.py), because MyHome answers the
Worker with a Cloudflare challenge. Given the flat an X-Ray saw (a post, a link, or a phone
number with flat details), it

1. strengthens the target from the agent's own MyHome listing of the same flat, found through
   the agent's phone: area, building height and street come from there when exactly one of the
   agent's listings matches (two or more would mean guessing, so none is used);
2. collects private-seller candidates from MyHome (owner_type=physical), SS.ge
   (individualEntityOnly) and D1 `properties`;
3. keeps a candidate only if the existing agree/conflict/unknown rule accepts it
   (`_candidate_matches_target`), its area is inside the duplicate finder's window
   (`SAME_FLAT_AREA_RATIO`) and it is not the agent's own listing;
4. drops hidden brokers: a MyHome account whose listings are not all one flat, or an SS.ge
   listing whose page shows an agency;
5. ranks a shared photo hash first, then more agreeing fields, then the closer area. Price is
   never a reason to reject; it only orders otherwise equal candidates, cheaper first;
6. shows one seller once: a later owner listing with the same photos, account or full phone
   goes under the first one's line.

Network access goes through `Sources`, so tests run on fakes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.xray import (
    SAME_FLAT_AREA_RATIO,
    _candidate_matches_target,
    _clean_floor,
    _compare_layout,
    _compare_location,
    _compare_total_floors,
    _expand_district_aliases,
    _merge_live_into_row,
    _micro_of,
    evaluate_broker_verdict,
    extract_next_data,
    find_statements_list,
    live_phone_result,
    myhome_search_url,
    parse_myhome_list_item,
)

# Work caps, not matching thresholds: how many result pages to read per portal, how many
# candidates to verify (each costs one more request), and how many to show in Telegram.
PAGES_PER_PORTAL = 2
MAX_VERIFIED = 8
MAX_SHOWN = 5


@dataclass
class Sources:
    """What the owner search reads. The job passes the scrapers; tests pass fakes."""

    myhome_html: Callable[[str], Awaitable[Optional[str]]]
    ssge_search: Callable[[dict], Awaitable[Optional[list]]]
    ssge_details: Callable[[str], Awaitable[Optional[dict]]]
    ssge_normalize: Callable[[dict], Optional[dict]]
    myhome_urbans: Dict[str, List[int]]
    ssge_subdistricts: Dict[str, List[int]]


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _same_flat(target: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    """The X-Ray's rule, plus area: it compares none, and a 70 m² flat passed for 51 m²."""
    if not _candidate_matches_target(target, cand):
        return False
    try:
        ta, ca = float(target.get("area_m2") or 0), float(cand.get("area_m2") or 0)
    except (TypeError, ValueError):
        return True
    return ta <= 0 or ca <= 0 or abs(ca - ta) <= ta * SAME_FLAT_AREA_RATIO


def _same_seller(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Two owner listings from one seller: the same photos, account or full phone number."""
    if a.get("group_id") and a.get("group_id") == b.get("group_id"):
        return True
    if (
        a.get("author_id")
        and a.get("source") == b.get("source")
        and str(a["author_id"]) == str(b.get("author_id"))
    ):
        return True
    pa, pb = _digits(a.get("phone_number"))[-9:], _digits(b.get("phone_number"))[-9:]
    return len(pa) == 9 and pa == pb


def _ids_for(district: str, mapping: Dict[str, List[int]]) -> List[int]:
    ids: set = set()
    for name in _expand_district_aliases(district):
        ids.update(mapping.get(name, []))
    return sorted(ids)


async def _myhome_rows(sources: Sources, params: Dict[str, str]) -> Optional[List[dict]]:
    html = await sources.myhome_html(myhome_search_url(params))
    items = find_statements_list(extract_next_data(html), params) if html else None
    return None if items is None else [parse_myhome_list_item(i) for i in items]


def _statement_from_html(html: Optional[str]) -> Optional[Dict[str, Any]]:
    page_props = ((extract_next_data(html) or {}).get("props") or {}).get("pageProps") or {}
    for q in (page_props.get("dehydratedState") or {}).get("queries") or []:
        statement = (((q.get("state") or {}).get("data") or {}).get("data") or {}).get("statement")
        if isinstance(statement, dict):
            return statement
    return None


async def strengthen_target(target: Dict[str, Any], sources: Sources) -> Dict[str, Any]:
    """Fills area, height, street and the agent's ids from the agent's own listing."""
    phone9 = target.get("phone9")
    if not phone9:
        return target
    html = await sources.myhome_html(myhome_search_url({"q": phone9}))
    items = find_statements_list(extract_next_data(html), {"q": phone9}) if html else None
    if not items:
        return target
    # Same guard as the phone lookup: the first hit's masked phone must be this number's,
    # because MyHome ignores a filter it does not know and then returns unrelated listings.
    first = _statement_from_html(
        await sources.myhome_html(f"https://www.myhome.ge/ka/pr/{items[0].get('id')}/")
    )
    agent_rows = live_phone_result(phone9, items, first, None)["rows"]
    same = [r for r in agent_rows if _same_flat(target, r)]
    if len(same) != 1:
        return target
    flat = same[0]
    strengthened = dict(target)
    for key in ("area_m2", "total_floors", "street", "subdistrict", "price_usd"):
        if not strengthened.get(key) and flat.get(key):
            strengthened[key] = flat[key]
    strengthened["agent_listing_id"] = flat["id"]
    strengthened["agent_author_id"] = flat.get("author_id")
    return strengthened


async def _myhome_candidates(target: Dict[str, Any], sources: Sources) -> Optional[List[dict]]:
    urbans = _ids_for(target.get("district") or "", sources.myhome_urbans)
    if not urbans:
        return []
    params = {
        "deal_types": "2" if target.get("deal_type") == "rent" else "1",
        "real_estate_types": "1",
        "cities": "1",
        "owner_type": "physical",
        "urbans": ",".join(str(u) for u in urbans),
    }
    if target.get("rooms"):
        params["room_types"] = str(target["rooms"])
    floor = _clean_floor(target.get("floor"))
    if floor:
        params["floor_from"] = params["floor_to"] = floor
    found: List[dict] = []
    for page in range(1, PAGES_PER_PORTAL + 1):
        rows = await _myhome_rows(sources, {**params, "page": str(page)})
        if rows is None:
            return None if page == 1 else found
        found.extend(rows)
        if not rows:
            break
    return found


async def _ssge_candidates(target: Dict[str, Any], sources: Sources) -> Optional[List[dict]]:
    subdistricts = _ids_for(target.get("district") or "", sources.ssge_subdistricts)
    if not subdistricts:
        return []
    payload: Dict[str, Any] = {
        "realEstateType": 5,
        "realEstateDealType": 1 if target.get("deal_type") == "rent" else 4,
        "cityIdList": [95],
        "currencyId": 2,
        "pageSize": 16,
        "order": 1,
        "subdistrictIds": subdistricts,
        "advancedSearch": {"individualEntityOnly": True},
    }
    if target.get("rooms"):
        payload["rooms"] = [int(target["rooms"])]
    found: List[dict] = []
    for page in range(1, PAGES_PER_PORTAL + 1):
        items = await sources.ssge_search({**payload, "page": page})
        if items is None:
            return None if page == 1 else found
        found.extend(r for r in (sources.ssge_normalize(i) for i in items) if r)
        if not items:
            break
    return found


def _d1_candidates(target: Dict[str, Any], conn: Any) -> List[dict]:
    rows: Dict[str, dict] = {}
    aliases = _expand_district_aliases(target.get("district") or "")
    if aliases:
        sql = [
            f"SELECT * FROM properties WHERE district IN ({', '.join('?' for _ in aliases)})",
            "AND (deal_type IS NULL OR deal_type = ?)",
        ]
        params: List[Any] = [*aliases, target.get("deal_type") or "sale"]
        if target.get("rooms"):
            sql.append("AND (rooms IS NULL OR rooms <= 0 OR rooms = ?)")
            params.append(int(target["rooms"]))
        floor = _clean_floor(target.get("floor"))
        if floor:
            sql.append("AND (floor = ? OR floor LIKE ?)")
            params += [floor, f"{floor}/%"]
        sql.append("ORDER BY scraped_at DESC LIMIT 50")
        for r in conn.execute(" ".join(sql), tuple(params)).fetchall():
            rows[r["id"]] = dict(r)
    if target.get("group_id"):
        for r in conn.execute(
            "SELECT * FROM properties WHERE group_id = ?", (target["group_id"],)
        ).fetchall():
            rows[r["id"]] = dict(r)
    return list(rows.values())


def _is_agents_own(target: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    if cand.get("id") in (target.get("agent_listing_id"), target.get("listing_id")):
        return True
    agent_ids = {target.get("agent_author_id"), target.get("author_id")} - {None, ""}
    if cand.get("author_id") and str(cand["author_id"]) in {str(a) for a in agent_ids}:
        return True
    agent_phone = _digits(target.get("phone9"))
    cand_phone = _digits(cand.get("phone_number"))
    return len(agent_phone) == 9 and len(cand_phone) >= 6 and cand_phone[:6] == agent_phone[:6]


def _agreements(target: Dict[str, Any], cand: Dict[str, Any]) -> int:
    checks = (_compare_layout, _compare_total_floors, _compare_location)
    return sum(1 for check in checks if check(target, cand) == "agree")


def _rank_key(target: Dict[str, Any], cand: Dict[str, Any]) -> tuple:
    photo = bool(target.get("group_id")) and cand.get("group_id") == target.get("group_id")
    try:
        area_gap = abs(float(cand.get("area_m2") or 0) - float(target.get("area_m2") or 0))
        known_area = bool(cand.get("area_m2")) and bool(target.get("area_m2"))
    except (TypeError, ValueError):
        area_gap, known_area = 0.0, False
    return (
        not photo,
        -_agreements(target, cand),
        not known_area,
        area_gap,
        float(cand.get("price_usd") or 0),
    )


async def _verified_owner(cand: Dict[str, Any], sources: Sources) -> Optional[Dict[str, Any]]:
    """The candidate with its owner fields, or None when it is a broker or unknown."""
    if cand.get("source") == "ss_ge":
        details = await sources.ssge_details(str(cand.get("source_id") or ""))
        if details is None:
            return None
        merged = _merge_live_into_row(cand, details)
        if details.get("agency_id") or evaluate_broker_verdict(merged, [merged])["is_broker"]:
            return None
        return merged if merged.get("is_owner") in (1, True) else None
    if not cand.get("author_id"):
        return None
    account = await _myhome_rows(sources, {"users": str(cand["author_id"])})
    if not account:
        return None
    # An owner sells one flat; an account whose listings are several flats is a broker.
    verdict = evaluate_broker_verdict(cand, account)
    if verdict["is_broker"] or verdict["unique_apartments"] != 1:
        return None
    return {**cand, "is_owner": 1}


async def search_owner(target: Dict[str, Any], conn: Any, sources: Sources) -> Dict[str, Any]:
    target = await strengthen_target(target, sources)
    myhome, ssge = await asyncio.gather(
        _myhome_candidates(target, sources), _ssge_candidates(target, sources)
    )
    failed = [name for name, rows in (("myhome", myhome), ("ss.ge", ssge)) if rows is None]
    pool: Dict[str, dict] = {}
    for cand in [*(myhome or []), *(ssge or []), *_d1_candidates(target, conn)]:
        if not cand.get("id") or cand["id"] in pool or _is_agents_own(target, cand):
            continue
        if _same_flat(target, cand):
            pool[cand["id"]] = cand
    ranked = sorted(pool.values(), key=lambda c: _rank_key(target, c))
    owners: List[dict] = []
    for cand in ranked[:MAX_VERIFIED]:
        verified = await _verified_owner(cand, sources)
        if not verified:
            continue
        first = next((o for o in owners if _same_seller(o, verified)), None)
        if first:
            first["also"].append(verified.get("url"))
            continue
        owners.append({**verified, "also": []})
        if len(owners) == MAX_SHOWN:
            break
    weak = not target.get("area_m2") and not target.get("agent_listing_id")
    return {"target": target, "owners": owners, "weak": weak, "failed": failed}


def target_summary(target: Dict[str, Any]) -> str:
    parts = [target.get("district") or "უცნობი უბანი"]
    micro = _micro_of(target)
    if micro:
        parts.append(f"მე-{micro} მიკრო")
    if target.get("rooms"):
        parts.append(f"{target['rooms']} ოთახი")
    floor = _clean_floor(target.get("floor"))
    if floor:
        parts.append(f"{floor} სართ.")
    if target.get("area_m2"):
        parts.append(f"{float(target['area_m2']):g} მ²")
    return ", ".join(parts)


def owner_report(result: Dict[str, Any]) -> str:
    """The follow-up message; candidate numbers match the ✅/❌ buttons under it."""
    target = result["target"]
    owners = result["owners"]
    lines = [f"🏠 <b>მესაკუთრის ძებნა:</b> {target_summary(target)}", ""]
    ref = float(target.get("price_usd") or 0)
    if not owners:
        lines.append("ℹ️ მესაკუთრის განცხადება ვერ მოიძებნა (myhome, ss.ge, ჩვენი ბაზა).")
    photo = [
        c for c in owners if target.get("group_id") and c.get("group_id") == target["group_id"]
    ]
    rest = [c for c in owners if c not in photo]
    n = 0
    for title, group in (("✅ <b>ფოტოები ემთხვევა:</b>", photo), ("🔎 <b>სავარაუდო:</b>", rest)):
        if not group:
            continue
        lines.append(title)
        for c in group:
            n += 1
            price = float(c.get("price_usd") or 0)
            diff = ""
            if ref > 0 and price > 0:
                delta = round(price - ref)
                if delta < 0:
                    diff = f" (<b>-${abs(delta):,} აგენტზე იაფად</b>)"
                elif delta > 0:
                    diff = f" (+${delta:,} აგენტზე ძვირად)"
                else:
                    diff = " (იგივე ფასი)"
            src = "MyHome" if c.get("source") == "myhome" else "SS.ge"
            floor = _clean_floor(c.get("floor"))
            height = c.get("total_floors")
            floor_str = f" | {floor}{f'/{height}' if height else ''} სართ." if floor else ""
            area = f" | {float(c['area_m2']):g} მ²" if c.get("area_m2") else ""
            also = c.get("also") or []
            more = ""
            if also:
                links = ", ".join(f'<a href="{u or "#"}">{src}</a>' for u in also)
                more = f" | კიდევ {len(also)} განცხადება: {links}"
            lines.append(
                f'{n}. <a href="{c.get("url") or "#"}">{src}</a> — <b>${int(price):,}</b>{diff}'
                f"{area}{floor_str} | მესაკუთრე{more}"
            )
    if owners:
        lines += ["", "მონიშნე ქვემოთ: ✅ იგივე ბინაა, ❌ სხვაა."]
    if result.get("weak"):
        lines.append(
            "ℹ️ ტექსტში ფართი არ წერია და აგენტის იგივე განცხადება ვერ ვიპოვე, "
            "ამიტომ ეს მხოლოდ კანდიდატებია."
        )
    for name in result.get("failed") or []:
        lines.append(f"⚠️ {name} ახლა არ პასუხობს, მისი შედეგი აქ არ არის.")
    return "\n".join(lines)
