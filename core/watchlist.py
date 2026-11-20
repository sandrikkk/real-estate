import asyncio
import html
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple

from core.database import DatabaseEngine
from core.dedup import (
    DuplicateDetector,
    _compute_hashes,
    count_image_matches,
    is_photo_duplicate,
)
from core.models import PropertyListing

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


WATCH_RECHECK_HOURS = 6
WATCH_CHECKS_PER_RUN = 15
RELIST_LOOKBACK_DAYS = 30

SOURCE_NAMES = {
    "myhome": "MyHome.ge",
    "ss_ge": "SS.ge",
}


def _is_older_than_hours(ts_str: Optional[str], hours: int) -> bool:
    if not ts_str:
        return True
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - ts >= timedelta(hours=hours)
    except Exception:
        return True


def _format_price(p: Optional[float]) -> str:
    if p is None:
        return "?"
    return f"${p:,.0f}"


async def check_watches(
    db: DatabaseEngine,
    dedup: DuplicateDetector,
    notifier,
    scrapers: list,
    cycle_prices: Dict[str, float],
    new_listings: List[PropertyListing],
    alerted: Set[Tuple[str, str]],
) -> dict:
    """Checks active watchlists for price drops, rises, delistings and relists."""
    counts = {
        "price_drop": 0,
        "price_rise": 0,
        "delisted": 0,
        "relist": 0,
    }

    now_iso = datetime.now(timezone.utc).isoformat()
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=RELIST_LOOKBACK_DAYS)).isoformat()

    # 1. Load active watches and recent delisted watches for relist comparison
    sql = """
        SELECT w.chat_id, w.listing_id, w.status, w.last_price_usd, w.last_checked_at, w.ended_at,
               p.source, p.source_id, p.url, p.district, p.area_m2, p.price_usd, p.images_json,
               p.group_id, p.delisted_at
        FROM watchlist w JOIN properties p ON p.id = w.listing_id
        WHERE w.status = 'active' OR (w.status = 'delisted' AND w.ended_at >= ?)
    """
    with db._connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (cutoff_iso,))
        rows = cursor.fetchall()

    if not rows:
        return counts

    active_rows = [r for r in rows if r["status"] == "active"]

    # 2. Determine current state per unique listing_id for active rows
    # Map listing_id -> row template
    unique_active = {}
    for r in active_rows:
        lid = r["listing_id"]
        if lid not in unique_active:
            unique_active[lid] = r

    states: Dict[str, dict] = {}
    recheck_candidates = []

    for lid, r in unique_active.items():
        if r["delisted_at"]:
            states[lid] = {"status": "deleted", "price": None}
        elif lid in cycle_prices:
            states[lid] = {"status": "alive", "price": cycle_prices[lid]}
        elif _is_older_than_hours(r["last_checked_at"], WATCH_RECHECK_HOURS):
            recheck_candidates.append(r)

    # Sort recheck candidates oldest first (empty/None last_checked_at comes first)
    recheck_candidates.sort(key=lambda r: r["last_checked_at"] or "")
    to_recheck = recheck_candidates[:WATCH_CHECKS_PER_RUN]

    if to_recheck:
        sem = asyncio.Semaphore(3)
        myhome = next((s for s in scrapers if getattr(s, "name", "") == "MyHome.ge"), None)
        ss_ge = next((s for s in scrapers if getattr(s, "name", "") == "SS.ge"), None)

        async def _check_one(r):
            source = r["source"]
            source_id = str(r["source_id"])
            lid = r["listing_id"]

            async with sem:
                try:
                    if source == "myhome" and myhome:
                        from core.myhome_alive import looks_deleted

                        del_check = await looks_deleted(myhome.fetch_html, source_id)
                        if del_check is True:
                            states[lid] = {"status": "deleted", "price": None}
                        elif del_check is False:
                            details = await myhome.fetch_statement_details(source_id)
                            price = (
                                float(details["price_usd"])
                                if details and (details.get("price_usd") or 0) > 0
                                else None
                            )
                            states[lid] = {"status": "alive", "price": price}
                        else:
                            states[lid] = {"status": "unknown", "price": None}
                    elif source == "ss_ge" and ss_ge:
                        details = await ss_ge.fetch_statement_details(source_id)
                        if details is None:
                            states[lid] = {"status": "unknown", "price": None}
                        else:
                            is_active = details.get("is_active")
                            if is_active is False:
                                states[lid] = {"status": "deleted", "price": None}
                            elif is_active is True:
                                price = (
                                    float(details["price_usd"])
                                    if (details.get("price_usd") or 0) > 0
                                    else None
                                )
                                states[lid] = {"status": "alive", "price": price}
                            else:
                                states[lid] = {"status": "unknown", "price": None}
                    else:
                        states[lid] = {"status": "unknown", "price": None}
                except Exception as err:
                    print(f"[Watchlist Recheck Error for {lid}]: {err}")
                    states[lid] = {"status": "unknown", "price": None}

        await asyncio.gather(*(_check_one(r) for r in to_recheck), return_exceptions=True)

    # 3. Events per (chat, listing)
    db_updates = []  # tuple of (status, last_price_usd, last_checked_at, ended_at, chat_id, listing_id)

    for r in active_rows:
        chat_id = str(r["chat_id"])
        lid = r["listing_id"]
        if lid not in states:
            continue

        st = states[lid]
        state = st["status"]
        live_price = st["price"]
        last_price = float(r["last_price_usd"]) if r["last_price_usd"] is not None else None

        district_esc = html.escape(str(r["district"] or ""))
        area_esc = html.escape(str(r["area_m2"] or "?"))
        url_esc = html.escape(str(r["url"] or ""), quote=True)
        src_name = html.escape(SOURCE_NAMES.get(r["source"], r["source"]))

        if state == "deleted":
            old_str = _format_price(last_price)
            msg = (
                f"❌ <b>განცხადება წაიშალა</b>, ბინა სავარაუდოდ გაიყიდა.\n"
                f"ბოლო ფასი: <b>{old_str}</b>\n"
                f"📍 {district_esc}, {area_esc} მ²\n"
                f"თვალყურის დევნება დასრულდა."
            )
            if notifier:
                await notifier.send_watch_update(chat_id, msg)
            counts["delisted"] += 1
            db_updates.append(("delisted", last_price, now_iso, now_iso, chat_id, lid))

        elif state == "alive":
            new_price = live_price if live_price is not None else last_price
            if live_price is not None and last_price is not None:
                diff = round(live_price - last_price, 2)
                if diff <= -1.0:
                    if (chat_id, lid) not in alerted:
                        diff_str = _format_price(abs(diff))
                        old_str = _format_price(last_price)
                        new_str = _format_price(live_price)
                        msg = (
                            f"📉 <b>ფასი დაიკლო</b> (თვალყურს ადევნებ)\n"
                            f"{old_str} → <b>{new_str}</b> (-{diff_str})\n"
                            f"📍 {district_esc}, {area_esc} მ²\n"
                            f'🔗 <a href="{url_esc}">განცხადება ({src_name})</a>'
                        )
                        if notifier:
                            await notifier.send_watch_update(chat_id, msg)
                        counts["price_drop"] += 1
                elif diff >= 1.0:
                    diff_str = _format_price(diff)
                    old_str = _format_price(last_price)
                    new_str = _format_price(live_price)
                    msg = (
                        f"⚠️ <b>ფასი გაიზარდა ან შესწორდა</b> (თვალყურს ადევნებ)\n"
                        f"{old_str} → <b>{new_str}</b> (+{diff_str})\n"
                        f"📍 {district_esc}, {area_esc} მ²\n"
                        f'🔗 <a href="{url_esc}">განცხადება ({src_name})</a>'
                    )
                    if notifier:
                        await notifier.send_watch_update(chat_id, msg)
                    counts["price_rise"] += 1

            db_updates.append(("active", new_price, now_iso, None, chat_id, lid))

        elif state == "unknown":
            # Only last_checked_at is updated
            db_updates.append(("active", last_price, now_iso, None, chat_id, lid))

    # Write updates with executemany in chunks of 50
    if db_updates:
        update_sql = """
            UPDATE watchlist
            SET status = ?, last_price_usd = ?, last_checked_at = ?, ended_at = ?
            WHERE chat_id = ? AND listing_id = ?
        """
        BATCH_CHUNK = 50
        with db._connection() as conn:
            cursor = conn.cursor()
            for i in range(0, len(db_updates), BATCH_CHUNK):
                chunk = db_updates[i : i + BATCH_CHUNK]
                cursor.executemany(update_sql, chunk)
            conn.commit()

    # 4. Relist 🔁: compare new_listings against active rows + delisted rows from last 30 days
    if new_listings and rows and dedup:
        watched_ids = list({r["listing_id"] for r in rows})
        watched_hashes = dedup._get_hashes_batch(watched_ids)

        # Hash watched listings that have none yet
        missing_hashes = {}
        for r in rows:
            lid = r["listing_id"]
            if not watched_hashes.get(lid) and r["images_json"]:
                try:
                    imgs = json.loads(r["images_json"])
                    if isinstance(imgs, list) and imgs:
                        missing_hashes[lid] = imgs
                except Exception:
                    pass

        if missing_hashes:
            newly_computed = {}
            for lid, imgs in missing_hashes.items():
                try:
                    h = await _compute_hashes(imgs, max_images=3)
                    if h:
                        newly_computed[lid] = h
                        watched_hashes[lid] = h
                except Exception as e:
                    print(f"[Watchlist Hash Error for {lid}]: {e}")
            if newly_computed:
                dedup.save_hashes_batch(newly_computed)

        new_ids = [nl.id for nl in new_listings]
        new_hashes = dedup._get_hashes_batch(new_ids)

        relist_alerted = set()

        for new_l in new_listings:
            h_new = new_hashes.get(new_l.id, [])
            for r in rows:
                watched_id = r["listing_id"]
                if new_l.id == watched_id:
                    continue

                watched_price = (
                    float(r["last_price_usd"])
                    if r["last_price_usd"] is not None
                    else (float(r["price_usd"]) if r["price_usd"] is not None else None)
                )
                watched_area = float(r["area_m2"]) if r["area_m2"] is not None else None
                watched_group = r["group_id"]
                h_watch = watched_hashes.get(watched_id, [])

                is_relist = False
                if h_new and h_watch:
                    matches = count_image_matches(h_new, h_watch)
                    if is_photo_duplicate(
                        matches,
                        new_l.price_usd,
                        watched_price,
                        new_l.area_m2,
                        watched_area,
                    ):
                        is_relist = True

                if not is_relist and getattr(new_l, "duplicate_group_id", None) and watched_group:
                    if new_l.duplicate_group_id == watched_group:
                        is_relist = True

                if is_relist:
                    chat_id = str(r["chat_id"])
                    pair_key = (chat_id, new_l.id)
                    if pair_key in relist_alerted:
                        continue
                    relist_alerted.add(pair_key)

                    src_new = html.escape(SOURCE_NAMES.get(new_l.source, new_l.source))
                    new_p_str = _format_price(new_l.price_usd)
                    old_p_str = _format_price(watched_price)
                    new_url_esc = html.escape(str(new_l.url or ""), quote=True)

                    msg = (
                        f"🔁 <b>იგივე ბინა ხელახლა დაიდო</b>\n"
                        f"{src_new}: <b>{new_p_str}</b> (ადრე: {old_p_str})\n"
                        f'🔗 <a href="{new_url_esc}">ახალი განცხადება</a>'
                    )

                    reply_markup = None
                    if TELEGRAM_AVAILABLE and new_l.id:
                        cb_data = f"w:{new_l.id}"
                        if len(cb_data.encode("utf-8")) <= 64:
                            reply_markup = InlineKeyboardMarkup(
                                [[InlineKeyboardButton("👁 თვალყური ადევნე", callback_data=cb_data)]]
                            )

                    if notifier:
                        await notifier.send_watch_update(chat_id, msg, reply_markup=reply_markup)
                    counts["relist"] += 1

    return counts
