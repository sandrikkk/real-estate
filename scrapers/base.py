import json
import re
import urllib.parse
from abc import ABC, abstractmethod
from typing import Any, List, Optional

from curl_cffi.requests import AsyncSession

from config import settings
from core.models import PropertyListing, SearchFilters

_DESCRIPTION_KEYS = ("ka", "text", "alllanguagetogather", "en", "ru")
# The start of a JSON text whose end was cut off: {"KA":"...
_JSON_TEXT_RE = re.compile(r'\{\s*"(ka|text|en|ru)"\s*:\s*"(.*)', re.S | re.I)
# Where that first language's text ends: a closing quote before the next key or the last brace.
_JSON_TEXT_END_RE = re.compile(r'"\s*(?:,\s*"[A-Za-z]+"\s*:|\}\s*$)')


def _description_from_dict(data: dict) -> str:
    """The first non-empty text of a portal's multi-language description."""
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in _DESCRIPTION_KEYS:
        value = lowered.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def clean_description(raw: Any) -> str:
    """A listing description as plain text.

    Portals send it as a dict of languages, and some sellers paste it as JSON text
    ({"KA": ...}), sometimes encoded twice. Other text is returned unchanged.
    """
    if raw is None:
        return ""
    if isinstance(raw, dict):
        return _description_from_dict(raw)
    text = str(raw)
    body = text.strip().lstrip("\u200c").strip()
    if not body.startswith("{"):
        return text
    for candidate in (body, '"' + body + '"'):
        try:
            data = json.loads(candidate, strict=False)
            if isinstance(data, str):
                data = json.loads(data, strict=False)
        except ValueError:
            continue
        if isinstance(data, dict) and any(str(k).lower() in _DESCRIPTION_KEYS for k in data):
            return _description_from_dict(data)
    # Portals cut long text, so the JSON may have no end: keep its first language's text.
    match = _JSON_TEXT_RE.match(body.replace('\\"', '"'))
    if not match:
        return text
    value = match.group(2)
    end = _JSON_TEXT_END_RE.search(value)
    if end:
        value = value[: end.start()]
    return value.replace("\\n", "\n")


def with_proxy_key(headers: Optional[dict] = None) -> dict:
    """Headers for the Cloudflare Worker proxy. Never send these to a portal directly."""
    out = dict(headers or {})
    key = (settings.CLOUDFLARE_SYNC_KEY or "").strip()
    if key:
        out["X-Sync-Key"] = key
    return out


class BaseScraper(ABC):
    def __init__(self, name: str, timeout: int = 15):
        self.name = name
        self.timeout = timeout
        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "ka-GE,ka;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Ch-Ua": '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    async def _get_session(self) -> AsyncSession:
        return AsyncSession(impersonate="chrome124")

    async def fetch_html(self, url: str) -> Optional[str]:
        target_url = url
        req_headers = self.headers
        timeout = self.timeout
        if settings.CLOUDFLARE_PROXY_URL:
            target_url = (
                f"{settings.CLOUDFLARE_PROXY_URL.rstrip('/')}/?url={urllib.parse.quote_plus(url)}"
            )
            req_headers = with_proxy_key()
            timeout = max(self.timeout, 30)
        elif settings.SCRAPER_API_KEY:
            target_url = f"https://api.scraperapi.com?api_key={settings.SCRAPER_API_KEY}&url={urllib.parse.quote_plus(url)}"
            req_headers = {}
            timeout = max(self.timeout, 45)

        try:
            async with AsyncSession(impersonate="chrome124") as session:
                response = await session.get(
                    target_url, headers=req_headers if req_headers else None, timeout=timeout
                )
                if response.status_code == 200:
                    return response.text
                elif (
                    response.status_code in (401, 403, 429, 500, 502, 503, 504)
                    and target_url != url
                ):
                    # Cloudflare worker / proxy hit daily limit, rate limit, or WAF block. Fallback directly!
                    direct_resp = await session.get(url, headers=self.headers, timeout=self.timeout)
                    if direct_resp.status_code == 200:
                        return direct_resp.text
                    if settings.SCRAPER_API_KEY and "api.scraperapi.com" not in target_url:
                        scraper_api_url = (
                            f"https://api.scraperapi.com?api_key={settings.SCRAPER_API_KEY}"
                            f"&url={urllib.parse.quote_plus(url)}"
                        )
                        sapi_resp = await session.get(
                            scraper_api_url, timeout=max(self.timeout, 45)
                        )
                        if sapi_resp.status_code == 200:
                            return sapi_resp.text
                print(f"[{self.name} HTTP Error]: Status {response.status_code} for {url}")
        except Exception as e:
            if target_url != url:
                try:
                    async with AsyncSession(impersonate="chrome124") as session:
                        direct_resp = await session.get(
                            url, headers=self.headers, timeout=self.timeout
                        )
                        if direct_resp.status_code == 200:
                            return direct_resp.text
                except Exception:
                    pass
            print(f"[{self.name} Network Error]: {e}")
        return None

    @abstractmethod
    async def fetch_listings(self, filters: SearchFilters) -> List[PropertyListing]:
        """
        Scrapes and extracts normalized PropertyListing items matching the search filters.
        """
        pass
