import asyncio
import signal
import sys
from typing import List
import warnings

from config import settings
from core.analytics import MarketAnalytics
from core.database import DatabaseEngine, DatabaseUnavailableError
from core.dedup import DuplicateDetector
from core.filters import ListingFilter
from core.models import PropertyListing
from core.user_sync import fetch_active_users
from notifier.telegram_bot import TelegramNotifier
from scrapers.myhome import MyHomeScraper
from scrapers.ss_ge import SSGeScraper

# Suppress minor policy deprecation warnings in newer Python versions
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Ensure UTF-8 output encoding for Georgian characters across all environments
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Configure Windows Selector Event Loop for curl_cffi async compatibility
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass


class RealEstateOrchestrator:
    def __init__(self):
        print("[Init]: Initializing Database Engine...")
        try:
            self.db = DatabaseEngine(settings.DATABASE_PATH)
        except DatabaseUnavailableError as e:
            print(f"[Circuit Breaker Fatal]: {e}")
            raise

        print(f"[Init]: Loading search criteria from {settings.FILTERS_CONFIG_PATH}...")
        self.filter_engine = ListingFilter.from_json(settings.FILTERS_CONFIG_PATH)

        print("[Init]: Initializing Market Analytics Engine...")
        self.analytics = MarketAnalytics()

        print("[Init]: Initializing Scraper Modules (MyHome.ge, SS.ge)...")
        self.scrapers = [
            MyHomeScraper(timeout=settings.REQUEST_TIMEOUT_SECONDS),
            SSGeScraper(timeout=settings.REQUEST_TIMEOUT_SECONDS),
        ]

        print(
            f"[Init]: Initializing Telegram Bot Notifier (Chat ID: {settings.TELEGRAM_CHAT_ID})..."
        )
        self.telegram_notifier = TelegramNotifier(
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            chat_id=settings.TELEGRAM_CHAT_ID,
            enable_console=settings.ENABLE_CONSOLE_NOTIFICATIONS,
        )

        print("[Init]: Initializing Duplicate Detector...")
        self.dedup = DuplicateDetector(db=self.db, hash_threshold=8)

        self.running = True

    async def run_cycle(self) -> dict:
        print(
            f"\n--- [Cycle Started at {asyncio.get_event_loop().time():.2f}] Scraping active portals... ---"
        )

        # Fail-closed Circuit Breaker: verify database health before scraping
        if self.db.use_d1 and not self.db.is_healthy():
            print(
                "[Circuit Breaker]: Cloudflare D1 is unavailable. Halting scraping cycle to prevent duplicate alerts."
            )
            return {
                "status": "halted",
                "reason": "d1_circuit_breaker",
                "scraped": 0,
                "notified": 0,
            }

        # 0. Sync active user profiles (from Cloudflare KV or fallback)
        active_users = fetch_active_users()
        print(f"[Users]: Active subscribers: {len(active_users)}")

        # Determine deal types required across all active subscribers and base configuration
        active_deal_types = set()
        cfg_dt = getattr(self.filter_engine.filters, "deal_type", "sale")
        if cfg_dt == "both":
            active_deal_types.add("sale")
            active_deal_types.add("rent")
        elif cfg_dt in ["sale", "rent"]:
            active_deal_types.add(cfg_dt)

        for u in active_users:
            dt = getattr(u, "deal_type", "sale") or "sale"
            if dt == "both":
                active_deal_types.add("sale")
                active_deal_types.add("rent")
            elif dt in ["sale", "rent"]:
                active_deal_types.add(dt)

        print(f"[Run]: Active deal types: {', '.join(sorted(active_deal_types))}")

        # Build scraping tasks for each active deal type
        tasks = []
        task_meta = []
        for dt in sorted(active_deal_types):
            envelope = self.filter_engine.filters.model_copy()
            envelope.deal_type = dt

            dt_users = [u for u in active_users if getattr(u, "deal_type", "sale") in [dt, "both"]]

            if dt == "rent":
                rent_mins = [
                    getattr(u, "rent_price_min_usd", None)
                    or (u.price_min_usd if getattr(u, "deal_type", "sale") == "rent" else None)
                    for u in dt_users
                ]
                rent_mins = [p for p in rent_mins if p is not None]
                rent_maxs = [
                    getattr(u, "rent_price_max_usd", None)
                    or (u.price_max_usd if getattr(u, "deal_type", "sale") == "rent" else None)
                    for u in dt_users
                ]
                rent_maxs = [p for p in rent_maxs if p is not None]

                min_val = (
                    min(rent_mins)
                    if rent_mins
                    else (self.filter_engine.filters.rent_price_min_usd or 300)
                )
                max_val = (
                    max(rent_maxs)
                    if rent_maxs
                    else (self.filter_engine.filters.rent_price_max_usd or 1500)
                )
                envelope.price_min_usd = min_val
                envelope.price_max_usd = max_val
                envelope.rent_price_min_usd = min_val
                envelope.rent_price_max_usd = max_val
            else:
                sale_mins = [u.price_min_usd for u in dt_users if u.price_min_usd is not None]
                sale_maxs = [u.price_max_usd for u in dt_users if u.price_max_usd is not None]
                if sale_mins:
                    envelope.price_min_usd = min(sale_mins)
                if sale_maxs:
                    envelope.price_max_usd = max(sale_maxs)

            if dt_users:
                valid_min_areas = [u.area_min_m2 for u in dt_users if u.area_min_m2 is not None]
                valid_max_areas = [u.area_max_m2 for u in dt_users if u.area_max_m2 is not None]
                valid_min_rooms = [u.rooms_min for u in dt_users if u.rooms_min is not None]
                if valid_min_areas:
                    envelope.area_min_m2 = min(valid_min_areas)
                if valid_max_areas:
                    envelope.area_max_m2 = max(valid_max_areas)
                if valid_min_rooms:
                    envelope.rooms_min = min(valid_min_rooms)

                all_user_districts = set()
                for u in dt_users:
                    if u.districts:
                        all_user_districts.update(u.districts)
                if all_user_districts:
                    envelope.whitelist_districts = list(all_user_districts)

                active_owner_types = {
                    (getattr(u, "owner_type", "all") or "all").lower().strip() for u in dt_users
                }
                if len(active_owner_types) == 1:
                    ot = list(active_owner_types)[0]
                    if ot in ["owner", "agent"]:
                        envelope.owner_type = ot
                else:
                    envelope.owner_type = None

            for scraper in self.scrapers:
                tasks.append(scraper.fetch_listings(envelope))
                task_meta.append((scraper, dt))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_listings: List[PropertyListing] = []
        for (scraper, dt), res in zip(task_meta, results):
            if isinstance(res, Exception):
                print(f"[{scraper.name} ({dt}) Error]: Scraper failed with exception: {res}")
            elif isinstance(res, list):
                print(f"[{scraper.name} ({dt})]: Fetched {len(res)} valid listings.")
                all_listings.extend(res)

        new_count = 0
        matched_count = 0
        notified_count = 0
        seen_in_batch = set()

        user_sent_count = {}
        MAX_ALERTS_PER_USER_CYCLE = 10

        # 1. In-memory batch deduplication across all scraped listings
        unique_listings = []
        for listing in all_listings:
            if listing.id not in seen_in_batch:
                seen_in_batch.add(listing.id)
                unique_listings.append(listing)
                if listing.id not in self.db._seen_ids:
                    new_count += 1

        # 2. Batch save all newly fetched listings in 1 single HTTP/database query
        self.db.save_listings_batch(unique_listings)

        # 3. Batch check unnotified listing IDs for each active user (1 query per user)
        all_unique_ids = [item.id for item in unique_listings]
        unnotified_ids_by_user = {}
        for u in active_users:
            unnotified_ids_by_user[str(u.chat_id)] = set(
                self.db.filter_unnotified_listings(u.chat_id, all_unique_ids)
            )

        if not getattr(settings, "ENABLE_MULTI_USER", True) and settings.TELEGRAM_CHAT_ID:
            admin_cid = str(settings.TELEGRAM_CHAT_ID)
            if admin_cid not in unnotified_ids_by_user:
                unnotified_ids_by_user[admin_cid] = set(
                    self.db.filter_unnotified_listings(admin_cid, all_unique_ids)
                )

        for listing in unique_listings:
            # 1. Baseline Filter Hygiene (Under Construction, Black Frame, Stop-words, Location Blacklist)
            if not self.filter_engine.matches_hygiene(listing):
                continue

            # 2. Identify active users who match this listing AND have not received it yet
            matching_users = [
                u
                for u in active_users
                if listing.id in unnotified_ids_by_user.get(str(u.chat_id), set())
                and self.filter_engine.matches_user(u, listing, tentative=True)
            ]

            # Fallback for single-admin / legacy mode if no user matched via multi-user
            if not matching_users and not getattr(settings, "ENABLE_MULTI_USER", True):
                admin_cid = str(settings.TELEGRAM_CHAT_ID)
                if listing.id in unnotified_ids_by_user.get(
                    admin_cid, set()
                ) and self.filter_engine.matches(listing):
                    matching_users = [u for u in active_users if str(u.chat_id) == admin_cid]

            if not matching_users:
                continue

            matched_count += 1

            # 3. Statement Detail Enrichment & Valuation Scale Evaluation
            myhome_label = None
            if listing.source == "myhome":
                myhome_scraper = next((s for s in self.scrapers if s.name == "MyHome.ge"), None)
                if myhome_scraper and hasattr(myhome_scraper, "fetch_statement_details"):
                    details = await myhome_scraper.fetch_statement_details(listing.source_id)
                    if details:
                        live_usd = details.get("price_usd")
                        live_gel = details.get("price_gel")
                        if live_usd and live_usd > 0:
                            listing.price_usd = live_usd
                            if live_gel and live_gel > 0:
                                listing.price_gel = live_gel
                            if listing.area_m2 and listing.area_m2 > 0:
                                listing.price_per_m2 = round(listing.price_usd / listing.area_m2, 2)

                        if "is_owner" in details and details["is_owner"] is not None:
                            listing.is_owner = bool(details["is_owner"])
                        elif (
                            details.get("agency_name")
                            or details.get("broker")
                            or details.get("agency")
                        ):
                            listing.is_owner = False

                        if not listing.condition_id and details.get("condition_id"):
                            listing.condition_id = details.get("condition_id")
                            cond_obj = details.get("condition")
                            listing.condition_name = (
                                cond_obj.get("name")
                                if isinstance(cond_obj, dict)
                                else (cond_obj or None)
                            )
                        if not listing.phone_number:
                            from scrapers.myhome import _extract_phone_number

                            listing.phone_number = _extract_phone_number(
                                details.get("user_phone_number"),
                                details.get("comment") or listing.description,
                            )
                        if details.get("price_label"):
                            myhome_label = details.get("price_label")
            elif listing.source == "ss_ge":
                ss_scraper = next((s for s in self.scrapers if s.name == "SS.ge"), None)
                if ss_scraper and hasattr(ss_scraper, "fetch_statement_details"):
                    details = await ss_scraper.fetch_statement_details(listing.source_id)
                    if details:
                        live_usd = details.get("price_usd")
                        live_gel = details.get("price_gel")
                        if live_usd and live_usd > 0:
                            listing.price_usd = live_usd
                            if live_gel and live_gel > 0:
                                listing.price_gel = live_gel
                            if listing.area_m2 and listing.area_m2 > 0:
                                listing.price_per_m2 = round(listing.price_usd / listing.area_m2, 2)

                        if "is_owner" in details and details["is_owner"] is not None:
                            listing.is_owner = bool(details["is_owner"])
                        elif details.get("agency_id") or details.get("agency_name"):
                            listing.is_owner = False
                        if not listing.condition_id and details.get("condition_id"):
                            listing.condition_id = details.get("condition_id")
                            cond_obj = details.get("condition")
                            listing.condition_name = (
                                cond_obj.get("name")
                                if isinstance(cond_obj, dict)
                                else (cond_obj or None)
                            )
                        if not listing.phone_number and details.get("phone_number"):
                            from scrapers.myhome import _extract_phone_number

                            listing.phone_number = _extract_phone_number(
                                details["phone_number"], listing.description
                            )
                        elif not listing.phone_number and details.get("user_phone_number"):
                            from scrapers.myhome import _extract_phone_number

                            listing.phone_number = _extract_phone_number(
                                details["user_phone_number"], listing.description
                            )
                        if details.get("price_label"):
                            myhome_label = details.get("price_label")

            # Re-verify baseline filter hygiene with enriched live price, condition & status
            if not self.filter_engine.matches_hygiene(listing):
                self.db.save_listing(listing)
                continue

            # Re-verify matching users after detail enrichment (ensures verified price and is_owner status match preferences)
            matching_users = [
                u
                for u in matching_users
                if self.filter_engine.matches_user(u, listing, tentative=False)
            ]
            if not matching_users:
                self.db.save_listing(listing)
                continue

            self.analytics.evaluate_listing(
                listing,
                db=self.db,
                discount_threshold_pct=settings.BARGAIN_DISCOUNT_THRESHOLD_PCT,
                myhome_price_label=myhome_label,
            )

            # Re-check is_hot_deal flag in case condition was just enriched
            if listing.price_per_m2 <= self.filter_engine.filters.hot_deal_price_per_sqm:
                if listing.condition_id in [1, 2] or (
                    listing.condition_name and "გარემონტებული" in listing.condition_name
                ):
                    listing.is_hot_deal = True
                    listing.is_bargain = True

            # 4. Save to Database
            self.db.save_listing(listing)

            # 4.5 Duplicate Detection
            dup_info = await self.dedup.check_listing(listing)
            if dup_info:
                listing.duplicate_group_id = dup_info["group_id"]
                # Skip alert if this apartment was already alerted at a lower price
                if not dup_info.get("is_new_best_price"):
                    continue

            # 5. Dispatch Alert to each matching unnotified user via Telegram Bot
            sent_any = False
            if self.telegram_notifier:
                for user in matching_users:
                    if user_sent_count.get(user.chat_id, 0) >= MAX_ALERTS_PER_USER_CYCLE:
                        continue
                    try:
                        sent = await self.telegram_notifier.send_notification(
                            listing, target_chat_id=user.chat_id, dup_info=dup_info
                        )
                        if sent:
                            sent_any = True
                            notified_count += 1
                            user_sent_count[user.chat_id] = user_sent_count.get(user.chat_id, 0) + 1
                            self.db.mark_user_notified(user.chat_id, listing.id)
                            unnotified_ids_by_user.get(str(user.chat_id), set()).discard(listing.id)
                    except Exception as e:
                        print(f"[Telegram Notification Error for {user.chat_id}]: {e}")
                    await asyncio.sleep(settings.RATE_LIMIT_DELAY_SECONDS)

            if sent_any:
                self.db.mark_as_notified(listing.id)

        stats = {
            "total_fetched": len(all_listings),
            "new_listings": new_count,
            "matched_filters": matched_count,
            "notifications_sent": notified_count,
        }
        print(
            f"--- [Cycle Summary]: Fetched: {stats['total_fetched']} | New: {stats['new_listings']} | Matched: {stats['matched_filters']} | Alerts: {stats['notifications_sent']} ---\n"
        )
        return stats

    async def start(self):
        print("=" * 60)
        print("  Real Estate Market Tracker & Telegram Notifier Running")
        print(f"  Telegram Chat ID: {settings.TELEGRAM_CHAT_ID or '(Console only)'}")
        print(f"  Check Interval:   {settings.CHECK_INTERVAL_SECONDS} seconds")
        print(f"  Database Path:    {settings.DATABASE_PATH}")
        print("=" * 60)

        while self.running:
            try:
                await self.run_cycle()
            except DatabaseUnavailableError as e:
                print(
                    f"[Circuit Breaker]: D1 database unavailable ({e}). Waiting for next cycle..."
                )
            except Exception as e:
                print(f"[Orchestrator Unexpected Error]: {e}")

            if not self.running:
                break

            print(
                f"[Sleeping]: Next check in {settings.CHECK_INTERVAL_SECONDS} seconds. Press Ctrl+C to stop.\n"
            )
            try:
                await asyncio.sleep(settings.CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    def stop(self):
        print("\n[Shutdown]: Stopping Real Estate Tracker...")
        self.running = False


async def main():
    try:
        orchestrator = RealEstateOrchestrator()
    except DatabaseUnavailableError as e:
        print(f"[Circuit Breaker Fatal]: {e}")
        print("[Circuit Breaker]: Halting process execution (Fail-Closed).")
        sys.exit(1)

    if "--stats" in sys.argv or "--report" in sys.argv:
        print("\n[Market Intelligence]: Computing comprehensive statistics...")
        report = orchestrator.analytics.format_market_report(orchestrator.db)
        print("\n" + report + "\n")
        if "--telegram" in sys.argv:
            await orchestrator.telegram_notifier.send_market_report(report)
            print("[Telegram]: Market report sent successfully.")
        return

    if "--once" in sys.argv:
        print("[Run-Once Mode]: Executing 1 full market scan & alert cycle...")
        try:
            stats = await orchestrator.run_cycle()
        except DatabaseUnavailableError as e:
            print(
                f"[Circuit Breaker Fatal]: D1 database error during run ({e}). Halting process (Fail-Closed)."
            )
            sys.exit(1)

        if stats.get("status") == "halted":
            print(
                f"[Circuit Breaker]: Cycle was halted ({stats.get('reason')}). No duplicate alerts sent."
            )
            sys.exit(1)

        print(
            f"[Finished]: Fetched: {stats.get('total_fetched', 0)} | New: {stats.get('new_listings', 0)} | Matched: {stats.get('matched_filters', 0)} | Alerts: {stats.get('notifications_sent', 0)}"
        )
        return

    # Graceful shutdown handler for signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, orchestrator.stop)
        except NotImplementedError:
            pass

    try:
        await orchestrator.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        orchestrator.stop()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Exit]: Process terminated by user.")
        sys.exit(0)
