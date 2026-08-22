import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import asyncio
from scrapers.myhome import MyHomeScraper
from core.filters import ListingFilter
from config import settings
from notifier.ntfy_notifier import NtfyNotifier

async def send_real_live_listing_alert():
    print("📡 Fetching REAL live listings from MyHome.ge...")
    flt_engine = ListingFilter.from_json(settings.FILTERS_CONFIG_PATH)
    scraper = MyHomeScraper(max_pages=1)
    
    listings = await scraper.fetch_listings(flt_engine.filters)
    print(f"Fetched {len(listings)} real listings from MyHome.")
    
    # Pick the first real listing
    if listings:
        first_real = listings[0]
        print(f"\nReal Listing Extracted from MyHome:")
        print(f"- ID: {first_real.id}")
        print(f"- Title: {first_real.title}")
        print(f"- Price: ${first_real.price_usd:,.0f}")
        print(f"- Area: {first_real.area_m2} მ²")
        print(f"- Price/m²: ${first_real.price_per_m2:,.0f}/მ²")
        print(f"- Location: {first_real.district}, {first_real.street}")
        print(f"- URL: {first_real.url}")
        
        notifier = NtfyNotifier(topic=settings.NTFY_TOPIC or "apartments_tbilisi_notification", enable_console=True)
        print("\nSending this REAL listing to NTFY...")
        await notifier.send_notification(first_real)
        print("\n✅ Real listing alert sent to NTFY!")

if __name__ == "__main__":
    asyncio.run(send_real_live_listing_alert())
