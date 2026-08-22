import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import asyncio
from core.models import PropertyListing
from notifier.telegram_bot import TelegramNotifier
from config import settings

async def send_live_telegram():
    print(f"📡 Sending Live Test Listing to Telegram: Chat ID {settings.TELEGRAM_CHAT_ID}...")
    
    bot = TelegramNotifier(
        bot_token=settings.TELEGRAM_BOT_TOKEN,
        chat_id=settings.TELEGRAM_CHAT_ID,
        enable_console=True
    )
    
    sample_listing = PropertyListing(
        id="myhome_25564781",
        source="myhome",
        source_id="25564781",
        title="იყიდება 2 ოთახიანი ახალი გარემონტებული ბინა დიდ დიღომში",
        price_usd=75000,
        price_gel=202500,
        area_m2=43.0,
        district="დიდი დიღომი",
        street="პეტრე იბერის ქ.",
        floor="4",
        total_floors=9,
        rooms=2,
        url="https://www.myhome.ge/ka/pr/25564781",
        images=["https://static-statements.tnet.ge/uploads/202608/20260820/statements/PiXrxdX6a873593bc887.webp"]
    )
    
    success = await bot.send_notification(sample_listing)
    if success:
        print("\n✅ TELEGRAM MESSAGE SENT SUCCESSFULLY TO YOUR PHONE!")
    else:
        print("\n❌ Failed to send Telegram message.")

if __name__ == "__main__":
    asyncio.run(send_live_telegram())
