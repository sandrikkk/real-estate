"""District $/m² averages from MyHome's own price statistics (the myhome.ge/binebis-pasebi page).

Endpoint: GET https://api-statements.tnet.ge/v1/statements/statistics/city-district-urban-prices
Both X-Ray (core/xray.py, cloudflare/worker.js) and the alert benchmark (core/analytics.py)
read the figure from here instead of averaging the listings this bot happened to scrape.
"""

from typing import Any, Dict, Iterable, Optional

# MyHome's bedroom_type_id for studios; 1, 2 and 3 are the bedroom counts themselves.
# 4+ bedrooms have no table: the endpoint answers 200 with no items.
BEDROOM_TYPE_STUDIO = 6


def bedroom_type_for(rooms: Optional[int], bedrooms: Optional[int]) -> Optional[int]:
    """Maps a listing's layout to MyHome's bedroom_type_id, or None for the all-flats average."""
    if bedrooms in (1, 2, 3):
        return bedrooms
    if not bedrooms:
        if rooms == 1:
            return BEDROOM_TYPE_STUDIO
        if rooms in (2, 3, 4):
            return rooms - 1  # rooms = bedrooms + 1
    return None


def parse_price_table(payload: Any) -> Optional[Dict[str, float]]:
    """Flattens the response into {area name: $/m²}, covering both groups and urbans.

    data.items is a list of objects like
    {"ვაკე-საბურთალო": 1478.23, "47": {"საბურთალო": 1577.81}, ...}: the group's own figure
    plus one {urban_id: {name: figure}} entry per urban. Returns None when there is nothing
    usable, so callers never mistake an empty answer for a price.
    """
    if not isinstance(payload, dict) or payload.get("result") is not True:
        return None
    data = payload.get("data")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    table: Dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if isinstance(value, dict):
                for name, price in value.items():
                    if isinstance(price, (int, float)):
                        table[name] = float(price)
            elif isinstance(value, (int, float)):
                table[key] = float(value)
    return table or None


def lookup_price(table: Optional[Dict[str, float]], names: Iterable[str]) -> Optional[float]:
    """First of `names` that MyHome has a figure for. The caller orders them most specific first."""
    if not table:
        return None
    for name in names:
        if name in table:
            return table[name]
    return None
