"""Measure free text parser against portal listings."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from core.database import D1Client, D1Connection  # noqa: E402
from core.xray import (  # noqa: E402
    _expand_district_aliases,
    _extract_fb_attributes,
    _extract_micro_district,
    _micro_of,
)

COLUMNS = (
    "id",
    "url",
    "description",
    "deal_type",
    "district",
    "subdistrict",
    "street",
    "floor",
    "total_floors",
    "rooms",
    "bedrooms",
    "area_m2",
    "price_usd",
    "metro_station_id",
)
PAGE = 1000
FIELDS = (
    "area_m2",
    "floor",
    "total_floors",
    "rooms",
    "bedrooms",
    "price_usd",
    "district",
    "micro",
)
MAX_EXAMPLES = 30


def load_rows(conn: Any) -> list[dict]:
    cols = ", ".join(COLUMNS)
    sql = (
        f"SELECT {cols} FROM properties "
        "WHERE id > ? AND description IS NOT NULL AND description != '' "
        f"ORDER BY id LIMIT {PAGE}"
    )
    last_id = ""
    listings: list[dict] = []
    while True:
        rows = conn.execute(sql, (last_id,)).fetchall()
        if not rows:
            break
        for r in rows:
            listings.append({k: r[k] for k in COLUMNS})
        if len(rows) < PAGE:
            break
        last_id = str(rows[-1]["id"])
    return listings


def truth_of(row: dict, field: str) -> Any:
    if field == "area_m2":
        try:
            val = float(row.get("area_m2") or 0)
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None

    if field == "floor":
        raw = row.get("floor")
        if raw is None:
            return None
        raw_str = str(raw).strip()
        if not raw_str:
            return None
        part = raw_str.split("/")[0].strip()
        try:
            return str(int(part))
        except (ValueError, TypeError):
            return None

    if field in ("total_floors", "rooms", "bedrooms"):
        try:
            val = int(row.get(field) or 0)
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None

    if field == "price_usd":
        deal_type = row.get("deal_type")
        if deal_type != "sale":
            return None
        try:
            val = float(row.get("price_usd") or 0)
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None

    if field == "district":
        d = row.get("district")
        if d is None:
            return None
        d_str = str(d).strip()
        return d_str if d_str else None

    if field == "micro":
        return _micro_of(row)

    return None


def pred_of(attrs: dict, field: str) -> Any:
    if field == "area_m2":
        try:
            val = float(attrs.get("area_m2") or 0)
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None

    if field == "floor":
        raw = attrs.get("floor")
        if raw is None:
            return None
        raw_str = str(raw).strip()
        if not raw_str:
            return None
        part = raw_str.split("/")[0].strip()
        try:
            return str(int(part))
        except (ValueError, TypeError):
            return None

    if field in ("total_floors", "rooms", "bedrooms"):
        try:
            val = int(attrs.get(field) or 0)
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None

    if field == "price_usd":
        try:
            val = float(attrs.get("price_usd") or 0)
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None

    if field == "district":
        d = attrs.get("district")
        if d is None:
            return None
        d_str = str(d).strip()
        return d_str if d_str else None

    if field == "micro":
        try:
            val = int(attrs.get("micro") or 0)
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None

    return None


def same(field: str, truth: Any, pred: Any) -> bool:
    if truth is None or pred is None:
        return False
    if field == "area_m2":
        return abs(float(truth) - float(pred)) <= 1.0
    if field == "price_usd":
        return abs(float(truth) - float(pred)) <= 0.01 * float(truth)
    if field == "district":
        t_aliases = _expand_district_aliases(str(truth))
        p_aliases = _expand_district_aliases(str(pred))
        return (str(pred) in t_aliases) or (str(truth) in p_aliases)
    return truth == pred


def evaluate(rows: list[dict]) -> dict:
    stats: dict[str, dict] = {
        field: {
            "truth_n": 0,
            "pred_n": 0,
            "both_n": 0,
            "correct_n": 0,
            "coverage": 0.0,
            "precision": 0.0,
            "examples": [],
        }
        for field in FIELDS
    }

    deal_types: list[str] = []
    with_metro_id = 0
    mentions_metro = 0
    metro_id_and_mention = 0

    for r in rows:
        dt = r.get("deal_type")
        if dt:
            deal_types.append(str(dt))

        desc = str(r.get("description") or "")
        has_metro_id = r.get("metro_station_id") is not None
        has_mention = "მეტრო" in desc

        if has_metro_id:
            with_metro_id += 1
        if has_mention:
            mentions_metro += 1
        if has_metro_id and has_mention:
            metro_id_and_mention += 1

        attrs = {**_extract_fb_attributes(desc), "micro": _extract_micro_district(desc)}

        for field in FIELDS:
            t = truth_of(r, field)
            p = pred_of(attrs, field)

            if t is not None:
                stats[field]["truth_n"] += 1
            if p is not None:
                stats[field]["pred_n"] += 1
            if t is not None and p is not None:
                stats[field]["both_n"] += 1
                if same(field, t, p):
                    stats[field]["correct_n"] += 1
                else:
                    if len(stats[field]["examples"]) < MAX_EXAMPLES:
                        stats[field]["examples"].append(
                            {
                                "id": r.get("id"),
                                "url": r.get("url"),
                                "truth": t,
                                "pred": p,
                                "snippet": desc[:200],
                            }
                        )

    for field in FIELDS:
        tn = stats[field]["truth_n"]
        bn = stats[field]["both_n"]
        cn = stats[field]["correct_n"]
        stats[field]["coverage"] = round(bn / tn, 4) if tn > 0 else 0.0
        stats[field]["precision"] = round(cn / bn, 4) if bn > 0 else 0.0

    report: dict[str, Any] = {
        "rows": len(rows),
        "by_deal_type": dict(Counter(deal_types)),
        "with_metro_id": with_metro_id,
        "mentions_metro": mentions_metro,
        "metro_id_and_mention": metro_id_and_mention,
        "fields": stats,
    }
    report.update(stats)
    return report


def to_markdown(report: dict) -> str:
    lines = [
        "| field | truth_n | coverage | precision |",
        "| --- | --- | --- | --- |",
    ]
    fields_dict = report.get("fields", report)
    for field in FIELDS:
        f_data = fields_dict.get(field, {})
        truth_n = f_data.get("truth_n", 0)
        cov = f_data.get("coverage", 0.0)
        prec = f_data.get("precision", 0.0)
        lines.append(f"| {field} | {truth_n} | {cov:.1%} | {prec:.1%} |")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None, conn: Optional[Any] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure free text parser against portal listings."
    )
    parser.add_argument(
        "--out-dir",
        default="out",
        help="Directory to write output files (default: out)",
    )
    args = parser.parse_args(argv)

    if conn is None:
        if not settings.CLOUDFLARE_PROXY_URL or not settings.CLOUDFLARE_SYNC_KEY:
            print("CLOUDFLARE_PROXY_URL and CLOUDFLARE_SYNC_KEY must be set", file=sys.stderr)
            return 2
        conn = D1Connection(
            D1Client(settings.CLOUDFLARE_PROXY_URL, settings.CLOUDFLARE_SYNC_KEY, timeout=60)
        )

    rows = load_rows(conn)
    report = evaluate(rows)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "parser_eval.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md = to_markdown(report)
    print(md)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as f:
                f.write(md + "\n")
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
