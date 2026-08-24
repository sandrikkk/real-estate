import asyncio
import io
import signal
import sys
import warnings
from typing import List

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

from config import settings
from core.models import PropertyListing
from core.database import DatabaseEngine
from core.filters import ListingFilter
from core.analytics import MarketAnalytics
from scrapers.myhome import MyHomeScraper
from scrapers.ss_ge import SSGeScraper
from scrapers.area_ge import AreaGeScraper
from notifier.telegram_bot import TelegramNotifier


class RealEstateOrchestrator:
    def __init__(self):
        print("[Init]: Initializing Database Engine...")
        self.db = DatabaseEngine(settings.DATABASE_PATH)

        print(f"[Init]: Loading search criteria from {settings.FILTERS_CONFIG_PATH}...")
        self.filter_engine = ListingFilter.from_json(settings.FILTERS_CONFIG_PATH)

        print("[Init]: Initializing Market Analytics Engine...")
        self.analytics = MarketAnalytics()

        print("[Init]: Initializing Scraper Modules (MyHome.ge, SS.ge)...")
        self.scrapers = [
            MyHomeScraper(timeout=settings.REQUEST_TIMEOUT_SECONDS),
            SSGeScraper(timeout=settings.REQUEST_TIMEOUT_SECONDS),
        ]

        print(f"[Init]: Initializing Telegram Bot Notifier (Chat ID: {settings.TELEGRAM_CHAT_ID})...")
        self.telegram_notifier = TelegramNotifier(
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            chat_id=settings.TELEGRAM_CHAT_ID,
            enable_console=settings.ENABLE_CONSOLE_NOTIFICATIONS
        )

        self.running = True

    async def run_cycle(self) -> dict:
        print(f"\n--- [Cycle Started at {asyncio.get_event_loop().time():.2f}] Scraping active portals... ---")
        tasks = [scraper.fetch_listings(self.filter_engine.filters) for scraper in self.scrapers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_listings: List[PropertyListing] = []
        for scraper, res in zip(self.scrapers, results):
            if isinstance(res, Exception):
                print(f"[{scraper.name} Error]: Scraper failed with exception: {res}")
            elif isinstance(res, list):
                print(f"[{scraper.name}]: Fetched {len(res)} valid listings.")
                all_listings.extend(res)

        new_count = 0
        matched_count = 0
        notified_count = 0
        seen_in_batch = set()

        for listing in all_listings:
            # In-memory batch deduplication
            if listing.id in seen_in_batch:
                continue
            seen_in_batch.add(listing.id)

            # 1. Database Deduplication Check (ID & Fingerprint)
            if self.db.is_seen(listing.id, listing):
                continue

            new_count += 1

            # 2. Filter Criteria Check (Price, Area, Districts, Stop-words)
            if not self.filter_engine.matches(listing):
                # Save as seen so we don't re-process in subsequent cycles
                self.db.save_listing(listing)
                continue

            matched_count += 1

            # 3. Market Analytics & Bargain / Valuation Scale Evaluation
            myhome_label = None
            if listing.source == "myhome":
                myhome_scraper = next((s for s in self.scrapers if s.name == "MyHome.ge"), None)
                if myhome_scraper and hasattr(myhome_scraper, "fetch_price_label"):
                    myhome_label = await myhome_scraper.fetch_price_label(listing.source_id)

            self.analytics.evaluate_listing(
                listing,
                db=self.db,
                discount_threshold_pct=settings.BARGAIN_DISCOUNT_THRESHOLD_PCT,
                myhome_price_label=myhome_label
            )

            # 4. Save to Database
            self.db.save_listing(listing)

            # 5. Dispatch Alert via Telegram Bot
            sent = False
            if self.telegram_notifier:
                sent = await self.telegram_notifier.send_notification(listing)

            if sent:
                self.db.mark_as_notified(listing.id)
                notified_count += 1

            # Brief pause to respect notification rates
            await asyncio.sleep(settings.RATE_LIMIT_DELAY_SECONDS)

        stats = {
            "total_fetched": len(all_listings),
            "new_listings": new_count,
            "matched_filters": matched_count,
            "notifications_sent": notified_count
        }
        print(f"--- [Cycle Summary]: Fetched: {stats['total_fetched']} | New: {stats['new_listings']} | Matched: {stats['matched_filters']} | Alerts: {stats['notifications_sent']} ---\n")
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
            except Exception as e:
                print(f"[Orchestrator Unexpected Error]: {e}")

            if not self.running:
                break

            print(f"[Sleeping]: Next check in {settings.CHECK_INTERVAL_SECONDS} seconds. Press Ctrl+C to stop.\n")
            try:
                await asyncio.sleep(settings.CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    def stop(self):
        print("\n[Shutdown]: Stopping Real Estate Tracker...")
        self.running = False


async def main():
    orchestrator = RealEstateOrchestrator()

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
        stats = await orchestrator.run_cycle()
        print(f"[Finished]: Fetched: {stats.get('total_fetched', 0)} | New: {stats.get('new_listings', 0)} | Matched: {stats.get('matched_filters', 0)} | Alerts: {stats.get('notifications_sent', 0)}")
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Exit]: Process terminated by user.")
        sys.exit(0)
