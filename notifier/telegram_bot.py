import asyncio
import html
import re
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

        if TELEGRAM_AVAILABLE and self.bot_token:
            try:
                self.bot = Bot(token=self.bot_token)
            except Exception as e:
                print(f"[Telegram Notifier Warning]: Failed to initialize Telegram Bot: {e}")

    def format_message(self, listing: PropertyListing) -> str:
        # Header: [Tag if applicable] [Price in USD] | [Area sq.m] | [$/sq.m]
        price_info = f"${listing.price_usd:,.0f} | {listing.area_m2} მ² | ${listing.price_per_m2:,.0f}/მ²"
        if listing.deal_tag:
            header = f"<b>{html.escape(listing.deal_tag)}</b>\n💰 <b>{price_info}</b>"
        elif listing.is_hot_deal:
            header = f"🚨 <b>HOT DEAL</b>\n💰 <b>{price_info}</b>"
        else:
            header = f"🏠 <b>{price_info}</b>"

        # Condition: [ახალი გარემონტებული / მწვანე კარკასი / etc.]
        safe_condition = html.escape(listing.condition_name or 'მითითებული არ არის')
        condition_line = f"🛠 <b>მდგომარეობა:</b> {safe_condition}"

        # Location: District / Street / Metro proximity
        loc_parts = []
        if listing.district:
            loc_parts.append(f"<b>{html.escape(listing.district)}</b>")
        elif listing.city:
            loc_parts.append(f"<b>{html.escape(listing.city)}</b>")

        if listing.street:
            loc_parts.append(html.escape(listing.street))

        if listing.metro_station_name:
            loc_parts.append(f"🚇 <b>მ. {html.escape(listing.metro_station_name)}</b>")

        location_line = f"📍 <b>ლოკაცია:</b> {' | '.join(loc_parts)}" if loc_parts else "📍 <b>ლოკაცია:</b> თბილისი"

        # Details: Owner vs Agent | Floor / Total Floors
        if listing.is_owner is True:
            owner_str = "👤 <b>მესაკუთრე (Owner)</b>"
        elif listing.is_owner is False:
            owner_str = "👤 <b>სააგენტო (Agent)</b>"
        else:
            owner_str = "👤 <b>განმცხადებელი</b>"

        if listing.floor:
            safe_floor = html.escape(str(listing.floor))
            if listing.total_floors:
                floor_str = f"🏢 სართული: <b>{safe_floor}/{listing.total_floors}</b>"
            else:
                floor_str = f"🏢 სართული: <b>{safe_floor}</b>"
        else:
            floor_str = "🏢 სართული: <b>-</b>"

        details_parts = [owner_str, floor_str]
        if listing.rooms or listing.bedrooms:
            r_parts = []
            if listing.rooms:
                r_parts.append(f"{listing.rooms} ოთახი")
            if listing.bedrooms:
                r_parts.append(f"{listing.bedrooms} საძინებელი")
            details_parts.append(f"🚪 {', '.join(r_parts)}")

        details_line = " | ".join(details_parts)

        # Direct listing link
        source_name = {
            "myhome": "MyHome.ge",
            "ss_ge": "SS.ge",
            "area_ge": "Area.ge"
        }.get(listing.source, listing.source.upper())
        safe_url = html.escape(listing.url)
        link_line = f'🔗 <a href="{safe_url}">განცხადების ლინკი ({source_name})</a>'

        # Phone number for one-tap calling
        if listing.phone_number:
            clean_digits = re.sub(r"\D", "", listing.phone_number)
            safe_phone = html.escape(listing.phone_number)
            if "*" not in listing.phone_number:
                if len(clean_digits) == 9 and clean_digits.startswith("5"):
                    tel_url = f"+995{clean_digits}"
                elif len(clean_digits) == 12 and clean_digits.startswith("995"):
                    tel_url = f"+{clean_digits}"
                else:
                    tel_url = clean_digits
                phone_line = f'📞 <a href="tel:{tel_url}">{safe_phone}</a>'
            else:
                phone_line = f'📞 <code>{safe_phone}</code> <i>(ნომრის სანახავად გადადით ლინკზე)</i>'
        else:
            phone_line = '📞 <i>ტელეფონი მითითებულია განცხადებაში</i>'

        lines = [
            header,
            "",
            condition_line,
            location_line,
            details_line,
        ]

        # Valuation scale / analytics if present
        if listing.valuation_scale_label:
            safe_scale_vis = html.escape(listing.valuation_scale_visual or '')
            safe_scale_lbl = html.escape(listing.valuation_scale_label)
            lines.append("")
            lines.append(f"📈 <b>MyHome შეფასება:</b> {safe_scale_vis} <b>{safe_scale_lbl}</b>")
        elif listing.market_median_price_m2:
            lines.append("")
            lines.append(f"📊 <b>საბაზრო შედარება:</b> უბნის ეტალონი <b>${listing.market_median_price_m2:,.0f}/მ²</b>")

        lines.extend([
            "",
            link_line,
            phone_line,
        ])

        return "\n".join(lines)

    async def send_notification(self, listing: PropertyListing, target_chat_id: Optional[str] = None) -> bool:
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

        recipient_id = target_chat_id or self.chat_id
        if not self.bot or not recipient_id:
            return True

        try:
            # Send photo with caption if image is available
            if listing.images and len(listing.images) > 0:
                first_image = listing.images[0]
                try:
                    await self.bot.send_photo(
                        chat_id=recipient_id,
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
                chat_id=recipient_id,
                text=message_html,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            await asyncio.sleep(0.2)
            return True
        except Exception as e:
            print(f"[Telegram Notification Error for {recipient_id}]: {e}")
            return False

    async def send_market_report(self, report_text: str) -> bool:
        """Dispatches a text-based market intelligence report to Telegram."""
        if not self.bot or not self.chat_id:
            _safe_print(report_text)
            return True

        try:
            formatted_text = f"<pre>{html.escape(report_text)}</pre>"
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=formatted_text,
                parse_mode=ParseMode.HTML
            )
            return True
        except Exception as e:
            print(f"[Telegram Report Error]: {e}")
            return False
