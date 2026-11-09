"""The X-Ray golden pairs (ADR 0002): one record per admin answer under an X-Ray candidate.

Each record holds the target and the candidate as they were shown, the answer
(1 ✅, 0 ❌, -1 🤷), and where the pair came from. The repository is public, so a record
carries no phone number, author id or chat id: only whether the two sides share them.
Organic answers cover only pairs the bot chose to show; `label_origin` tells them apart from
the weekly review's answers.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from core.photo_match import _caption_flat
from core.xray import snapshot_fields

PORTAL_PREFIXES = ("myhome_", "ss_ge_")
PII_FIELDS = ("phone9", "phone_number", "author_id", "phone_9", "formatted_phone", "masked_phone")
REVIEW_KINDS = ("review_uncertain", "review_random")
CHUNK = 50  # ids per IN (...) query (ADR 0001)


def _chunked(items: List[Any], n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _phone9(v: Any) -> Optional[str]:
    if not v:
        return None
    digits = re.sub(r"\D", "", str(v))
    return digits[-9:] if len(digits) >= 9 else None


def build_golden_pairs(conn: Any, admin_chat_id: str) -> Tuple[List[Dict[str, Any]], int]:
    """(records sorted by pair_id, number of answers whose reply no longer exists)."""
    # 1. Read the answers
    raw_fb = conn.execute(
        "SELECT job_id, candidate_id, target_id, label, created_at "
        "FROM xray_feedback WHERE chat_id = ?",
        (str(admin_chat_id),),
    ).fetchall()
    feedback_rows = [{k: row[k] for k in row.keys()} for row in raw_fb]

    # 2. Load the replies
    distinct_job_ids = sorted({r["job_id"] for r in feedback_rows if r.get("job_id")})

    results_by_id: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunked(distinct_job_ids, CHUNK):
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, kind, target, candidates, created_at FROM xray_results WHERE id IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            results_by_id[d["id"]] = d

    missing_job_ids = [jid for jid in distinct_job_ids if jid not in results_by_id]
    jobs_by_id: Dict[str, Dict[str, Any]] = {}
    owner_target_ids: List[str] = []
    for chunk in _chunked(missing_job_ids, CHUNK):
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, kind, target_id, result, created_at, done_at FROM xray_jobs WHERE id IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            jobs_by_id[d["id"]] = d
            if d.get("kind") == "owner" and d.get("target_id"):
                owner_target_ids.append(d["target_id"])

    targets_by_id: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunked(sorted(set(owner_target_ids)), CHUNK):
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, target FROM xray_targets WHERE id IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            targets_by_id[d["id"]] = d

    # 3. Build one raw record per answer
    skipped = 0
    raw_records: List[Dict[str, Any]] = []

    for fb in feedback_rows:
        job_id = fb["job_id"]
        candidate_id = fb["candidate_id"]

        if job_id in results_by_id:
            row = results_by_id[job_id]
            target = json.loads(row.get("target") or "{}")
            review = target.pop("_review", None)
            cands = json.loads(row.get("candidates") or "[]")
            kind = row.get("kind")
            reply_kind = "review" if kind in REVIEW_KINDS else kind
            label_origin = kind if kind in REVIEW_KINDS else "organic"
            shown_at = row.get("created_at")
        elif job_id in jobs_by_id:
            row = jobs_by_id[job_id]
            result_obj = (
                json.loads(row.get("result") or "{}")
                if isinstance(row.get("result"), str)
                else (row.get("result") or {})
            )
            cands = result_obj.get("candidates") or []
            kind = row.get("kind")
            reply_kind = kind
            label_origin = "organic"
            review = None
            shown_at = row.get("done_at") or row.get("created_at")
            if kind == "owner":
                target_row = targets_by_id.get(row.get("target_id"))
                target = (
                    json.loads(target_row["target"])
                    if (target_row and target_row.get("target"))
                    else {}
                )
            elif kind == "photo":
                flat = _caption_flat(conn, row.get("target_id"))
                target = {"photo_batch": row.get("target_id"), **flat}
                if "phone9" not in target and target.get("phone_9"):
                    target["phone9"] = str(target["phone_9"])
            else:
                target = {}
        else:
            skipped += 1
            continue

        # 4. Find the candidate
        cand = next((dict(c) for c in cands if c.get("id") == candidate_id), {"id": candidate_id})
        photo_matches = cand.pop("photo_matches", None)
        snapshot = "at_label" if set(cand) - {"id", "url"} else None

        raw_records.append(
            {
                "fb": fb,
                "target": target,
                "review": review,
                "cand": cand,
                "candidate_id": candidate_id,
                "reply_kind": reply_kind,
                "label_origin": label_origin,
                "shown_at": shown_at,
                "snapshot": snapshot,
                "photo_matches": photo_matches,
            }
        )

    # 5. Backfill for records with snapshot is None
    need_backfill = [r for r in raw_records if r["snapshot"] is None]
    if need_backfill:
        cand_ids = sorted({r["candidate_id"] for r in need_backfill})
        prop_rows: Dict[str, Dict[str, Any]] = {}
        for chunk in _chunked(cand_ids, CHUNK):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT * FROM properties WHERE id IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            for r in rows:
                d = {k: r[k] for k in r.keys()}
                prop_rows[d["id"]] = d

        still_missing: List[str] = []
        for r in need_backfill:
            cid = r["candidate_id"]
            if cid in prop_rows:
                r["cand"] = {
                    **snapshot_fields(dict(prop_rows[cid])),
                    **{k: v for k, v in r["cand"].items() if v is not None},
                }
                r["snapshot"] = "backfilled"
            else:
                still_missing.append(cid)

        if still_missing:
            live_rows: Dict[str, Dict[str, Any]] = {}
            for chunk in _chunked(sorted(set(still_missing)), CHUNK):
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT * FROM xray_live_listings WHERE id IN ({placeholders})",
                    tuple(chunk),
                ).fetchall()
                for r in rows:
                    d = {k: r[k] for k in r.keys()}
                    live_rows[d["id"]] = d

            for r in need_backfill:
                if r["snapshot"] is None:
                    cid = r["candidate_id"]
                    if cid in live_rows:
                        r["cand"] = {
                            **snapshot_fields(dict(live_rows[cid])),
                            **{k: v for k, v in r["cand"].items() if v is not None},
                        }
                        r["snapshot"] = "backfilled"
                    else:
                        r["snapshot"] = "missing"

    # 6. Relations & final record build
    records: List[Dict[str, Any]] = []
    for item in raw_records:
        fb = item["fb"]
        target = item["target"]
        cand = item["cand"]
        candidate_id = item["candidate_id"]
        job_id = fb["job_id"]

        # 6. Relations
        pt = _phone9(target.get("phone9") or target.get("phone_number"))
        pc = _phone9(cand.get("phone9") or cand.get("phone_number"))
        if pt is not None and pc is not None:
            same_phone = "agree" if pt == pc else "conflict"
        else:
            same_phone = "unknown"

        at = target.get("author_id")
        ac = cand.get("author_id")
        st = target.get("source")
        sc = cand.get("source")
        if at and ac and st and sc and st == sc:
            same_author = "agree" if str(at) == str(ac) else "conflict"
        else:
            same_author = "unknown"

        # 7. Strip PII
        target_clean = {k: v for k, v in target.items() if k not in PII_FIELDS}
        cand_clean = {k: v for k, v in cand.items() if k not in PII_FIELDS}

        # 8. The listing pair
        target_listing = None
        for k in ("listing_id", "id"):
            v = target.get(k)
            if v is not None and any(str(v).startswith(p) for p in PORTAL_PREFIXES):
                target_listing = str(v)
                break

        target_kind = "listing" if target_listing else "post"
        if target_listing and target_listing != candidate_id:
            listing_pair_key = "__".join(sorted([target_listing, candidate_id]))
        else:
            listing_pair_key = None

        # 9. The record
        rec = {
            "pair_id": f"{job_id}__{candidate_id}",
            "target_key": job_id,
            "label": int(fb["label"]),
            "label_origin": item["label_origin"],
            "reply_kind": item["reply_kind"],
            "target_kind": target_kind,
            "listing_pair_key": listing_pair_key,
            "shown_at": item["shown_at"],
            "labelled_at": fb["created_at"],
            "snapshot": item["snapshot"],
            "target": target_clean,
            "candidate": cand_clean,
            "relations": {"same_phone": same_phone, "same_author": same_author},
            "evidence": {"photo_matches": item["photo_matches"]},
            "review": item["review"],
        }
        records.append(rec)

    # 10. Order
    return sorted(records, key=lambda r: r["pair_id"]), skipped


def to_jsonl(records: List[Dict[str, Any]]) -> str:
    """One compact JSON object per line, keys sorted; byte-identical for identical input."""
    return "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for r in records
    )


def summarize(records: List[Dict[str, Any]], skipped: int) -> Dict[str, Any]:
    """Counts by label, label_origin, reply_kind, snapshot and target_kind, plus skipped."""
    label_counts: Dict[str, int] = {}
    origin_counts: Dict[str, int] = {}
    reply_counts: Dict[str, int] = {}
    snapshot_counts: Dict[str, int] = {}
    target_kind_counts: Dict[str, int] = {}

    for r in records:
        lbl = str(r["label"])
        label_counts[lbl] = label_counts.get(lbl, 0) + 1

        orig = str(r["label_origin"])
        origin_counts[orig] = origin_counts.get(orig, 0) + 1

        rk = str(r["reply_kind"])
        reply_counts[rk] = reply_counts.get(rk, 0) + 1

        sn = str(r["snapshot"])
        snapshot_counts[sn] = snapshot_counts.get(sn, 0) + 1

        tk = str(r["target_kind"])
        target_kind_counts[tk] = target_kind_counts.get(tk, 0) + 1

    return {
        "records": len(records),
        "skipped": skipped,
        "label": {k: label_counts[k] for k in sorted(label_counts)},
        "label_origin": {k: origin_counts[k] for k in sorted(origin_counts)},
        "reply_kind": {k: reply_counts[k] for k in sorted(reply_counts)},
        "snapshot": {k: snapshot_counts[k] for k in sorted(snapshot_counts)},
        "target_kind": {k: target_kind_counts[k] for k in sorted(target_kind_counts)},
    }
