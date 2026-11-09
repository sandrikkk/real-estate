"""ADR 0002, weekly review: pairs the bot did not choose, sent to the admin to answer.

Organic answers only cover pairs X-Ray showed, which the old rule had already accepted. Each
week this sends a few close-area pairs from the whole database, without saying why each was
picked (so the answer is not anchored):
- "review_uncertain": pairs where the old rule and the new weights disagree, one pair per kind
  of disagreement (its comparison pattern and which rule says yes), the kinds that cover the
  most pairs first, so each answer settles a different question; topped up with the pairs
  closest to one half;
- "review_random": a weighted random draw (half in proportion to the posterior, half uniform),
  each pick keeping its probability `q`, so answers can later be re-weighted into unbiased
  counts (importance sampling).
A pair already answered or already sent is never sent again.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import math
import time
import uuid
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from PIL import Image, ImageOps
from curl_cffi import requests

from core.dedup import MAX_IMAGE_BYTES
from core.xray import _candidate_matches_target, _clean_floor, snapshot_fields
from core.xray_weights import (
    POSSIBLE,
    iter_block_pairs,
    log_odds,
)

PHOTOS_PER_SIDE = 4  # photos of each listing in the picture; display only
TILE = (320, 240)  # one photo's cell, in pixels; display only

# MyHome moved its photo host in October 2026. Photos saved before that keep the old host,
# which no longer resolves; the same path still serves on the new one.
OLD_MYHOME_PHOTO_HOST = "//static-statements.tnet.ge/"
NEW_MYHOME_PHOTO_HOST = "//static-api-statements.tnet.ge/"
REVIEW_CAPTION_FOOTER = (
    "ზედა რიგი: ა, ქვედა რიგი: ბ. ფოტოები ჩვენი ბაზიდანაა, "
    "ამიტომ ჩანს მაშინაც, როცა განცხადება წაშლილია."
)


def current_photo_url(url: str) -> str:
    return url.replace(OLD_MYHOME_PHOTO_HOST, NEW_MYHOME_PHOTO_HOST, 1) if url else url


def load_photo_urls(conn: Any, ids: Any) -> Dict[str, List[str]]:
    """The first PHOTOS_PER_SIDE saved photo URLs of each listing, hosts corrected.
    One query: SELECT id, images_json FROM properties WHERE id IN (...). Bad JSON gives []."""
    result: Dict[str, List[str]] = {}
    id_list = list(dict.fromkeys(ids))
    if not id_list:
        return result
    for i in range(0, len(id_list), 50):
        chunk = id_list[i : i + 50]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, images_json FROM properties WHERE id IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        for r in rows:
            try:
                rid = r["id"]
                raw = r["images_json"]
            except (TypeError, KeyError, IndexError):
                rid, raw = r[0], r[1]
            urls: List[str] = []
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        urls = [current_photo_url(u) for u in parsed if isinstance(u, str)][
                            :PHOTOS_PER_SIDE
                        ]
                except Exception:
                    urls = []
            result[str(rid)] = urls
    return result


def photo_downloader() -> Callable[[str], Optional[bytes]]:
    """download(url) -> bytes, or None unless HTTP 200 and at most MAX_IMAGE_BYTES.
    curl_cffi requests.Session(impersonate="chrome124"), the impersonation core/dedup.py uses,
    timeout 15 s. Prints nothing."""
    session = requests.Session(impersonate="chrome124")

    def download(url: str) -> Optional[bytes]:
        if not url:
            return None
        target_url = f"https:{url}" if url.startswith("//") else url
        try:
            resp = session.get(target_url, timeout=15)
            if resp.status_code == 200 and resp.content:
                data = resp.content
                if 0 < len(data) <= MAX_IMAGE_BYTES:
                    return data
            return None
        except Exception:
            return None

    return download


def collage(rows: List[List[bytes]]) -> Optional[bytes]:
    """One JPEG: row 0 = listing ა, row 1 = listing ბ. Each photo is opened with Pillow
    (skip any that fails to open), converted to RGB and fitted into TILE with
    ImageOps.pad(img, TILE, color=(255, 255, 255)). Width = TILE[0] * max(1, longest row),
    height = TILE[1] * 2; an empty row stays white. JPEG quality 85.
    None when both rows end up empty."""

    def _tiles(img_bytes_list: List[bytes]) -> List[Image.Image]:
        tiles: List[Image.Image] = []
        for b in img_bytes_list:
            try:
                img = Image.open(io.BytesIO(b))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                padded = ImageOps.pad(img, TILE, color=(255, 255, 255))
                tiles.append(padded)
            except Exception:
                continue
        return tiles

    row_0 = rows[0] if len(rows) > 0 else []
    row_1 = rows[1] if len(rows) > 1 else []
    tiles_0 = _tiles(row_0)
    tiles_1 = _tiles(row_1)

    if not tiles_0 and not tiles_1:
        return None

    longest_row = max(len(tiles_0), len(tiles_1))
    width = TILE[0] * max(1, longest_row)
    height = TILE[1] * 2

    canvas = Image.new("RGB", (width, height), color=(255, 255, 255))
    for idx, tile in enumerate(tiles_0):
        canvas.paste(tile, (idx * TILE[0], 0))
    for idx, tile in enumerate(tiles_1):
        canvas.paste(tile, (idx * TILE[0], TILE[1]))

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=85)
    return out.getvalue()


REVIEW_UNCERTAIN = 5  # pairs per week; sets the message volume, not a model parameter
REVIEW_RANDOM = 5  # pairs per week; sets the message volume, not a model parameter
REVIEW_KINDS = ("review_uncertain", "review_random")


def pair_key(a_id: Any, b_id: Any) -> str:
    return "__".join(sorted([str(a_id), str(b_id)]))


def seen_review_items(conn: Any, records: List[Dict[str, Any]]) -> Tuple[Set[str], Set[str]]:
    """(seen_pairs, seen_listings): pairs already in golden records or sent for review,
    and listing IDs already sent for review (target or candidates[0])."""
    seen_pairs: Set[str] = {r["listing_pair_key"] for r in records if r.get("listing_pair_key")}
    seen_listings: Set[str] = set()
    try:
        rows = conn.execute(
            "SELECT target, candidates FROM xray_results WHERE kind IN ('review_uncertain', 'review_random')"
        ).fetchall()
        for r in rows:
            try:
                t = json.loads(r["target"]) if isinstance(r["target"], str) else (r["target"] or {})
                cands = (
                    json.loads(r["candidates"])
                    if isinstance(r["candidates"], str)
                    else (r["candidates"] or [])
                )
                t_id = t.get("id")
                c_id = cands[0].get("id") if (cands and isinstance(cands, list)) else None
                if t_id:
                    seen_listings.add(str(t_id))
                if c_id:
                    seen_listings.add(str(c_id))
                if t_id and c_id:
                    seen_pairs.add(pair_key(t_id, c_id))
            except Exception:
                continue
    except Exception:
        pass
    return seen_pairs, seen_listings


def seen_pair_keys(conn: Any, records: List[Dict[str, Any]]) -> Set[str]:
    pairs, _ = seen_review_items(conn, records)
    return pairs


def seen_listing_ids(conn: Any) -> Set[str]:
    _, listings = seen_review_items(conn, [])
    return listings


def _hash01(text: str) -> float:
    return (int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) + 1) / (2**256 + 1)


class PoolItem(tuple):
    key: str
    a: Dict[str, Any]
    b: Dict[str, Any]
    post: float
    champ: bool
    levels: Tuple[str, ...]

    def __new__(
        cls,
        key: str,
        a: Dict[str, Any],
        b: Dict[str, Any],
        post: float,
        champ: bool,
        levels: Tuple[str, ...],
    ):
        obj = super().__new__(cls, (key, a, b, post, champ))
        obj.key = key
        obj.a = a
        obj.b = b
        obj.post = post
        obj.champ = champ
        obj.levels = levels
        return obj


def _pattern_posterior(weights: Dict[str, Any], levels: Tuple[str, ...]) -> float:
    z = log_odds(weights, levels)
    if z <= -700:
        return 0.0
    if z >= 700:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def _pool(
    listings: List[Dict[str, Any]],
    weights: Dict[str, Any],
    exclude: Set[str],
    post_by_pattern: Dict[Tuple[str, ...], float],
    seen_listing_ids: Optional[Set[str]] = None,
) -> Iterator[PoolItem]:
    seen_ids = seen_listing_ids or set()
    for a, b, levels in iter_block_pairs(listings):
        if levels[1] not in ("equal", "close"):
            continue
        id_a = str(a.get("id") or "")
        id_b = str(b.get("id") or "")
        if id_a in seen_ids or id_b in seen_ids:
            continue
        g_a = str(a.get("group_id") or "").strip()
        g_b = str(b.get("group_id") or "").strip()
        if g_a and g_b and g_a == g_b:
            continue
        key = pair_key(a["id"], b["id"])
        if key in exclude:
            continue
        if levels not in post_by_pattern:
            post_by_pattern[levels] = _pattern_posterior(weights, levels)
        post = post_by_pattern[levels]
        champ = bool(_candidate_matches_target(a, b))
        yield PoolItem(key, a, b, post, champ, levels)


def _round_robin(
    sorted_kinds: List[Tuple[Any, Dict[str, Any]]],
    n: int,
    used_listing_ids: Optional[Set[str]] = None,
) -> List[Tuple[PoolItem, int]]:
    """Return up to n items round-robin across kinds (ordered by count descending).
    Each returned tuple is (PoolItem, count). Skips a kind's item when either of its listing ids
    is already in used_listing_ids, and takes that kind's following item."""
    picks: List[Tuple[PoolItem, int]] = []
    if n <= 0:
        return picks
    used = used_listing_ids if used_listing_ids is not None else set()
    cursors = [0] * len(sorted_kinds)
    while len(picks) < n:
        added_any = False
        for k_idx, (_kind, data) in enumerate(sorted_kinds):
            items = data.get("items", [])
            while cursors[k_idx] < len(items):
                idx = cursors[k_idx]
                cursors[k_idx] += 1
                raw = items[idx]
                item = raw[1] if isinstance(raw, (list, tuple)) else raw
                id_a = str(item.a.get("id") or "")
                id_b = str(item.b.get("id") or "")
                if id_a in used or id_b in used:
                    continue
                picks.append((item, data.get("count", len(items))))
                used.add(id_a)
                used.add(id_b)
                added_any = True
                break
            if len(picks) == n:
                break
        if not added_any:
            break
    return picks


def pick_review_pairs(
    listings: List[Dict[str, Any]],
    weights: Dict[str, Any],
    pools_counts: Dict[str, Any],
    exclude: Set[str],
    week: str,
    n_uncertain: int = REVIEW_UNCERTAIN,
    n_random: int = REVIEW_RANDOM,
    seen_listing_ids: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    if n_uncertain == 0 and n_random == 0:
        return []

    close_area_counts = pools_counts.get("close_area", {})
    N = sum(close_area_counts.values())

    post_by_pattern: Dict[Tuple[str, ...], float] = {}
    for p in close_area_counts:
        p_tuple = tuple(p) if isinstance(p, (list, tuple)) else p
        post_by_pattern[p_tuple] = _pattern_posterior(weights, p_tuple)

    P = sum(
        n * post_by_pattern[tuple(p) if isinstance(p, (list, tuple)) else p]
        for p, n in sorted(close_area_counts.items(), key=lambda item: str(item[0]))
    )

    kinds: Dict[Tuple[Tuple[str, ...], bool], Dict[str, Any]] = {}
    fallback: List[Tuple[float, str, PoolItem]] = []
    rand: List[Tuple[float, str, PoolItem, float]] = []

    for x in _pool(listings, weights, exclude, post_by_pattern, seen_listing_ids):
        is_disagree = x.champ != (x.post > POSSIBLE)
        u_hash = hashlib.sha256(f"{week}:u:{x.key}".encode("utf-8")).hexdigest()

        if is_disagree and n_uncertain > 0:
            kind = (x.levels, x.champ)
            if kind not in kinds:
                kinds[kind] = {"count": 1, "items": [(u_hash, x)]}
            else:
                kinds[kind]["count"] += 1
                items = kinds[kind]["items"]
                cap = max(50, n_uncertain * 2)
                if len(items) < cap:
                    items.append((u_hash, x))
                    items.sort(key=lambda t: t[0])
                elif u_hash < items[-1][0]:
                    items[-1] = (u_hash, x)
                    items.sort(key=lambda t: t[0])
        elif n_uncertain > 0:
            fb_val = (abs(x.post - 0.5), u_hash)
            cap_fb = max(100, n_uncertain * 3)
            if len(fallback) < cap_fb:
                fallback.append((fb_val[0], fb_val[1], x))
                fallback.sort(key=lambda t: (t[0], t[1]))
            elif fb_val < (fallback[-1][0], fallback[-1][1]):
                fallback[-1] = (fb_val[0], fb_val[1], x)
                fallback.sort(key=lambda t: (t[0], t[1]))

        if n_random > 0:
            if P > 0 and N > 0:
                q_val = 0.5 * x.post / P + 0.5 / N
            elif N > 0:
                q_val = 1.0 / N
            else:
                q_val = 1.0

            h01 = _hash01(f"{week}:r:{x.key}")
            es_key = -math.log(h01) / q_val
            k_rand = max(100, (n_random + n_uncertain) * 4)
            if len(rand) < k_rand:
                rand.append((es_key, x.key, x, q_val))
                rand.sort(key=lambda t: t[0])
            elif es_key < rand[-1][0]:
                rand[-1] = (es_key, x.key, x, q_val)
                rand.sort(key=lambda t: t[0])

    used_listing_ids: Set[str] = set()
    sorted_kinds = sorted(kinds.items(), key=lambda item: (-item[1]["count"], str(item[0])))
    uncertain_picks: List[Dict[str, Any]] = []
    if n_uncertain > 0:
        for item, count in _round_robin(sorted_kinds, n_uncertain, used_listing_ids):
            uncertain_picks.append(
                {
                    "a": item.a,
                    "b": item.b,
                    "key": item.key,
                    "origin": "review_uncertain",
                    "posterior": item.post,
                    "champion": item.champ,
                    "kind_pairs": count,
                    "q": None,
                    "pool": N,
                }
            )

        if len(uncertain_picks) < n_uncertain:
            for _, _, fb_item in fallback:
                id_a = str(fb_item.a.get("id") or "")
                id_b = str(fb_item.b.get("id") or "")
                if id_a in used_listing_ids or id_b in used_listing_ids:
                    continue
                uncertain_picks.append(
                    {
                        "a": fb_item.a,
                        "b": fb_item.b,
                        "key": fb_item.key,
                        "origin": "review_uncertain",
                        "posterior": fb_item.post,
                        "champion": fb_item.champ,
                        "kind_pairs": None,
                        "q": None,
                        "pool": N,
                    }
                )
                used_listing_ids.add(id_a)
                used_listing_ids.add(id_b)
                if len(uncertain_picks) == n_uncertain:
                    break

    uncertain_keys = {p["key"] for p in uncertain_picks}
    random_picks: List[Dict[str, Any]] = []
    if n_random > 0:
        rand.sort(key=lambda t: t[0])
        for _es_k, key, item, q_val in rand:
            if key in uncertain_keys:
                continue
            id_a = str(item.a.get("id") or "")
            id_b = str(item.b.get("id") or "")
            if id_a in used_listing_ids or id_b in used_listing_ids:
                continue
            random_picks.append(
                {
                    "a": item.a,
                    "b": item.b,
                    "key": item.key,
                    "origin": "review_random",
                    "posterior": item.post,
                    "champion": item.champ,
                    "kind_pairs": None,
                    "q": q_val,
                    "pool": N,
                }
            )
            used_listing_ids.add(id_a)
            used_listing_ids.add(id_b)
            if len(random_picks) == n_random:
                break

    all_picks = uncertain_picks + random_picks
    all_picks.sort(key=lambda p: hashlib.sha256(f"{week}:o:{p['key']}".encode("utf-8")).hexdigest())
    return all_picks


def review_keyboard(result_id: str) -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "1 ✅ იგივეა", "callback_data": f"fd:{result_id}:0:y"},
                {"text": "1 ❌ სხვაა", "callback_data": f"fd:{result_id}:0:n"},
                {"text": "1 🤷 ვერ ვხვდები", "callback_data": f"fd:{result_id}:0:u"},
            ]
        ]
    }


def _format_facts(item: Dict[str, Any]) -> str:
    parts = []
    dist = item.get("district")
    if dist:
        parts.append(html.escape(str(dist).strip()))
    rooms = item.get("rooms")
    if rooms is not None and str(rooms).strip():
        parts.append(f"{html.escape(str(rooms).strip())} ოთახი")
    fl = _clean_floor(item.get("floor"))
    if fl:
        tf = item.get("total_floors")
        if tf is not None and str(tf).strip():
            parts.append(f"{html.escape(fl)}/{html.escape(str(tf).strip())} სართ.")
        else:
            parts.append(f"{html.escape(fl)} სართ.")
    ar = item.get("area_m2") if item.get("area_m2") is not None else item.get("area")
    if ar is not None and str(ar).strip():
        try:
            parts.append(f"{float(ar):g} მ²")
        except (ValueError, TypeError):
            pass
    pr = item.get("price_usd") if item.get("price_usd") is not None else item.get("price")
    if pr is not None and str(pr).strip():
        try:
            price_int = int(round(float(pr)))
            parts.append(f"${price_int:,}")
        except (ValueError, TypeError):
            pass
    return " | ".join(parts)


def review_text(i: int, total: int, a: Dict[str, Any], b: Dict[str, Any]) -> str:
    src_a = "MyHome" if a.get("source") == "myhome" else "SS.ge"
    src_b = "MyHome" if b.get("source") == "myhome" else "SS.ge"
    url_a = html.escape(str(a.get("url") or ""), quote=True)
    url_b = html.escape(str(b.get("url") or ""), quote=True)
    facts_a = _format_facts(a)
    facts_b = _format_facts(b)
    return (
        f"🔍 <b>კვირის შემოწმება</b> {i}/{total}\n"
        "ეს ორი განცხადება ერთი და იგივე ბინაა?\n\n"
        f'ა) <a href="{url_a}">{src_a}</a>: {facts_a}\n'
        f'ბ) <a href="{url_b}">{src_b}</a>: {facts_b}'
    )


def review_caption(i: int, total: int, a: Dict[str, Any], b: Dict[str, Any]) -> str:
    return f"{review_text(i, total, a, b)}\n\n{REVIEW_CAPTION_FOOTER}"


def send_review(
    conn: Any,
    send: Any,
    chat_id: Any,
    picks: List[Dict[str, Any]],
    version: str,
    week: str,
    now_iso: str,
    sleep: Any = time.sleep,
    send_photo: Any = None,
    download: Any = None,
    photo_urls: Optional[Dict[str, List[str]]] = None,
) -> int:
    sent = 0
    total = len(picks)
    for i, pick in enumerate(picks, 1):
        rid = str(uuid.uuid4())
        a = pick["a"]
        b = pick["b"]
        target = {
            **snapshot_fields(a),
            "_review": {
                "origin": pick["origin"],
                "posterior": pick["posterior"],
                "champion": pick["champion"],
                "kind_pairs": pick.get("kind_pairs"),
                "q": pick.get("q"),
                "pool": pick["pool"],
                "week": week,
                "version": version,
            },
        }
        candidates = [snapshot_fields(b)]
        conn.execute(
            """
            INSERT INTO xray_results (id, chat_id, kind, target, candidates, owner_target_id, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                rid,
                str(chat_id),
                pick["origin"],
                json.dumps(target, ensure_ascii=False),
                json.dumps(candidates, ensure_ascii=False),
                now_iso,
            ),
        )
        conn.commit()

        jpeg = None
        if send_photo is not None and download is not None and photo_urls is not None:
            a_urls = photo_urls.get(str(a.get("id")), photo_urls.get(a.get("id"), []))
            b_urls = photo_urls.get(str(b.get("id")), photo_urls.get(b.get("id"), []))
            rows = [
                [x for x in map(download, a_urls) if x],
                [x for x in map(download, b_urls) if x],
            ]
            jpeg = collage(rows)

        if jpeg is not None:
            resp = send_photo(
                chat_id,
                jpeg,
                review_caption(i, total, a, b),
                review_keyboard(rid),
            )
        else:
            resp = send(
                "sendMessage",
                {
                    "chat_id": str(chat_id),
                    "text": review_text(i, total, a, b),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": review_keyboard(rid),
                },
            )

        if not resp or not resp.get("ok"):
            conn.execute("DELETE FROM xray_results WHERE id = ?", (rid,))
            conn.commit()
            continue

        sent += 1
        sleep(1)

    return sent
