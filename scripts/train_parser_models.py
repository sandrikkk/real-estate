"""Train text classification models for X-Ray parser."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from core import text_model  # noqa: E402
from core.database import D1Client, D1Connection  # noqa: E402

KINDS = ("deal_type",)  # PR 3 adds "number_role", PR 4 adds "metro"
TARGET = {"deal_type": 0.99}  # the Facebook check allows 1 wrong in ~74, i.e. about 98.6%
MIN_TEXT = 30  # characters; shorter descriptions are skipped
PAGE = 1000


def load_deal_rows(conn: Any) -> List[Dict[str, Any]]:
    """Load listings with deal_type in ('sale', 'rent') in pages."""
    sql = (
        "SELECT id, group_id, description, deal_type FROM properties "
        f"WHERE id > ? ORDER BY id LIMIT {PAGE}"
    )
    last_id = ""
    rows_out: List[Dict[str, Any]] = []
    while True:
        rows = conn.execute(sql, (last_id,)).fetchall()
        if not rows:
            break
        for r in rows:
            deal_type = r["deal_type"]
            desc = str(r["description"] or "").strip()
            if deal_type in ("sale", "rent") and len(desc) >= MIN_TEXT:
                rows_out.append(
                    {
                        "id": str(r["id"]),
                        "group_id": r["group_id"],
                        "description": desc,
                        "deal_type": deal_type,
                    }
                )
        if len(rows) < PAGE:
            break
        last_id = str(rows[-1]["id"])
    return rows_out


def run_deal_type(rows: List[Dict[str, Any]], fb_posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Train and evaluate the deal_type classification model."""
    train_rows: List[Dict[str, Any]] = []
    test_rows: List[Dict[str, Any]] = []
    for r in rows:
        key = str(r["group_id"] or r["id"])
        if text_model.test_split(key):
            test_rows.append(r)
        else:
            train_rows.append(r)

    train_texts = [r["description"] for r in train_rows]
    train_labels = [r["deal_type"] for r in train_rows]

    model = text_model.train(train_texts, train_labels, ["sale", "rent"], "deal_type")

    # Evaluate on test
    test_probs: List[float] = []
    test_correct: List[bool] = []
    test_errors: List[Dict[str, Any]] = []
    for r in test_rows:
        probs = text_model.predict(model, r["description"])
        pred_label = max(probs, key=probs.get)
        prob = probs[pred_label]
        is_corr = pred_label == r["deal_type"]
        test_probs.append(prob)
        test_correct.append(is_corr)
        if not is_corr:
            test_errors.append(
                {
                    "id": r["id"],
                    "label": r["deal_type"],
                    "pred": pred_label,
                    "prob": round(prob, 4),
                    "snippet": r["description"][:200],
                }
            )

    accuracy_all = sum(test_correct) / len(test_rows) if test_rows else 0.0
    threshold = text_model.pick_threshold(test_probs, test_correct, TARGET["deal_type"])

    if threshold is not None:
        confident_indices = [i for i, p in enumerate(test_probs) if p >= threshold]
        coverage = len(confident_indices) / len(test_rows) if test_rows else 0.0
        accuracy_confident = (
            sum(1 for i in confident_indices if test_correct[i]) / len(confident_indices)
            if confident_indices
            else 0.0
        )
    else:
        coverage = 0.0
        accuracy_confident = 0.0

    # Facebook posts check
    fb_checked = 0
    fb_confident = 0
    fb_wrong = 0
    fb_errors: List[Dict[str, Any]] = []
    for post in fb_posts:
        exp = post.get("expected") or {}
        exp_deal = exp.get("deal_type")
        if exp_deal in ("sale", "rent"):
            fb_checked += 1
            text = post.get("text") or ""
            probs = text_model.predict(model, text)
            pred_deal = max(probs, key=probs.get)
            prob = probs[pred_deal]
            if pred_deal != exp_deal:
                fb_errors.append(
                    {
                        "id": post.get("id"),
                        "expected": exp_deal,
                        "pred": pred_deal,
                        "prob": round(prob, 4),
                        "snippet": text[:200],
                    }
                )
            if threshold is not None and prob >= threshold:
                fb_confident += 1
                if pred_deal != exp_deal:
                    fb_wrong += 1

    passed = bool(threshold is not None and fb_wrong <= 1)
    model["threshold"] = threshold

    report: Dict[str, Any] = {
        "kind": "deal_type",
        "train_n": len(train_rows),
        "test_n": len(test_rows),
        "by_class": dict(Counter(train_labels + [r["deal_type"] for r in test_rows])),
        "accuracy_all": round(accuracy_all, 4),
        "threshold": threshold,
        "coverage": round(coverage, 4),
        "accuracy_confident": round(accuracy_confident, 4),
        "fb_checked": fb_checked,
        "fb_confident": fb_confident,
        "fb_wrong": fb_wrong,
        "passed": passed,
        "errors": sorted(test_errors, key=lambda e: -e["prob"])[:20],
        "fb_errors": sorted(fb_errors, key=lambda e: -e["prob"])[:20],
    }

    return {"model": model, "passed": passed, "report": report}


def report_lines(report: Dict[str, Any]) -> List[str]:
    """The class counts and the wrong test answers, one line each, for the job log."""
    counts = " ".join(f"{k}={v}" for k, v in sorted(report.get("by_class", {}).items()))
    lines = [f"by_class: {counts}"]
    for e in report.get("errors", []):
        snippet = " ".join(str(e.get("snippet") or "").split())
        lines.append(f"{e['label']} -> {e['pred']} {e['prob']} {e['id']} | {snippet}")
    return lines


def main(argv: Optional[List[str]] = None, conn: Optional[Any] = None) -> int:
    parser = argparse.ArgumentParser(description="Train text parser models.")
    parser.add_argument(
        "--kind",
        choices=KINDS,
        required=True,
        help="Model kind to train",
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

    fb_path = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "xray_fb_posts.json"
    with open(fb_path, "r", encoding="utf-8") as f:
        fb_posts = json.load(f)

    if args.kind == "deal_type":
        rows = load_deal_rows(conn)
        result = run_deal_type(rows, fb_posts)
    else:
        print(f"Unknown kind: {args.kind}", file=sys.stderr)
        return 1

    model = result["model"]
    report = result["report"]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model_file = out_dir / f"model_{args.kind}.json"
    report_file = out_dir / f"report_{args.kind}.json"

    with open(model_file, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(
        f"[{args.kind}] train: {report['train_n']} | test: {report['test_n']} | "
        f"acc: {report['accuracy_all']:.1%} | threshold: {report['threshold']} | "
        f"cov: {report['coverage']:.1%} | fb_wrong: {report['fb_wrong']} | "
        f"passed: {report['passed']}"
    )
    for line in report_lines(report):
        print(line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
