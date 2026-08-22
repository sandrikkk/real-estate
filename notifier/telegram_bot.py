import asyncio
import sys
from typing import Optional
from core.models import PropertyListing

try:
    from telegram import Bot
    from telegram.constants import ParseMode
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


def _safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        # Fallback to UTF-8 buffer write
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
            sys.stdout.buffer.flush()
        else:
            print(text.encode("ascii", errors="replace").decode("ascii"))


class TelegramNotifier:
    def __init__(self, bot_token: str = "", chat_id: str = "", enable_console: bool = True):
        self.bot_token = bot_token.strip() if bot_token else ""
        self.chat_id = chat_id.strip() if chat_id else ""
        self.enable_console = enable_console
        self.bot: Optional[Bot] = None

        if TELEGRAM_AVAILABLE and self.bot_token and self.chat_id:
            try:
                self.bot = Bot(token=self.bot_token)
            except Exception as e:
                print(f"[Telegram Notifier Warning]: Failed to initialize Telegram Bot: {e}")

    def format_message(self, listing: PropertyListing) -> str:
        source_name = {
            "myhome": "MyHome.ge",
            "ss_ge": "SS.ge",
            "area_ge": "Area.ge"
        }.get(listing.source, listing.source.upper())

        header = "🏠 <b>ახალი უძრავი ქონება!</b>"
        if listing.is_bargain and listing.discount_pct:
            header = f"🔥 <b>სარფიანი შეთავაზება! ({listing.discount_pct}% ფასდაკლება საბაზროზე)</b> 🔥"

        gel_price_str = f" (~{int(listing.price_gel):,} ₾)" if listing.price_gel else ""
        location_str = ", ".join(filter(None, [listing.city, listing.district, listing.street]))

        floor_str = ""
        if listing.floor:
            floor_str = f" | 🏢 სართული: {listing.floor}"
            if listing.total_floors:
                floor_str += f"/{listing.total_floors}"

        rooms_str = ""
        if listing.rooms or listing.bedrooms:
            rooms_count = listing.rooms or listing.bedrooms
            rooms_str = f" | 🛏 ოთახები: {rooms_count}"

        lines = [
            header,
            f"<b>{listing.title}</b>",
            "",
            f"💰 <b>ფასი:</b> ${listing.price_usd:,.0f}{gel_price_str}",
            f"📐 <b>ფართობი:</b> {listing.area_m2} მ² (<b>${listing.price_per_m2:,.0f}/მ²</b>)",
            f"📍 <b>მისამართი:</b> {location_str}{floor_str}{rooms_str}",
            f"🌐 <b>პორტალი:</b> {source_name}",
            "",
            f'🔗 <a href="{listing.url}">განცხადების ლინკი</a>'
        ]

        return "\n".join(lines)

    async def send_notification(self, listing: PropertyListing) -> bool:
        message_html = self.format_message(listing)

        # Print to console if enabled or if bot is not configured
        if self.enable_console or not self.bot:
            bargain_tag = f" [🔥 BARGAIN: -{listing.discount_pct}%]" if listing.is_bargain else ""
            alert_text = (
                "\n" + "=" * 60 + "\n"
                f"[NEW LISTING ALERT - {listing.source.upper()} - ID: {listing.source_id}]\n"
                f"Title:    {listing.title}\n"
                f"Price:    ${listing.price_usd:,.0f} (${listing.price_per_m2:,.0f}/m²){bargain_tag}\n"
                f"Location: {listing.city}, {listing.district or 'N/A'}, {listing.street or ''}\n"
                f"URL:      {listing.url}\n"
                + "=" * 60
            )
            _safe_print(alert_text)

        if not self.bot or not self.chat_id:
            return True

        try:
            # Send photo with caption if image is available
            if listing.images and len(listing.images) > 0:
                first_image = listing.images[0]
                try:
                    await self.bot.send_photo(
                        chat_id=self.chat_id,
                        photo=first_image,
                        caption=message_html,
                        parse_mode=ParseMode.HTML
                    )
                    await asyncio.sleep(0.2)
                    return True
                except Exception as img_err:
                    # Fallback to plain text message if image fails to load
                    print(f"[Telegram Image Send Error]: {img_err}, falling back to text...")

            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message_html,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            await asyncio.sleep(0.2)
            return True
        except Exception as e:
            print(f"[Telegram Notification Error]: {e}")
            return False
