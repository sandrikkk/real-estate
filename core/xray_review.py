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
import json
import math
import time
import uuid
from typing import Any, Dict, Iterator, List, Set, Tuple

from core.xray import _candidate_matches_target, _clean_floor, snapshot_fields
from core.xray_weights import (
    POSSIBLE,
    iter_block_pairs,
    log_odds,
)

REVIEW_UNCERTAIN = 5  # pairs per week; sets the message volume, not a model parameter
REVIEW_RANDOM = 5  # pairs per week; sets the message volume, not a model parameter
REVIEW_KINDS = ("review_uncertain", "review_random")


def pair_key(a_id: Any, b_id: Any) -> str:
    return "__".join(sorted([str(a_id), str(b_id)]))


def seen_pair_keys(conn: Any, records: List[Dict[str, Any]]) -> Set[str]:
    seen: Set[str] = {r["listing_pair_key"] for r in records if r.get("listing_pair_key")}
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
                if t.get("id") and cands and cands[0].get("id"):
                    seen.add(pair_key(t["id"], cands[0]["id"]))
            except Exception:
                continue
    except Exception:
        pass
    return seen


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
) -> Iterator[PoolItem]:
    for a, b, levels in iter_block_pairs(listings):
        if levels[1] not in ("equal", "close"):
            continue
        key = pair_key(a["id"], b["id"])
        if key in exclude:
            continue
        if levels not in post_by_pattern:
            post_by_pattern[levels] = _pattern_posterior(weights, levels)
        post = post_by_pattern[levels]
        champ = bool(_candidate_matches_target(a, b))
        yield PoolItem(key, a, b, post, champ, levels)


def pick_review_pairs(
    listings: List[Dict[str, Any]],
    weights: Dict[str, Any],
    pools_counts: Dict[str, Any],
    exclude: Set[str],
    week: str,
) -> List[Dict[str, Any]]:
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

    for x in _pool(listings, weights, exclude, post_by_pattern):
        is_disagree = x.champ != (x.post > POSSIBLE)
        u_hash = hashlib.sha256(f"{week}:u:{x.key}".encode("utf-8")).hexdigest()

        if is_disagree:
            kind = (x.levels, x.champ)
            if kind not in kinds:
                kinds[kind] = {"count": 1, "best_hash": u_hash, "item": x}
            else:
                kinds[kind]["count"] += 1
                if u_hash < kinds[kind]["best_hash"]:
                    kinds[kind]["best_hash"] = u_hash
                    kinds[kind]["item"] = x
        else:
            fb_val = (abs(x.post - 0.5), u_hash)
            if len(fallback) < REVIEW_UNCERTAIN:
                fallback.append((fb_val[0], fb_val[1], x))
                fallback.sort(key=lambda t: (t[0], t[1]))
            elif fb_val < (fallback[-1][0], fallback[-1][1]):
                fallback[-1] = (fb_val[0], fb_val[1], x)
                fallback.sort(key=lambda t: (t[0], t[1]))

        if P > 0 and N > 0:
            q_val = 0.5 * x.post / P + 0.5 / N
        elif N > 0:
            q_val = 1.0 / N
        else:
            q_val = 1.0

        h01 = _hash01(f"{week}:r:{x.key}")
        es_key = -math.log(h01) / q_val
        k_rand = REVIEW_RANDOM + REVIEW_UNCERTAIN
        if len(rand) < k_rand:
            rand.append((es_key, x.key, x, q_val))
            rand.sort(key=lambda t: t[0])
        elif es_key < rand[-1][0]:
            rand[-1] = (es_key, x.key, x, q_val)
            rand.sort(key=lambda t: t[0])

    sorted_kinds = sorted(kinds.items(), key=lambda item: (-item[1]["count"], str(item[0])))
    uncertain_picks: List[Dict[str, Any]] = []
    for _kind, data in sorted_kinds[:REVIEW_UNCERTAIN]:
        item = data["item"]
        uncertain_picks.append(
            {
                "a": item.a,
                "b": item.b,
                "key": item.key,
                "origin": "review_uncertain",
                "posterior": item.post,
                "champion": item.champ,
                "kind_pairs": data["count"],
                "q": None,
                "pool": N,
            }
        )

    if len(uncertain_picks) < REVIEW_UNCERTAIN:
        needed = REVIEW_UNCERTAIN - len(uncertain_picks)
        for _, _, fb_item in fallback[:needed]:
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

    uncertain_keys = {p["key"] for p in uncertain_picks}
    random_picks: List[Dict[str, Any]] = []
    rand.sort(key=lambda t: t[0])
    for _es_k, key, item, q_val in rand:
        if key in uncertain_keys:
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
        if len(random_picks) == REVIEW_RANDOM:
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


def send_review(
    conn: Any,
    send: Any,
    chat_id: Any,
    picks: List[Dict[str, Any]],
    version: str,
    week: str,
    now_iso: str,
    sleep: Any = time.sleep,
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
