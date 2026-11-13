"""Throwaway diagnosis: why the photo search missed MyHome listing 26209700."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dedup import _compute_hashes, hamming  # noqa: E402
from core.owner_search import _image_urls, _myhome_candidates, _ssge_candidates, _statement_from_html  # noqa: E402
from core.photo_match import LIVE_MAX_HASHED, _gap  # noqa: E402
from core.xray import parse_myhome_list_item  # noqa: E402
from scrapers.myhome import MyHomeScraper  # noqa: E402
from scrapers.ss_ge import SSGeScraper  # noqa: E402
from scripts.xray_lookup import scraper_sources  # noqa: E402

LID = "26209700"
POST = {"photo A (kitchen)": "6b6f4d6f6fe9cd4d", "photo B (living room)": "741ca42425ad99b1"}
FLAT = {"district": "ნუცუბიძე", "area_m2": 56.1, "rooms": None, "bedrooms": 1, "floor": "5", "total_floors": 5}


async def main():
    myhome = MyHomeScraper()
    st = _statement_from_html(await myhome.fetch_html(f"https://www.myhome.ge/ka/pr/{LID}/"))
    if not st:
        print("listing page: no statement")
        return
    row = parse_myhome_list_item(st)
    print("listing:", {k: row.get(k) for k in ("area_m2", "floor", "total_floors", "rooms", "district", "price_usd", "user_type")})
    print("dates:", {k: v for k, v in st.items() if any(t in k for t in ("date", "created", "updated", "_at"))})
    urls = _image_urls(row)
    print("photos on the listing:", len(urls))
    for i, u in enumerate(urls, 1):
        h = (await _compute_hashes([u], max_images=1) or [None])[0]
        if h:
            d = {name: hamming(h, ph) for name, ph in POST.items()}
            mark = " <== MATCH" if min(d.values()) <= 10 else ""
            print(f"  #{i}: bits apart {d}{mark}")
        else:
            print(f"  #{i}: not downloaded")

    sources = scraper_sources(myhome, SSGeScraper())
    (mh, mh_full), (ss, ss_full) = await asyncio.gather(
        _myhome_candidates(FLAT, sources, owners_only=False),
        _ssge_candidates(FLAT, sources, owners_only=False),
    )
    print(f"query: myhome {len(mh or [])} rows (last page full: {mh_full}), ss.ge {len(ss or [])} rows (last page full: {ss_full})")
    pool = {c["id"]: c for c in [*(mh or []), *(ss or [])] if c.get("id") and _image_urls(c)}
    ranked = sorted(pool.values(), key=lambda c: (_gap(c.get("area_m2"), FLAT["area_m2"]), _gap(c.get("price_usd"), None), c["id"]))
    ids = [c["id"] for c in ranked]
    target = f"myhome_{LID}"
    if target in ids:
        print(f"listing in the query: yes, rank {ids.index(target) + 1} of {len(ids)} (checked: first {LIVE_MAX_HASHED})")
    else:
        print("listing in the query: NO")
    print("areas of the first 24:", [c.get("area_m2") for c in ranked[:LIVE_MAX_HASHED]])


asyncio.run(main())
