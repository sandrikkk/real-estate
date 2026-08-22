import asyncio
import json
import re
import sqlite3
from curl_cffi import requests

# Telegram-ის იმპორტი დააკომენტარებულია
# from telegram import Bot

# Config
# TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
# TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"
CHECK_INTERVAL_SECONDS = 180  # 3 წუთი


# DB Initialization
def init_db():
    conn = sqlite3.connect("properties.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS seen_items (
            item_id TEXT PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()


def is_seen(item_id: str) -> bool:
    conn = sqlite3.connect("properties.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM seen_items WHERE item_id = ?", (item_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def mark_as_seen(item_id: str):
    conn = sqlite3.connect("properties.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO seen_items (item_id) VALUES (?)", (item_id,))
    conn.commit()
    conn.close()


def extract_listings_from_html(html_text: str):
    """
    Next.js HTML-იდან <script id="__NEXT_DATA__"> JSON-ის ამოღება
    """
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.DOTALL)
    if not match:
        print("[Error]: __NEXT_DATA__ script tag not found in HTML")
        return []

    try:
        data = json.loads(match.group(1))
        page_props = data.get("props", {}).get("pageProps", {})

        # React Query / Dehydrated State-იდან მონაცემების ამოღება
        queries = page_props.get("dehydratedState", {}).get("queries", [])
        for q in queries:
            query_key = q.get("queryKey", [])
            if isinstance(query_key, list) and len(query_key) > 0:
                key_name = str(query_key[0])
                if key_name in ["statements", "search", "statementList", "listings"]:
                    state_data = q.get("state", {}).get("data", {})
                    if isinstance(state_data, dict):
                        inner = state_data.get("data", {})
                        if isinstance(inner, dict):
                            items = inner.get("data") or inner.get("items")
                            if isinstance(items, list):
                                return items
                        elif isinstance(inner, list):
                            return inner

        # Fallback
        if "items" in page_props and isinstance(page_props["items"], list):
            return page_props["items"]

    except Exception as e:
        print(f"[JSON Parsing Error]: {e}")

    return []


def fetch_search_listings():
    session = requests.Session(impersonate="chrome120")

    # MyHome-ის ძებნის URL (შეიძლება ფილტრების შეცვლა)
    url = "https://www.myhome.ge/ka/s/iyideba-bina-tbilisi?PriceFrom=50000&PriceTo=90000"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ka,en-US;q=0.9,en;q=0.8",
    }

    try:
        response = session.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            return extract_listings_from_html(response.text)
        else:
            print(f"[HTTP Error]: Status Code {response.status_code}")
    except Exception as e:
        print(f"[Network Fetch Error]: {e}")

    return []


async def main():
    init_db()
    # bot = Bot(token=TELEGRAM_BOT_TOKEN)
    print("Monitoring started. Listening for new listings on MyHome...")

    while True:
        listings = fetch_search_listings()
        print(f"\n[Info]: Parsed {len(listings)} listings on current check.")

        for item in listings:
            if not isinstance(item, dict):
                continue

            item_id = str(item.get("id") or item.get("statement_id") or "")
            if not item_id:
                continue

            if not is_seen(item_id):
                mark_as_seen(item_id)

                price_usd = (
                    item.get("price_usd")
                    or item.get("total_price")
                    or item.get("price", {}).get("2", {}).get("price_total", "N/A")
                )
                area = item.get("area_size") or item.get("area", "N/A")
                address = item.get("street_address") or item.get("address") or item.get("urban_name", "თბილისი")
                title = item.get("dynamic_title") or item.get("title", "ახალი ბინა MyHome-ზე")

                link = f"https://www.myhome.ge/ka/pr/{item_id}"

                msg = (
                    f"--- ახალი ბინა (ID: {item_id}) ---\n"
                    f"სათაური: {title}\n"
                    f"ფასი: ${price_usd}\n"
                    f"ფართი: {area} მ²\n"
                    f"მისამართი: {address}\n"
                    f"ბმული: {link}\n"
                )

                # Telegram-ის ნაცვლად კონსოლში ბეჭდვა:
                print(msg)

                # await bot.send_message(
                #     chat_id=TELEGRAM_CHAT_ID,
                #     text=msg,
                #     parse_mode="Markdown"
                # )
                await asyncio.sleep(0.05)

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())