#!/usr/bin/env python3
"""How many Tbilisi flats MyHome and SS.ge list, all of them and inside config/filters.json.

A one-off, read-only measurement for deciding whether the database should hold every listing
(so the X-Ray photo search covers the whole market) and what that would cost in D1 rows. It
asks each portal for the first page of four searches (sale and rent, with and without the
filters) and prints every number in the answer whose key names a total or a count. The scrapers
read only the listings, so the name of the total field is not known in advance: it is printed
as found. Listings themselves are never printed.

Usage:
    python scripts/portal_size.py
"""

import asyncio
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from core.filters import ListingFilter  # noqa: E402
from scrapers.myhome import MyHomeScraper  # noqa: E402
from scrapers.ss_ge import SSGeScraper  # noqa: E402

COUNT_KEY = re.compile(r"total|count|last_?page|pages|per_?page|page_?size", re.IGNORECASE)
# Keys that hold a listing or a person, never printed (MyHome wraps its totals in a top "data").
SKIP_KEYS = {"data", "items", "realStateItemModel", "applications", "statements", "user"}


def numbers(value: Any, path: str = "", depth: int = 0) -> List[Tuple[str, Any]]:
    """Every (path, number) whose key names a total or a count, outside listing arrays."""
    out: List[Tuple[str, Any]] = []
    if depth > 4 or not isinstance(value, dict):
        return out
    for k, v in value.items():
        p = f"{path}.{k}" if path else str(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and COUNT_KEY.search(str(k)):
            out.append((p, v))
        elif isinstance(v, dict) and (k not in SKIP_KEYS or (k == "data" and depth == 0)):
            out.extend(numbers(v, p, depth + 1))
    return out


async def myhome_json(scraper: MyHomeScraper, url: str) -> Optional[dict]:
    """MyHomeScraper._fetch_api_page's request, keeping the whole answer."""
    from curl_cffi.requests import AsyncSession

    from scrapers.base import with_proxy_key

    target, headers = url, dict(scraper.API_HEADERS)
    if settings.CLOUDFLARE_PROXY_URL:
        target = f"{settings.CLOUDFLARE_PROXY_URL.rstrip('/')}/?url={urllib.parse.quote_plus(url)}"
        headers = with_proxy_key()
    async with AsyncSession(impersonate="chrome124") as session:
        resp = await session.get(target, headers=headers, timeout=30)
        return resp.json() if resp.status_code == 200 else None


async def ssge_json(scraper: SSGeScraper, payload: dict) -> Optional[dict]:
    """SSGeScraper._fetch_api_listings' request, keeping the whole answer."""
    api_url = f"{scraper.API_BASE_URL}/v1/RealEstate/LegendSearch"
    for use_origin in (True, False):
        async with scraper._create_session(use_origin_resolve=use_origin) as session:
            token = await scraper._get_access_token(session)
            if not token:
                continue
            targets = [api_url]
            if settings.CLOUDFLARE_PROXY_URL:
                proxy = settings.CLOUDFLARE_PROXY_URL.rstrip("/")
                targets.append(f"{proxy}/?url={urllib.parse.quote_plus(api_url)}")
            for target in targets:
                headers = scraper._build_api_headers(token, via_proxy=target != api_url)
                resp = await session.post(
                    target, data=json.dumps(payload), headers=headers, timeout=30
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data if isinstance(data, dict) else {"_list_length": len(data)}
    return None


def myhome_all_url(deal: str) -> str:
    return (
        f"{MyHomeScraper.API_BASE}?locale=ka&deal_types={deal}&real_estate_types=1"
        "&currency_id=2&cities=1&page=1"
    )


def ssge_all_payload(deal_type_id: int) -> dict:
    return {
        "realEstateType": 5,
        "realEstateDealType": deal_type_id,
        "cityIdList": [95],
        "currencyId": 2,
        "page": 1,
        "pageSize": 16,
        "order": 1,
    }


async def measure() -> List[str]:
    base = ListingFilter.from_json("config/filters.json").filters
    myhome, ssge = MyHomeScraper(), SSGeScraper()
    lines = ["## Tbilisi flats on the portals", ""]
    for deal, mh_deal, ss_deal in (("sale", "1", 4), ("rent", "2", 1)):
        filtered = base.model_copy(update={"deal_type": deal})
        searches: Dict[str, Any] = {
            f"MyHome {deal}, all": myhome_json(myhome, myhome_all_url(mh_deal)),
            f"MyHome {deal}, filters.json": myhome_json(
                myhome, myhome._build_api_url(filtered, page=1)
            ),
            f"SS.ge {deal}, all": ssge_json(ssge, ssge_all_payload(ss_deal)),
            f"SS.ge {deal}, filters.json": ssge_json(
                ssge, ssge._build_api_payload(filtered, page=1)
            ),
        }
        for name, task in searches.items():
            try:
                answer = await task
            except Exception as e:  # one search failing must not hide the others
                answer = None
                print(f"{name}: {type(e).__name__}")
            if answer is None:
                lines.append(f"- {name}: no answer")
                continue
            found = numbers(answer)
            shown = ", ".join(f"`{p}` = {v}" for p, v in found) or "no total or count field"
            lines.append(f"- {name}: {shown}")
    return lines


def main() -> None:
    import os

    text = "\n".join(asyncio.run(measure()))
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
