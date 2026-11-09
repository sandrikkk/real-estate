"""Print or save the X-Ray golden pairs (ADR 0002) from production D1, read-only.

This script reads admin answers from xray_feedback, loads the corresponding replies from
xray_results or xray_jobs/xray_targets, backfills missing candidate fields from properties
or xray_live_listings, strips all PII (phone numbers, author ids, chat ids), and outputs a summary
and optional JSONL file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from core.database import D1Client, D1Connection  # noqa: E402
from core.golden_pairs import build_golden_pairs, summarize, to_jsonl  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print or save the X-Ray golden pairs (ADR 0002) from production D1, read-only."
    )
    parser.add_argument("--out", type=str, default=None, help="Path to output JSONL file")
    args = parser.parse_args(argv)

    for name in ("CLOUDFLARE_PROXY_URL", "CLOUDFLARE_SYNC_KEY", "TELEGRAM_CHAT_ID"):
        if not getattr(settings, name, None):
            print(name)
            return 2

    conn = D1Connection(
        D1Client(settings.CLOUDFLARE_PROXY_URL, settings.CLOUDFLARE_SYNC_KEY, timeout=60)
    )
    records, skipped = build_golden_pairs(conn, str(settings.TELEGRAM_CHAT_ID))

    print(json.dumps(summarize(records, skipped), ensure_ascii=False, sort_keys=True, indent=1))
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(to_jsonl(records))

    print(conn.client.usage_line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
