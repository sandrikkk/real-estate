"""ADR 0002: learn the X-Ray duplicate-match weights from the whole database and test them on
the admin's answers.

Fellegi-Sunter, as in Splink: `u` (chance agreement) counted exactly over every pair in a block,
`m` (agreement on a true match) by expectation maximisation in two sessions, each blocked on one
field it therefore cannot learn, and λ (match prevalence) from EM. Answers are never fitted:
they are the exam (`gate`). No number here is tuned by hand.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Iterator, List, Optional, Tuple

from core.xray import (
    SAME_FLAT_AREA_RATIO,
    _candidate_matches_target,
    _clean_floor,
    _compare_layout,
    _compare_location,
    _compare_total_floors,
)

FIELDS = ("floor", "area", "layout", "total_floors", "location")
LEVELS = {
    "floor": ("equal", "off_by_one", "further"),
    "area": ("equal", "close", "far"),
    "layout": ("agree", "conflict"),
    "total_floors": ("agree", "conflict"),
    "location": ("agree", "conflict"),
}
UNKNOWN = "unknown"  # a side is missing: the field adds nothing
LEVELS_VERSION = 1
EM_MAX_ITERATIONS = 10_000  # a guard against a bug, not a tuning value; the report shows the count
LISTING_COLUMNS = (
    "id",
    "source",
    "url",
    "district",
    "subdistrict",
    "street",
    "floor",
    "total_floors",
    "rooms",
    "bedrooms",
    "area_m2",
    "price_usd",
    "deal_type",
    "group_id",
)
PAGE = 1000
POSSIBLE = 0.5  # ADR 0002: "possible duplicate" above one half (Bayes decision rule)
STRONG = ("floor", "area", "layout", "total_floors")
TOP = {f: LEVELS[f][0] for f in FIELDS}  # "equal" / "agree"
Z95 = 1.959964  # two-sided 95%: a statistical convention, not a tuned value


def wilson95(k: float, n: float) -> Tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    c = (p + Z95 * Z95 / (2 * n)) / (1 + Z95 * Z95 / n)
    h = Z95 * math.sqrt(p * (1 - p) / n + Z95 * Z95 / (4 * n * n)) / (1 + Z95 * Z95 / n)
    return (max(0.0, c - h), min(1.0, c + h))


def photo_floor_check(
    groups: List[Tuple[Tuple[str, ...], float]], m_floor_equal: float
) -> Dict[str, Any]:
    """dedup groups photos without looking at the floor, so the photo groups are independent
    evidence for the floor: EM's floor-equal m must sit inside their 95% interval. A fit that
    learned look-alike flats instead of the same flat fails here."""
    k = sum(w for p, w in groups if p[0] == "equal")
    n = sum(w for p, w in groups if p[0] != UNKNOWN)
    lo, hi = wilson95(k, n)
    return {
        "share": k / n if n else None,
        "groups": n,
        "low": lo,
        "high": hi,
        "em": m_floor_equal,
        "ok": bool(n > 0 and lo <= m_floor_equal <= hi),
    }


def leave_one_out_pool(
    patterns: Dict[tuple, int], field: str
) -> Tuple[Dict[tuple, int], Tuple[str, ...]]:
    """The pairs where every other STRONG field is at its top level, with those fields set to
    UNKNOWN: a pool where most pairs are the same flat, so EM learns `field` from same-flat
    pairs rather than from look-alike flats (Splink trains each field under other blocks)."""
    others = tuple(g for g in STRONG if g != field)
    pool = defaultdict(int)
    for p, n in sorted(patterns.items()):
        if all(p[FIELDS.index(g)] == TOP[g] for g in others):
            pool[tuple(UNKNOWN if g in others else lvl for g, lvl in zip(FIELDS, p))] += n
    return dict(pool), others


def floor_level(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    fa, fb = _clean_floor(a.get("floor")), _clean_floor(b.get("floor"))
    if not fa or not fb:
        return UNKNOWN
    try:
        d = abs(int(fa) - int(fb))
    except ValueError:
        return "equal" if fa == fb else UNKNOWN
    return "equal" if d == 0 else ("off_by_one" if d == 1 else "further")


def _round_half_up(x: float) -> int:
    # JavaScript's Math.round for positive numbers; Python's round() rounds 54.5 to 54.
    return math.floor(x + 0.5)


def area_level(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    try:
        x, y = float(a.get("area_m2") or 0), float(b.get("area_m2") or 0)
    except (TypeError, ValueError):
        return UNKNOWN
    if x <= 0 or y <= 0:
        return UNKNOWN
    if _round_half_up(x) == _round_half_up(y):
        return "equal"
    return "close" if abs(x - y) <= SAME_FLAT_AREA_RATIO * max(x, y) else "far"


def _both_ways(compare, a: Dict[str, Any], b: Dict[str, Any]) -> str:
    # _compare_location reads the target's district and the candidate's street, so it is not
    # symmetric; a listing pair has no target, so both directions count.
    x, y = compare(a, b), compare(b, a)
    if "conflict" in (x, y):
        return "conflict"
    if "agree" in (x, y):
        return "agree"
    return UNKNOWN


def pair_levels(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        floor_level(a, b),
        area_level(a, b),
        _both_ways(_compare_layout, a, b),
        _both_ways(_compare_total_floors, a, b),
        _both_ways(_compare_location, a, b),
    )


def load_listings(conn: Any) -> List[Dict[str, Any]]:
    """Every properties row's comparison columns, in pages on the primary key (ADR 0001)."""
    cols = ", ".join(LISTING_COLUMNS)
    sql = f"SELECT {cols} FROM properties WHERE id > ? ORDER BY id LIMIT {PAGE}"
    last_id = ""
    listings: List[Dict[str, Any]] = []
    while True:
        rows = conn.execute(sql, (last_id,)).fetchall()
        if not rows:
            break
        for r in rows:
            listings.append({k: r[k] for k in LISTING_COLUMNS})
        if len(rows) < PAGE:
            break
        last_id = str(rows[-1]["id"])
    return listings


def block_key(row: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    district = str(row.get("district") or "").strip()
    try:
        area = float(row.get("area_m2") or 0)
    except (TypeError, ValueError):
        area = 0.0
    if not district or area <= 0:
        return None
    return (district, str(row.get("deal_type") or "sale"))


def iter_block_pairs(
    listings: List[Dict[str, Any]],
) -> Iterator[Tuple[Dict[str, Any], Dict[str, Any], Tuple[str, str, str, str, str]]]:
    """Every pair (a, b, levels) in the same block, a["id"] < b["id"], in a fixed order:

    blocks sorted by key, rows inside a block sorted by id.
    """
    blocks: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in listings:
        bk = block_key(r)
        if bk is not None:
            blocks[bk].append(r)

    sorted_blocks = sorted(blocks.items(), key=lambda item: item[0])
    for _bk, block_listings in sorted_blocks:
        block_listings.sort(key=lambda r: str(r.get("id") or ""))
        n = len(block_listings)
        for i in range(n):
            a = block_listings[i]
            for j in range(i + 1, n):
                b = block_listings[j]
                if a["id"] == b["id"]:
                    continue
                levels = pair_levels(a, b)
                yield (a, b, levels)


def count_pairs(listings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """{"pairs": N,

    "u_counts": {field: {level: n}},         # every block pair; UNKNOWN not counted
    "patterns": {pattern: n},                # every block pair pattern
    "pools": {"close_area": {pattern: n}}}   # area level "equal" or "close"
    Patterns are the 5-tuples of pair_levels; store them as tuples.
    """
    pairs = 0
    u_counts = {f: {lvl: 0 for lvl in LEVELS[f]} for f in FIELDS}
    patterns: Dict[Tuple[str, str, str, str, str], int] = defaultdict(int)
    close_area: Dict[Tuple[str, str, str, str, str], int] = defaultdict(int)

    for _a, _b, levels in iter_block_pairs(listings):
        pairs += 1
        patterns[levels] += 1
        for f, lvl in zip(FIELDS, levels):
            if lvl != UNKNOWN:
                u_counts[f][lvl] += 1
        ar = levels[1]
        if ar in ("equal", "close"):
            close_area[levels] += 1

    return {
        "pairs": pairs,
        "u_counts": u_counts,
        "patterns": dict(patterns),
        "pools": {
            "close_area": dict(close_area),
        },
    }


def group_pairs(listings: List[Dict[str, Any]]) -> List[Tuple[Tuple[str, ...], float]]:
    """Every pair inside one non-empty group_id (groups sorted by id, members by id), with weight

    2 / (s * (s - 1)) for a group of s members, so each group's pairs together weigh 1 and a
    60-member render group counts as much as a 2-member one. The area level is replaced by
    UNKNOWN (dedup only groups areas within SAME_FLAT_AREA_RATIO, so area agreement inside a
    group is not evidence).
    """
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in listings:
        gid = r.get("group_id")
        if gid is not None and str(gid).strip() != "":
            groups[str(gid).strip()].append(r)

    sorted_groups = sorted(groups.items(), key=lambda item: item[0])
    pairs: List[Tuple[Tuple[str, ...], float]] = []

    for _gid, members in sorted_groups:
        if len(members) < 2:
            continue
        members.sort(key=lambda r: str(r.get("id") or ""))
        s = len(members)
        w = 2.0 / (s * (s - 1))
        for i in range(s):
            a = members[i]
            for j in range(i + 1, s):
                b = members[j]
                if a["id"] == b["id"]:
                    continue
                levels = pair_levels(a, b)
                levels_no_area = (levels[0], UNKNOWN, levels[2], levels[3], levels[4])
                pairs.append((levels_no_area, w))

    return pairs


def u_probs(u_counts: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, float]]:
    u: Dict[str, Dict[str, float]] = {}
    for f in FIELDS:
        total = sum(u_counts[f].get(lvl, 0) for lvl in LEVELS[f]) + len(LEVELS[f])
        u[f] = {lvl: (u_counts[f].get(lvl, 0) + 1.0) / total for lvl in LEVELS[f]}
    return u


def seed_m(groups: List[Tuple[Tuple[str, ...], float]]) -> Dict[str, Dict[str, float]]:
    m0: Dict[str, Dict[str, float]] = {}
    for f in FIELDS:
        if f == "area":
            k = len(LEVELS["area"])
            m0["area"] = {lvl: 1.0 / k for lvl in LEVELS["area"]}
        else:
            idx = FIELDS.index(f)
            counts = {lvl: 0.0 for lvl in LEVELS[f]}
            known_weight = 0.0
            for pattern, w in groups:
                lvl = pattern[idx]
                if lvl in LEVELS[f]:
                    counts[lvl] += w
                    known_weight += w
            denom = known_weight + len(LEVELS[f])
            m0[f] = {lvl: (counts[lvl] + 1.0) / denom for lvl in LEVELS[f]}
    return m0


def em_session(
    pool: Dict[tuple, int],
    blocked: Any,
    m0: Dict[str, Dict[str, float]],
    u: Dict[str, Dict[str, float]],
    lam0: float,
) -> Dict[str, Any]:
    """MAP-EM with a +1 Dirichlet prior on every m level; u fixed.

    active = FIELDS without `blocked`. For a pattern p: K(p) = active fields whose level is not
    UNKNOWN; PM(p) = prod over K(p) of m[f][p_f]; PU(p) = prod over K(p) of u[f][p_f]
    (an empty product is 1).
    Repeat, iterating patterns in sorted order:
      E: r_p = lam*PM / (lam*PM + (1-lam)*PU)
      M: lam' = sum n_p r_p / sum n_p
         m'[f][l] = (sum over p with p_f == l of n_p r_p + 1)
                    / (sum over p with p_f in LEVELS[f] of n_p r_p + len(LEVELS[f]))  for f in active
      J = sum n_p * ln(lam'*PM' + (1-lam')*PU') + sum over active f, l of ln m'[f][l]
    Stop when J <= previous J (keep the previous parameters) or after EM_MAX_ITERATIONS.
    Returns {"m": m (blocked field copied from m0), "lambda": lam, "iterations": t, "objective": J,
             "pairs": sum n_p}.
    """
    blocked_fields = (blocked,) if isinstance(blocked, str) else tuple(blocked)
    active = [f for f in FIELDS if f not in blocked_fields]
    f_idx = {f: i for i, f in enumerate(FIELDS)}
    total_pairs = sum(pool.values())
    if total_pairs == 0:
        m_out = {f: dict(m0[f]) for f in FIELDS}
        return {"m": m_out, "lambda": lam0, "iterations": 0, "objective": 0.0, "pairs": 0}

    # Pre-parse pool patterns for fast iteration: (n_p, [(f, lvl), ...], pu)
    items = []
    for p, n_p in sorted(pool.items()):
        if n_p <= 0:
            continue
        k_p = [(f, p[f_idx[f]]) for f in active if p[f_idx[f]] != UNKNOWN]
        pu = 1.0
        for f, lvl in k_p:
            pu *= u[f][lvl]
        items.append((n_p, k_p, pu))

    lam = lam0
    m = {f: dict(m0[f]) for f in FIELDS}

    def calc_objective(cur_lam: float, cur_m: Dict[str, Dict[str, float]]) -> float:
        obj = 0.0
        for n_p, k_p, pu in items:
            pm = 1.0
            for f, lvl in k_p:
                pm *= cur_m[f][lvl]
            val = cur_lam * pm + (1.0 - cur_lam) * pu
            if val > 0:
                obj += n_p * math.log(val)
        for f in active:
            for lvl in LEVELS[f]:
                val = cur_m[f][lvl]
                if val > 0:
                    obj += math.log(val)
        return obj

    prev_J = -math.inf
    t = 0
    while t < EM_MAX_ITERATIONS:
        t += 1
        # E-step
        r_sum = 0.0
        m_counts = {f: {lvl: 0.0 for lvl in LEVELS[f]} for f in active}
        m_known_sum = {f: 0.0 for f in active}

        for n_p, k_p, pu in items:
            pm = 1.0
            for f, lvl in k_p:
                pm *= m[f][lvl]
            num = lam * pm
            denom = num + (1.0 - lam) * pu
            r_p = num / denom if denom > 0 else 0.0
            nr = n_p * r_p
            r_sum += nr
            for f, lvl in k_p:
                m_counts[f][lvl] += nr
                m_known_sum[f] += nr

        # M-step
        lam_next = r_sum / total_pairs
        m_next = {f: dict(m[f]) for f in FIELDS}
        for f in active:
            denom = m_known_sum[f] + len(LEVELS[f])
            for lvl in LEVELS[f]:
                m_next[f][lvl] = (m_counts[f][lvl] + 1.0) / denom

        J_next = calc_objective(lam_next, m_next)
        if J_next <= prev_J:
            break

        lam = lam_next
        m = m_next
        prev_J = J_next

    if lam <= 0 or lam >= 1:
        raise ValueError(f"EM collapsed in {','.join(blocked_fields)}")

    m_out = {f: dict(m[f]) for f in FIELDS}
    for f in blocked_fields:
        m_out[f] = dict(m0[f])
    return {
        "m": m_out,
        "lambda": lam,
        "iterations": t,
        "objective": prev_J,
        "pairs": total_pairs,
    }


def estimate(
    counts: Dict[str, Any], groups: List[Tuple[Tuple[str, ...], float]]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(weights, em_report).

    Learns m via four leave-one-out EM sessions on STRONG fields, averages location,
    computes lambda from the mean scaled matches, and performs the photo floor check.
    """
    u = u_probs(counts["u_counts"])
    m0 = seed_m(groups)
    sum_group_weights = sum(w for _, w in groups)

    s: Dict[str, Dict[str, Any]] = {}
    for f in STRONG:
        pool, others = leave_one_out_pool(counts["patterns"], f)
        pool_pairs = sum(pool.values())
        lam0 = (sum_group_weights + 1.0) / (pool_pairs + 2.0)
        s[f] = em_session(pool, others, m0, u, lam0)

    m: Dict[str, Dict[str, float]] = {}
    for f in STRONG:
        m[f] = dict(s[f]["m"][f])
    m["location"] = {
        lvl: sum(s[f]["m"]["location"][lvl] for f in STRONG) / len(STRONG)
        for lvl in LEVELS["location"]
    }

    matches: Dict[str, float] = {}
    for f in STRONG:
        others = tuple(g for g in STRONG if g != f)
        p_others = math.prod(m[g][TOP[g]] for g in others)
        matches[f] = (s[f]["lambda"] * s[f]["pairs"]) / p_others if p_others > 0 else 0.0

    bp = counts["pairs"]
    mean_matches = sum(matches.values()) / len(matches) if matches else 0.0
    lam = mean_matches / bp if bp > 0 else 0.0

    weights = {
        "levels_version": LEVELS_VERSION,
        "lambda": lam,
        "fields": {
            f: {
                lvl: {
                    "m": m[f][lvl],
                    "u": u[f][lvl],
                    "bits": math.log2(m[f][lvl] / u[f][lvl]),
                }
                for lvl in LEVELS[f]
            }
            for f in FIELDS
        },
    }

    photo_floor = photo_floor_check(groups, m["floor"]["equal"])

    em_report = {
        "sessions": {
            f: {
                "pool": s[f]["pairs"],
                "lambda": s[f]["lambda"],
                "iterations": s[f]["iterations"],
                "converged": s[f]["iterations"] < EM_MAX_ITERATIONS,
                "objective": s[f]["objective"],
                "matches": matches[f],
            }
            for f in STRONG
        },
        "group_pairs": len(groups),
        "block_pairs": bp,
        "photo_floor": photo_floor,
    }

    return weights, em_report


def log_odds(weights: Dict[str, Any], levels: Tuple[str, ...]) -> float:
    lam = weights["lambda"]
    if lam <= 0:
        return -math.inf
    if lam >= 1:
        return math.inf
    bits = sum(
        weights["fields"][f][lvl]["bits"] for f, lvl in zip(FIELDS, levels) if lvl != UNKNOWN
    )
    return math.log(lam / (1 - lam)) + bits * math.log(2)


def posterior(weights: Dict[str, Any], a: Dict[str, Any], b: Dict[str, Any]) -> float:
    z = log_odds(weights, pair_levels(a, b))
    if z <= -700:
        return 0.0
    if z >= 700:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def challenger_says_same(weights: Dict[str, Any], a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return posterior(weights, a, b) > POSSIBLE


def sign_test_p(wins: int, n: int) -> float:
    """One-sided exact sign test: P(X >= wins) for X ~ Binomial(n, 1/2). 1.0 when n == 0."""
    return sum(math.comb(n, k) for k in range(wins, n + 1)) / (2**n) if n else 1.0


def _reply_sign(
    records_with_disagreement: List[Tuple[Dict[str, Any], bool, bool, bool]],
) -> Tuple[int, int, int]:
    """(replies won, replies lost, ties): a reply wins when the challenger is right on more of
    its disagreeing pairs than the champion, loses in the opposite case. Candidates under one
    reply share its target, so a reply, not a pair, is one independent answer."""
    by_reply: Dict[Any, List[int]] = defaultdict(
        lambda: [0, 0]
    )  # target_key -> [challenger right, champion right]
    for r, champion, challenger, truth in records_with_disagreement:
        by_reply[r["target_key"]][0 if challenger == truth else 1] += 1
    won = sum(1 for w, lst in by_reply.values() if w > lst)
    lost = sum(1 for w, lst in by_reply.values() if lst > w)
    return won, lost, len(by_reply) - won - lost


def gate(
    weights: Dict[str, Any],
    records: List[Dict[str, Any]],
    vectors: List[Dict[str, Any]],
    photo_check: Dict[str, Any],
) -> Dict[str, Any]:
    """Exam = records with label 0 or 1 and snapshot != "missing".

    For each exam record: champion = _candidate_matches_target(target, candidate);
    challenger = challenger_says_same(weights, target, candidate); truth = label == 1.
    For a set S of exam records:
      disagreements = records in S where champion != challenger
      wins   = disagreements where challenger == truth
      losses = disagreements where champion == truth
      replies = disagreeing replies (grouped by target_key)
      replies_won / lost / tied via _reply_sign
      p_value = sign_test_p(replies_won, replies_won + replies_lost)
      fp_challenger = records in S with label 0 and challenger True
      fp_champion   = records in S with label 0 and champion True
    Checks:
      A. on all exam records: p_value < 0.05 (reply-level sign test)
      B. on all exam records: fp_challenger <= fp_champion
      C. on records with target_kind == "post": replies_won >= replies_lost and fp_challenger <= fp_champion
      D. every vector with "gate": true gives challenger_says_same(*v["args"]) == v["expected"]
      E. photo_floor check ok
    passed = A and B and C and D and E.
    Returns {"exam": {"pairs", "unsure", "missing"},
             "all": {"disagreements", "wins", "losses", "replies", "replies_won", "replies_lost", "replies_tied", "p_value", "fp_challenger", "fp_champion"},
             "post": {same keys},
             "vectors": {"checked": n, "failed": [names]},
             "photo_floor": photo_check,
             "passed": bool,
             "disagreement_pairs": [{"pair_id", "label", "champion", "challenger", "posterior"}]}
    sorted by pair_id. No field values, so no PII.
    """
    unsure = 0
    missing = 0
    exam_records = []

    for r in records:
        lbl = r.get("label")
        snap = r.get("snapshot")
        if lbl == -1:
            unsure += 1
        elif snap == "missing":
            missing += 1
        elif lbl in (0, 1):
            exam_records.append(r)

    exam_stats = {"pairs": len(exam_records), "unsure": unsure, "missing": missing}

    evaluated = []
    disagreement_pairs = []

    for r in exam_records:
        pid = r["pair_id"]
        target = r.get("target") or {}
        cand = r.get("candidate") or {}
        t = {**target, "pair_id": pid}
        c = {**cand, "pair_id": pid}

        champ = bool(_candidate_matches_target(t, c))
        chall = bool(challenger_says_same(weights, t, c))
        try:
            post = posterior(weights, t, c)
        except Exception:
            post = 0.5

        truth = r["label"] == 1
        disagrees = champ != chall

        if disagrees:
            disagreement_pairs.append(
                {
                    "pair_id": pid,
                    "label": r["label"],
                    "champion": champ,
                    "challenger": chall,
                    "posterior": post,
                }
            )

        evaluated.append(
            {
                "record": r,
                "champion": champ,
                "challenger": chall,
                "truth": truth,
                "disagrees": disagrees,
            }
        )

    disagreement_pairs.sort(key=lambda d: str(d["pair_id"]))

    def calc_stratum(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        disagreements = sum(1 for x in items if x["disagrees"])
        wins = sum(1 for x in items if x["disagrees"] and x["challenger"] == x["truth"])
        losses = sum(1 for x in items if x["disagrees"] and x["champion"] == x["truth"])
        fp_chall = sum(1 for x in items if not x["truth"] and x["challenger"])
        fp_champ = sum(1 for x in items if not x["truth"] and x["champion"])

        disagreeing = [
            (x["record"], x["champion"], x["challenger"], x["truth"])
            for x in items
            if x["disagrees"]
        ]
        rep_won, rep_lost, rep_tied = _reply_sign(disagreeing)
        rep_total = rep_won + rep_lost + rep_tied
        p_val = sign_test_p(rep_won, rep_won + rep_lost)

        return {
            "disagreements": disagreements,
            "wins": wins,
            "losses": losses,
            "replies": rep_total,
            "replies_won": rep_won,
            "replies_lost": rep_lost,
            "replies_tied": rep_tied,
            "p_value": p_val,
            "fp_challenger": fp_chall,
            "fp_champion": fp_champ,
        }

    all_stats = calc_stratum(evaluated)
    post_items = [x for x in evaluated if x["record"].get("target_kind") == "post"]
    post_stats = calc_stratum(post_items)

    check_a = all_stats["p_value"] < 0.05
    check_b = all_stats["fp_challenger"] <= all_stats["fp_champion"]
    check_c = (
        post_stats["replies_won"] >= post_stats["replies_lost"]
        and post_stats["fp_challenger"] <= post_stats["fp_champion"]
    )

    checked_vectors = 0
    failed_vectors = []
    for v in vectors:
        if v.get("gate") is True:
            checked_vectors += 1
            if challenger_says_same(weights, *v["args"]) != v["expected"]:
                failed_vectors.append(v["name"])

    check_d = len(failed_vectors) == 0
    check_e = bool(photo_check.get("ok"))
    passed = bool(check_a and check_b and check_c and check_d and check_e)

    return {
        "exam": exam_stats,
        "all": all_stats,
        "post": post_stats,
        "vectors": {"checked": checked_vectors, "failed": failed_vectors},
        "photo_floor": photo_check,
        "passed": passed,
        "disagreement_pairs": disagreement_pairs,
    }
