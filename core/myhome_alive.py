"""Is a MyHome listing still on MyHome? (the X-Ray "alive" job and the nightly sweep)

MyHome's listing page loads for a deleted listing too, without the listing in it; a listing
that is there carries is_active. A page that does not load says nothing.
"""

from typing import Awaitable, Callable, Optional

from core.owner_search import _statement_from_html

FetchHtml = Callable[[str], Awaitable[Optional[str]]]


def myhome_page(source_id: str) -> str:
    return f"https://www.myhome.ge/ka/pr/{source_id}/"


async def looks_deleted(fetch_html: FetchHtml, source_id: str) -> Optional[bool]:
    """True when MyHome's page loads without the listing (deleted) or shows it inactive,
    False when it is there, None when the page did not load."""
    html = await fetch_html(myhome_page(source_id))
    if html is None:
        return None
    statement = _statement_from_html(html)
    return statement is None or statement.get("is_active") is False
