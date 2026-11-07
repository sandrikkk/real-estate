"""Daily live check of the MyHome behaviour the X-Ray phone lookup relies on (ADR 0003, 0004).

Uses the network and the same client and helpers as scripts/xray_lookup.py. Exit code 1 means
MyHome changed something. The repository is public, so this never prints a phone number or
an account id.

Run: python scripts/xray_canary.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.xray import (  # noqa: E402
    MYHOME_SEARCH_PAGE_SIZE,
    extract_next_data,
    find_statements_list,
    myhome_search_url,
)
from scrapers.myhome import MyHomeScraper  # noqa: E402
from xray_lookup import lookup_phone  # noqa: E402

PHONE_IN_TEXT = re.compile(r"5\d{2}[\s.-]?\d{2}[\s.-]?\d{2}[\s.-]?\d{2}|5\d{8}")
failures: List[str] = []


def check(ok: bool, what: str) -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        failures.append(what)
    return ok


def digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


async def search(scraper: MyHomeScraper, params: Dict[str, str]) -> Optional[list]:
    html = await scraper.fetch_html(myhome_search_url(params))
    return find_statements_list(extract_next_data(html), params) if html else None


async def find_test_listing(scraper: MyHomeScraper) -> Optional[Dict[str, str]]:
    """A listing whose description states the full number behind its masked account phone."""
    for page in ("1", "2"):
        params = {"deal_types": "1", "real_estate_types": "1", "cities": "1", "page": page}
        for item in await search(scraper, params) or []:
            await asyncio.sleep(0.3)
            page_html = await scraper.fetch_html(f"https://www.myhome.ge/ka/pr/{item['id']}/")
            st = scraper.extract_statement_from_html(page_html) or {}
            prefix = digits(st.get("user_phone_number"))[:6]
            if len(prefix) < 6:
                continue
            for found in PHONE_IN_TEXT.findall(str(st.get("comment") or "")):
                if len(digits(found)) == 9 and digits(found).startswith(prefix):
                    return {
                        "id": str(item["id"]),
                        "user_id": str(st.get("user_id") or item.get("user_id")),
                        "phone9": digits(found),
                    }
    return None


async def main() -> int:
    scraper = MyHomeScraper()
    base = {"deal_types": "1", "real_estate_types": "1", "cities": "1"}
    page1 = await search(scraper, base)
    check(isinstance(page1, list), "the search page still carries statements/list in __NEXT_DATA__")
    check(
        isinstance(page1, list) and len(page1) == MYHOME_SEARCH_PAGE_SIZE,
        f"a search page still holds {MYHOME_SEARCH_PAGE_SIZE} listings",
    )

    t = await find_test_listing(scraper)
    if check(t is not None, "found a listing whose description states its account phone"):
        result = await lookup_phone(t["phone9"], scraper.fetch_html, scraper)
        check(result["ok"], "q=<phone> passes the prefix guard")
        check(
            any(r["source_id"] == t["id"] for r in result["rows"]),
            "q=<phone> returns the listing posted under that phone",
        )
        last3 = str((int(t["phone9"][6:]) + 500) % 1000).zfill(3)
        wrong = await search(scraper, {"q": t["phone9"][:6] + last3})
        check(
            isinstance(wrong, list) and not any(str(i.get("id")) == t["id"] for i in wrong),
            "q= matches the whole number (other last digits do not return the listing)",
        )
        by_user = await search(scraper, {"users": t["user_id"]})
        check(
            bool(by_user) and all(str(i.get("user_id")) == t["user_id"] for i in by_user),
            "users=<user_id> returns only that account's listings",
        )

    if failures:
        print(f"\n{len(failures)} check(s) failed: MyHome changed something X-Ray relies on.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
