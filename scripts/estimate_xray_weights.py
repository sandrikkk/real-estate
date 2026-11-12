"""Weekly X-Ray weights (ADR 0002): estimate, test on the admin's answers, store, report.

Fellegi-Sunter weights learned from the whole properties table in two EM sessions,
tested against the admin's golden answers via an exact sign test gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from core.database import (  # noqa: E402
    D1Client,
    D1Connection,
    XRAY_CALIBRATION_DDL,
    XRAY_WEIGHTS_DDL,
)
from core.golden_pairs import build_golden_pairs, summarize, to_jsonl  # noqa: E402
from core.xray_review import (  # noqa: E402
    collage,
    load_photo_urls,
    photo_downloader,
    pick_review_pairs,
    seen_review_items,
    send_review,
)
from core.xray_weights import (  # noqa: E402
    calibration,
    count_pairs,
    estimate,
    gate,
    group_pairs,
    load_listings,
    tier_report,
    valid_weights,
)


def telegram_sender(token: str):
    """POST JSON to Telegram sendMessage with a 20s timeout; suppresses exceptions to avoid logging token."""

    def send(method: str, payload: Dict[str, Any]) -> Optional[dict]:
        if not token:
            return None
        url = f"https://api.telegram.org/bot{token}/{method}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    return send


def telegram_photo_sender(token: str):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"

    def send_photo(chat_id, jpeg: bytes, caption: str, reply_markup: dict) -> Optional[dict]:
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (
            ("chat_id", str(chat_id)),
            ("caption", caption),
            ("parse_mode", "HTML"),
            ("reply_markup", json.dumps(reply_markup, ensure_ascii=False)),
        ):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode(
                    "utf-8"
                )
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="pair.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n".encode("utf-8")
            + jpeg
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        req = urllib.request.Request(
            url,
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except Exception:
            return None  # print nothing: the URL holds the token

    return send_photo


def photo_answers(records: List[Dict[str, Any]]) -> Tuple[int, int]:
    """(same flat, answered) over the admin's ✅/❌ on photo-search lines (ADR 0005)."""
    answered = [
        r["label"] for r in records if r.get("reply_kind") == "photo" and r["label"] in (0, 1)
    ]
    return sum(answered), len(answered)


def summary_text(
    version: str,
    summary: Dict[str, Any],
    report: Dict[str, Any],
    sent: Optional[int] = None,
    photo: Optional[Tuple[int, int]] = None,
) -> str:
    lbl_counts = summary.get("label") or {}
    n1 = lbl_counts.get("1", 0)
    n0 = lbl_counts.get("0", 0)
    nu = lbl_counts.get("-1", 0)

    lines = [
        f"📊 <b>X-Ray: კვირის შეფასება</b> ({version})",
        "",
        f"შენი პასუხები: ✅ {n1} · ❌ {n0} · 🤷 {nu}",
    ]

    champ_id = report.get("champion")
    is_weights = bool(champ_id and champ_id != "rule")
    champ_label = f"ბოტის ახლანდელი წონები ({champ_id})" if is_weights else "ძველი წესი"

    disagreements = report.get("all", {}).get("disagreements", 0)
    if disagreements == 0:
        lines.append(f"{champ_label} და ახალი წესი შენს ყველა პასუხზე ერთნაირად ფიქრობს.")
    else:
        replies = report.get("all", {}).get("replies", 0)
        replies_won = report.get("all", {}).get("replies_won", 0)
        replies_lost = report.get("all", {}).get("replies_lost", 0)
        lines.append(
            f"{champ_label} და ახალი წესი {disagreements} წყვილზე არ ეთანხმება ({replies} დამოუკიდებელ ბინაში): ახალი მართალია {replies_won}-ში, {champ_label} {replies_lost}-ში."
        )

    if not report.get("photo_floor", {}).get("ok", True):
        lines.append(
            "⚠️ ახალი წონები ფოტო-ჯგუფებს არ ემთხვევა (სართული), ამიტომ ეს კვირა არ ითვლება."
        )

    if report.get("passed"):
        if is_weights:
            lines.append(
                f"✅ ახალმა წესმა გამოცდა გაიარა ({champ_id}-ზე უკეთესია). ბოტში ჯერ ისევ {champ_id}-ია: ჩართვა ცალკე ნაბიჯია."
            )
        else:
            lines.append(
                "✅ ახალმა წესმა გამოცდა გაიარა. ბოტში ჯერ ისევ ძველი წესია: ჩართვა ცალკე ნაბიჯია."
            )
    else:
        if is_weights:
            lines.append(
                f"⏳ ახალი წესი ჯერ ვერ ამტკიცებს, რომ {champ_id}-ზე უკეთესია. ბოტი {champ_id}-ს იყენებს."
            )
        else:
            lines.append(
                "⏳ ახალი წესი ჯერ ვერ ამტკიცებს, რომ ძველზე უკეთესია. ბოტი ძველ წესს იყენებს."
            )

    tiers = report.get("tiers") or {}
    sure = tiers.get("sure") or {}
    maybe = tiers.get("maybe") or {}
    sure_pairs = sure.get("pairs", 0)
    maybe_pairs = maybe.get("pairs", 0)
    if sure_pairs + maybe_pairs > 0:
        sure_same = sure.get("same", 0)
        maybe_same = maybe.get("same", 0)
        lines.append(
            f"შენი პასუხებით: 🎯 ხაზებიდან {sure_same}/{sure_pairs} იგივე ბინაა, 🤔 ხაზებიდან {maybe_same}/{maybe_pairs}."
        )

    if photo is not None:
        same, answered = photo
        lines.append(
            f"📸 ფოტოთი ძებნის ხაზებიდან {same}/{answered} იგივე ბინაა."
            if answered
            else "📸 ფოტოთი ძებნაზე პასუხი ჯერ არ გაქვს."
        )

    if sent is not None and sent > 0:
        lines.append(f"ამ კვირის შესამოწმებელი: {sent} წყვილი (ქვემოთ).")

    return "\n".join(lines)


def run(
    conn: Any,
    settings_like: Any,
    now: datetime,
    send: Optional[Any],
    out_dir: str = "out",
    dry_run: bool = False,
    vectors: Optional[List[Dict[str, Any]]] = None,
    send_photo: Optional[Any] = None,
    download: Optional[Any] = None,
    review_uncertain: int = 5,
    review_random: int = 5,
) -> int:
    iso = now.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    utc_now = now.astimezone(timezone.utc) if now.tzinfo else now
    version = f"w-{week}-{utc_now:%Y%m%dT%H%M%SZ}"

    records, skipped = build_golden_pairs(conn, getattr(settings_like, "TELEGRAM_CHAT_ID", ""))
    listings = load_listings(conn)

    t0 = time.monotonic()
    counts = count_pairs(listings)
    groups = group_pairs(listings)
    weights, em_report = estimate(counts, groups)
    runtime = time.monotonic() - t0

    if vectors is None:
        vectors_path = (
            Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "xray_rule_vectors.json"
        )
        with open(vectors_path, "r", encoding="utf-8") as f:
            vectors = json.load(f)

    champion: Optional[Dict[str, Any]] = None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, weights FROM xray_weights WHERE status = 'passed' ORDER BY created_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            try:
                w_id = row["id"]
                w_raw = row["weights"]
            except Exception:
                w_id = row[0]
                w_raw = row[1]
            w_parsed = json.loads(w_raw) if isinstance(w_raw, str) else w_raw
            if valid_weights(w_parsed):
                champion = {**w_parsed, "id": w_id}
    except Exception:
        champion = None

    report = gate(weights, records, vectors, em_report["photo_floor"], champion=champion)
    calibrations: Dict[str, Any] = {}
    if champion:
        champ_id = champion.get("id") or "champion"
        calibrations[champ_id] = calibration(champion, records)
    calibrations[version] = calibration(weights, records)

    active = champion if champion else {**weights, "id": version}
    report["tiers"] = tier_report(active, records)
    jsonl = to_jsonl(records)
    labels_hash = hashlib.sha256(jsonl.encode("utf-8")).hexdigest()

    pools_counts = {
        name: sum(pair_counts.values()) for name, pair_counts in counts.get("pools", {}).items()
    }
    counts_payload = {
        "block_pairs": counts.get("pairs", 0),
        "pools": pools_counts,
        "group_pairs": len(groups),
        "em": em_report,
        "seconds": round(runtime, 2),
    }

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "golden_pairs.jsonl").write_text(jsonl, encoding="utf-8")
    (out_path / "weights.json").write_text(
        json.dumps(weights, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )
    (out_path / "counts.json").write_text(
        json.dumps(counts_payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )
    (out_path / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )
    (out_path / "calibration.json").write_text(
        json.dumps(calibrations, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )

    seen_pairs, seen_listings = seen_review_items(conn, records)
    picks = pick_review_pairs(
        listings,
        weights,
        counts.get("pools", {}),
        seen_pairs,
        week,
        n_uncertain=review_uncertain,
        n_random=review_random,
        seen_listing_ids=seen_listings,
    )
    clean_picks = [
        {
            "key": p["key"],
            "origin": p["origin"],
            "posterior": p["posterior"],
            "champion": p["champion"],
            "kind_pairs": p.get("kind_pairs"),
            "q": p.get("q"),
            "pool": p["pool"],
        }
        for p in picks
    ]
    (out_path / "review_picks.json").write_text(
        json.dumps(clean_picks, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )

    pick_ids = sorted({p["a"]["id"] for p in picks} | {p["b"]["id"] for p in picks})
    photo_urls = load_photo_urls(conn, pick_ids)

    photos_count = 0
    if dry_run:
        downloader = download if download is not None else photo_downloader()
        for i, pick in enumerate(picks, 1):
            a_urls = photo_urls.get(str(pick["a"]["id"]), photo_urls.get(pick["a"]["id"], []))
            b_urls = photo_urls.get(str(pick["b"]["id"]), photo_urls.get(pick["b"]["id"], []))
            rows = [
                [x for x in map(downloader, a_urls) if x],
                [x for x in map(downloader, b_urls) if x],
            ]
            jpeg = collage(rows)
            if jpeg:
                photos_count += 1
                (out_path / f"review_{i}.jpg").write_bytes(jpeg)

    status = "passed" if report.get("passed") else "candidate"

    if not dry_run:
        conn.execute(XRAY_WEIGHTS_DDL)
        thresholds = '{"possible": 0.5}'
        created_at = now.isoformat()
        weights_json = json.dumps(weights, ensure_ascii=False, sort_keys=True)
        counts_json = json.dumps(counts_payload, ensure_ascii=False, sort_keys=True)
        report_json = json.dumps(report, ensure_ascii=False, sort_keys=True)
        conn.execute(
            """
            INSERT OR REPLACE INTO xray_weights
            (id, created_at, status, weights, thresholds, counts, labels_hash, report)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version,
                created_at,
                status,
                weights_json,
                thresholds,
                counts_json,
                labels_hash,
                report_json,
            ),
        )
        conn.execute(XRAY_CALIBRATION_DDL)
        for w_id, cal in calibrations.items():
            cal_json = json.dumps(cal, ensure_ascii=False, sort_keys=True)
            conn.execute(
                """
                INSERT OR REPLACE INTO xray_calibration
                (weights_id, created_at, calibration)
                VALUES (?, ?, ?)
                """,
                (w_id, created_at, cal_json),
            )
        conn.commit()

        if send:
            summary = summarize(records, skipped)
            chat_id = getattr(settings_like, "TELEGRAM_CHAT_ID", "")
            send(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": summary_text(
                        version, summary, report, sent=len(picks), photo=photo_answers(records)
                    ),
                    "parse_mode": "HTML",
                },
            )
            now_iso = now.isoformat()
            actual_downloader = download if download is not None else photo_downloader()
            actual_send_photo = (
                send_photo
                if send_photo is not None
                else telegram_photo_sender(getattr(settings_like, "TELEGRAM_BOT_TOKEN", ""))
            )

            def _tracking_send_photo(cid, jpeg, caption, markup):
                nonlocal photos_count
                photos_count += 1
                return actual_send_photo(cid, jpeg, caption, markup)

            send_review(
                conn,
                send,
                chat_id,
                picks,
                version,
                week,
                now_iso,
                send_photo=_tracking_send_photo,
                download=actual_downloader,
                photo_urls=photo_urls,
            )

    print(f"X-Ray weights {version}: {status}")
    print(
        f"Pairs: {counts.get('pairs', 0)} block pairs, "
        f"{pools_counts.get('close_area', 0)} close-area pairs, "
        f"{len(groups)} group pairs"
    )
    print(f"Review photos: {photos_count}/{len(picks)}")
    print(f"Runtime: {runtime:.2f}s")
    if hasattr(conn, "client") and hasattr(conn.client, "usage_line"):
        print(conn.client.usage_line())
    else:
        print("[D1 usage]: (offline)")

    return 0


def main(
    argv: Optional[List[str]] = None,
    now: Optional[datetime] = None,
    send: Optional[Any] = None,
    send_photo: Optional[Any] = None,
    download: Optional[Any] = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Weekly X-Ray weights estimation, gate, storage and summary."
    )
    parser.add_argument(
        "--out-dir",
        default="out",
        help="Directory to write output files (default: out)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only mode: do not write to D1 and do not send Telegram messages",
    )
    parser.add_argument(
        "--review-uncertain",
        type=int,
        default=5,
        help="Pairs where the two rules disagree (weekly default 5; a sprint e.g. 30; 0 = summary only)",
    )
    parser.add_argument(
        "--review-random",
        type=int,
        default=5,
        help="Random pairs (weekly default 5; a sprint e.g. 10; 0 = summary only)",
    )
    args = parser.parse_args(argv)

    if args.review_uncertain < 0 or args.review_random < 0:
        print("review_uncertain and review_random must be non-negative", file=sys.stderr)
        return 2

    if not settings.CLOUDFLARE_PROXY_URL:
        print("CLOUDFLARE_PROXY_URL", file=sys.stderr)
        return 2
    if not settings.CLOUDFLARE_SYNC_KEY:
        print("CLOUDFLARE_SYNC_KEY", file=sys.stderr)
        return 2
    if not settings.TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID", file=sys.stderr)
        return 2
    if not args.dry_run and not settings.TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 2

    if now is None:
        now = datetime.now(timezone.utc)
    if send is None and not args.dry_run:
        send = telegram_sender(settings.TELEGRAM_BOT_TOKEN)
    if send_photo is None and not args.dry_run:
        send_photo = telegram_photo_sender(settings.TELEGRAM_BOT_TOKEN)
    if download is None:
        download = photo_downloader()

    conn = D1Connection(
        D1Client(settings.CLOUDFLARE_PROXY_URL, settings.CLOUDFLARE_SYNC_KEY, timeout=60)
    )

    return run(
        conn=conn,
        settings_like=settings,
        now=now,
        send=send,
        out_dir=args.out_dir,
        dry_run=args.dry_run,
        send_photo=send_photo,
        download=download,
        review_uncertain=args.review_uncertain,
        review_random=args.review_random,
    )


if __name__ == "__main__":
    sys.exit(main())
