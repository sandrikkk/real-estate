import asyncio
import sys
from curl_cffi.requests import AsyncSession
from scrapers.ss_ge import SSGeScraper
from core.models import SearchFilters
from notifier.telegram_bot import TelegramNotifier

sys.stdout.reconfigure(encoding="utf-8")


async def test_live():
    print("=" * 60)
    print("🚀 SS.ge ცოცხალი ტესტირება...")
    print("=" * 60)

    scraper = SSGeScraper(max_pages=1)
    filters = SearchFilters(deal_type="sale", price_min_usd=40000, price_max_usd=120000)

    print("\n1. განცხადებების წამოღება SS.ge-დან...")
    listings = await scraper.fetch_listings(filters)
    print(f"✅ სულ ნაპოვნია: {len(listings)} განცხადება\n")

    if not listings:
        print("❌ განცხადებები ვერ მოიძებნა.")
        return

    sample = listings[:3]
    notifier = TelegramNotifier()

    async with AsyncSession(impersonate="chrome124") as session:
        for i, item in enumerate(sample, 1):
            print(f"--- განცხადება #{i} ---")
            print(f"🔑 ID: {item.id}")
            print(f"🏢 სათაური: {item.title}")
            print(f"💰 ფასი: ${item.price_usd:,.0f} ({item.price_gel:,.0f} ₾)")
            print(f"📍 ლოკაცია: {item.district}")
            print(f"🔗 ლინკი: {item.url}")

            # 2. შევამოწმოთ საიტზე ლინკი რეალურად იხსნება თუ არა
            print("⏳ ლინკის შემოწმება SS.ge-ზე...")
            res = await session.get(item.url, timeout=10)
            if res.status_code == 200 and "განცხადება არ მოიძებნა" not in res.text:
                print("✅ ლინკი ვალიდურია: საიტზე განცხადება წარმატებით გაიხსნა!")
            else:
                print("❌ შეცდომა: საიტზე განცხადება ვერ მოიძებნა!")

            # 3. დეტალების გამდიდრების ტესტი (Owner / Agent)
            print("⏳ დეტალების გადამოწმება (მესაკუთრე / სააგენტო)...")
            details = await scraper.fetch_statement_details(item.source_id)
            if details:
                if details.get("is_owner") is not None:
                    item.is_owner = details["is_owner"]
                if details.get("phone_number"):
                    item.phone_number = details["phone_number"]
                if details.get("condition_name"):
                    item.condition_name = details["condition_name"]

                owner_str = (
                    "მესაკუთრე (Owner)"
                    if item.is_owner is True
                    else ("სააგენტო (Agent)" if item.is_owner is False else "დაუდგენელი")
                )
                phone = item.phone_number or "არ არის"
                cond = item.condition_name or "მითითებული არ არის"
                print(f"👤 სტატუსი: {owner_str} | 📞 ტელ: {phone} | 🛠 მდგომარეობა: {cond}")

            print("\n📱 ტელეგრამის შეტყობინების ფორმატი:")
            formatted_msg = notifier.format_message(item)
            print(formatted_msg)
            print("-" * 60 + "\n")

    print("=" * 60)
    print("🎉 ტესტირება დასრულდა წარმატებით!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_live())
