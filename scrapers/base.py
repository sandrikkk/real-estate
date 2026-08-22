from abc import ABC, abstractmethod
from typing import List, Optional
from curl_cffi.requests import AsyncSession
from core.models import PropertyListing, SearchFilters


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
        try:
            async with AsyncSession(impersonate="chrome124") as session:
                response = await session.get(url, headers=self.headers, timeout=self.timeout)
                if response.status_code == 200:
                    return response.text
                else:
                    print(f"[{self.name} HTTP Error]: Status {response.status_code} for {url}")
        except Exception as e:
            print(f"[{self.name} Network Error]: {e}")
        return None

    @abstractmethod
    async def fetch_listings(self, filters: SearchFilters) -> List[PropertyListing]:
        """
        Scrapes and extracts normalized PropertyListing items matching the search filters.
        """
        pass
