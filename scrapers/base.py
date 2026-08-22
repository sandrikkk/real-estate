from abc import ABC, abstractmethod
from typing import List, Optional
from curl_cffi.requests import AsyncSession
from core.models import PropertyListing, SearchFilters


class BaseScraper(ABC):
    def __init__(self, name: str, timeout: int = 15):
        self.name = name
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ka,en-US;q=0.9,en;q=0.8",
        }

    async def _get_session(self) -> AsyncSession:
        return AsyncSession(impersonate="chrome120")

    async def fetch_html(self, url: str) -> Optional[str]:
        try:
            async with AsyncSession(impersonate="chrome120") as session:
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
