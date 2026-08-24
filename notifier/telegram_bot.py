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
        if listing.is_bargain and listing.valuation_scale_label in ["დაბალი ფასი", "საშუალოზე იაფი"]:
            header = f"🔥 <b>სარფიანი შეთავაზება! (MyHome: {listing.valuation_scale_label})</b> 🔥"
        elif listing.is_bargain and listing.discount_pct and listing.discount_pct >= 15.0:
            header = f"🔥 <b>სარფიანი შეთავაზება! ({listing.discount_pct}% ფასდაკლება)</b> 🔥"

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
        ]

        # Official Valuation Scale (ღირებულების შკალა - MyHome.ge ოფიციალური შეფასება)
        if listing.valuation_scale_label:
            scale_icon = {
                1: "🟢",
                2: "🟢",
                3: "🟡",
                4: "🟠",
                5: "🔴"
            }.get(listing.valuation_scale_tier, "📈")
            lines.append("")
            lines.append(f"📈 <b>ღირებულების შკალა (MyHome.ge):</b> {scale_icon} <b>{listing.valuation_scale_label}</b>")
            if listing.valuation_scale_visual:
                lines.append(f"<code>{listing.valuation_scale_visual}</code>")
        elif listing.market_median_price_m2:
            district_name = listing.district or listing.city
            lines.append("")
            lines.append(f"📊 <b>საბაზრო შედარება ({district_name}):</b>")
            lines.append(f"• უბნის საბაზრო ეტალონი: <b>${listing.market_median_price_m2:,.0f}/მ²</b>")
            if listing.price_status_label:
                lines.append(f"• შეფასება: <b>{listing.price_status_label}</b>")

        lines.extend([
            "",
            f"🌐 <b>პორტალი:</b> {source_name}",
            f'🔗 <a href="{listing.url}">განცხადების ლინკი</a>'
        ])

        return "\n".join(lines)

    async def send_notification(self, listing: PropertyListing) -> bool:
        message_html = self.format_message(listing)

        # Print to console if enabled or if bot is not configured
        if self.enable_console or not self.bot:
            scale_info = f" [📈 შკალა: {listing.valuation_scale_label}]" if listing.valuation_scale_label else ""
            status_tag = f" [{listing.price_status_label}]" if listing.price_status_label else ""
            alert_text = (
                "\n" + "=" * 60 + "\n"
                f"[NEW LISTING ALERT - {listing.source.upper()} - ID: {listing.source_id}]\n"
                f"Title:    {listing.title}\n"
                f"Price:    ${listing.price_usd:,.0f} (${listing.price_per_m2:,.0f}/m²){status_tag}{scale_info}\n"
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

    async def send_market_report(self, report_text: str) -> bool:
        """Dispatches a text-based market intelligence report to Telegram."""
        if not self.bot or not self.chat_id:
            _safe_print(report_text)
            return True

        try:
            formatted_text = f"<pre>{report_text}</pre>"
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=formatted_text,
                parse_mode=ParseMode.HTML
            )
            return True
        except Exception as e:
            print(f"[Telegram Report Error]: {e}")
            return False
