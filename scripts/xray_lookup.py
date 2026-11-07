"""Runs one X-Ray MyHome lookup that the Worker queued (ADR 0004).

The Worker cannot read myhome.ge (it gets a Cloudflare challenge), so this job reads it with
curl_cffi, the way the scraper does, and hands the result back:

1. read the job from D1 `xray_jobs`;
2. a "phone" job fetches MyHome's search page for q=<phone> and the first listing's page, and
   keeps the rows only if that listing's account phone starts like the queried number, because
   MyHome ignores a filter it does not know and then returns unrelated listings;
   a "listing" job fetches one listing's page and counts its account's listings (users=<id>);
3. save the rows to `xray_live_listings`, fill the owner fields of a listing already in
   `properties` (the Worker's persistLiveEnrichment did this before), and never insert there;
4. mark the job done or failed and ask the Worker to send the follow-up message
   (POST /api/xray/followup).

The repository is public, so nothing here prints the phone number or the chat id.

Run: python scripts/xray_lookup.py --job <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from core.database import D1Client, D1Connection  # noqa: E402
from core.xray import (  # noqa: E402
    MYHOME_SEARCH_PAGE_SIZE,
    _merge_live_into_row,
    extract_next_data,
    find_statements_list,
    live_phone_result,
    myhome_search_url,
    parse_myhome_list_item,
    save_live_listings,
    utc_now_iso,
)
from scrapers.myhome import MyHomeScraper  # noqa: E402

JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PHONE_RE = re.compile(r"^5\d{8}$")
LISTING_ID_RE = re.compile(r"^\d{4,12}$")

FetchHtml = Callable[[str], Awaitable[Optional[str]]]


def statement_details(
    scraper: MyHomeScraper, statement: Optional[dict]
) -> Optional[Dict[str, Any]]:
    """Owner fields of a listing page, by the scraper's own rules (scrapers/myhome.py)."""
    listing = scraper._normalize_item(statement) if statement else None
    if listing is None:
        return None
    return {
        "is_owner": None if listing.is_owner is None else int(bool(listing.is_owner)),
        "user_type": listing.user_type,
        "phone_number": listing.phone_number,
        "author_id": listing.author_id,
        "author_listings_count": listing.author_listings_count,
    }


async def lookup_phone(
    phone9: str, fetch_html: FetchHtml, scraper: MyHomeScraper
) -> Dict[str, Any]:
    html = await fetch_html(myhome_search_url({"q": phone9}))
    items = find_statements_list(extract_next_data(html), {"q": phone9}) if html else None
    statement = None
    if items:
        page = await fetch_html(f"https://www.myhome.ge/ka/pr/{items[0].get('id')}/")
        statement = scraper.extract_statement_from_html(page)
    return live_phone_result(phone9, items, statement, statement_details(scraper, statement))


async def lookup_listing(
    listing_id: str, fetch_html: FetchHtml, scraper: MyHomeScraper
) -> Dict[str, Any]:
    """One listing's owner signals: its page, plus how many listings its account has."""
    page = await fetch_html(f"https://www.myhome.ge/ka/pr/{listing_id}/")
    st = scraper.extract_statement_from_html(page)
    if not st or str(st.get("id")) != listing_id:
        return {"ok": False, "rows": [], "full": False}
    # A listing page names its rooms room_type_id / bedroom_type_id; a list item says room / bedroom.
    item = {
        **st,
        "room": st.get("room") or st.get("room_type_id"),
        "bedroom": st.get("bedroom") or st.get("bedroom_type_id"),
    }
    row = _merge_live_into_row(parse_myhome_list_item(item), statement_details(scraper, st))
    full = False
    if st.get("user_id"):
        params = {"users": str(st["user_id"])}
        html = await fetch_html(myhome_search_url(params))
        account = find_statements_list(extract_next_data(html), params) if html else None
        if account is not None:
            full = len(account) >= MYHOME_SEARCH_PAGE_SIZE
            row["author_listings_count"] = max(
                int(row.get("author_listings_count") or 0), len(account)
            )
    return {"ok": True, "rows": [row], "full": full}


def enrich_property(conn: Any, row: Dict[str, Any]) -> None:
    """The fields the Worker's persistLiveEnrichment updates, on a row that already exists."""
    existing = conn.execute("SELECT * FROM properties WHERE id = ?", (row["id"],)).fetchone()
    if existing is None:
        return
    merged = _merge_live_into_row(dict(existing), row)
    conn.execute(
        "UPDATE properties SET is_owner = COALESCE(?, is_owner), user_type = COALESCE(?, user_type),"
        " phone_number = COALESCE(?, phone_number), author_id = COALESCE(?, author_id),"
        " author_listings_count = COALESCE(?, author_listings_count) WHERE id = ?",
        (
            merged.get("is_owner"),
            merged.get("user_type"),
            merged.get("phone_number"),
            merged.get("author_id"),
            merged.get("author_listings_count"),
            row["id"],
        ),
    )


async def run_job(
    job_id: str,
    conn: Any,
    fetch_html: FetchHtml,
    scraper: MyHomeScraper,
    notify: Callable[[str], None],
) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT kind, phone9, listing_id FROM xray_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        return {"status": "missing"}
    kind = row["kind"] or "phone"
    phone9 = str(row["phone9"] or "")
    listing_id = str(row["listing_id"] or "")
    result: Dict[str, Any] = {"ok": False, "rows": [], "full": False}
    if kind == "phone" and PHONE_RE.match(phone9):
        result = await lookup_phone(phone9, fetch_html, scraper)
        if result["ok"]:
            save_live_listings(conn, phone9, result["rows"])
    elif kind == "listing" and LISTING_ID_RE.match(listing_id):
        result = await lookup_listing(listing_id, fetch_html, scraper)
        if result["ok"]:
            save_live_listings(conn, None, result["rows"])
            enrich_property(conn, result["rows"][0])
    status = "done" if result["ok"] else "failed"
    conn.execute(
        "UPDATE xray_jobs SET status = ?, found = ?, page_full = ?, done_at = ? WHERE id = ?",
        (status, len(result["rows"]), 1 if result["full"] else 0, utc_now_iso(), job_id),
    )
    conn.commit()
    notify(job_id)
    return {"status": status, "found": len(result["rows"]), "full": result["full"]}


def notify_worker(job_id: str) -> None:
    req = urllib.request.Request(
        settings.CLOUDFLARE_PROXY_URL.rstrip("/") + "/api/xray/followup",
        data=json.dumps({"job_id": job_id}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Sync-Key": settings.CLOUDFLARE_SYNC_KEY,
            "User-Agent": "RealEstate-XRayLookup/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", required=True, help="xray_jobs id queued by the Worker")
    args = parser.parse_args(argv)
    if not JOB_ID_RE.match(args.job):
        print("X-Ray lookup: invalid job id")
        return 2
    if not settings.CLOUDFLARE_PROXY_URL or not settings.CLOUDFLARE_SYNC_KEY:
        print("X-Ray lookup: CLOUDFLARE_PROXY_URL and CLOUDFLARE_SYNC_KEY are required")
        return 2
    conn = D1Connection(D1Client(settings.CLOUDFLARE_PROXY_URL, settings.CLOUDFLARE_SYNC_KEY))
    scraper = MyHomeScraper()
    outcome = asyncio.run(run_job(args.job, conn, scraper.fetch_html, scraper, notify_worker))
    full = " (first page full)" if outcome.get("full") else ""
    print(f"X-Ray lookup {outcome['status']}: {outcome.get('found', 0)} listing(s){full}")
    return 0 if outcome["status"] in ("done", "failed") else 1


if __name__ == "__main__":
    sys.exit(main())
