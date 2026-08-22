import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import asyncio
from core.models import PropertyListing
from notifier.ntfy_notifier import NtfyNotifier
from config import settings

async def send_live_test_alert():
    topic = settings.NTFY_TOPIC or "apartments_tbilisi_notification"
    print(f"📡 Sending Live Test Property Alert to NTFY topic: '{topic}'...")
    
    notifier = NtfyNotifier(topic=topic, enable_console=True)
    
    # Simulate a realistic new apartment posted in Dighomi matching all user criteria
    sample_listing = PropertyListing(
        id="myhome_test_99999",
        source="myhome",
        source_id="test_99999",
        title="იყიდება 2 ოთახიანი ახალი გარემონტებული ბინა დიდ დიღომში",
        price_usd=59000,
        price_gel=159000,
        area_m2=48.0,
        district="დიდი დიღომი",
        street="მირიან მეფის ქუჩა",
        floor="5",
        total_floors=12,
        rooms=2,
        url="https://www.myhome.ge/ka/pr/25815716",
        images=["https://static-statements.tnet.ge/uploads/202608/20260820/statements/PiXrxdX6a873593bc887.webp"]
    )
    
    success = await notifier.send_notification(sample_listing)
    if success:
        print("\n✅ LIVE ALERT SENT SUCCESSFULLY!")
        print("📲 Check your phone's NTFY app now — you should see the alert with photo and details!")
    else:
        print("\n❌ Failed to send notification.")

if __name__ == "__main__":
    asyncio.run(send_live_test_alert())
