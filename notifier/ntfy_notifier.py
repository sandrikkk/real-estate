import asyncio
import json
import sys
from typing import Optional
import aiohttp

from core.models import PropertyListing


def _safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
            sys.stdout.buffer.flush()
        else:
            print(text.encode("ascii", errors="replace").decode("ascii"))


class NtfyNotifier:
    def __init__(
        self,
        topic: str = "",
        server_url: str = "https://ntfy.sh",
        auth_token: Optional[str] = None,
        enable_console: bool = True
    ):
        self.topic = topic.strip() if topic else ""
        self.server_url = server_url.rstrip("/")
        self.auth_token = auth_token.strip() if auth_token else None
        self.enable_console = enable_console

    def format_title(self, listing: PropertyListing) -> str:
        district_part = f" ({listing.district})" if listing.district else ""
        if listing.is_bargain and listing.discount_pct:
            return f"🔥 სარფიანი შეთავაზება: ${listing.price_usd:,.0f}{district_part} [-{listing.discount_pct}%]"
        return f"🏠 ახალი ბინა: ${listing.price_usd:,.0f}{district_part}"

    def format_body(self, listing: PropertyListing) -> str:
        source_name = {
            "myhome": "MyHome.ge",
            "ss_ge": "SS.ge",
            "area_ge": "Area.ge"
        }.get(listing.source, listing.source.upper())

        gel_price_str = f" (~{int(listing.price_gel):,} ₾)" if listing.price_gel else ""
        location_str = ", ".join(filter(None, [listing.city, listing.district, listing.street]))

        floor_str = ""
        if listing.floor:
            floor_str = f" | სართული: {listing.floor}"
            if listing.total_floors:
                floor_str += f"/{listing.total_floors}"

        rooms_str = ""
        if listing.rooms or listing.bedrooms:
            rooms_count = listing.rooms or listing.bedrooms
            rooms_str = f" | ოთახები: {rooms_count}"

        lines = [
            listing.title,
            "",
            f"💰 ფასი: ${listing.price_usd:,.0f}{gel_price_str}",
            f"📐 ფართობი: {listing.area_m2} მ² (${listing.price_per_m2:,.0f}/მ²)",
            f"📍 მისამართი: {location_str}{floor_str}{rooms_str}",
            f"🌐 პორტალი: {source_name}",
        ]
        return "\n".join(lines)

    async def send_notification(self, listing: PropertyListing) -> bool:
        title = self.format_title(listing)
        body = self.format_body(listing)

        # Print to console if enabled or if NTFY topic is not set
        if self.enable_console or not self.topic:
            bargain_tag = f" [🔥 BARGAIN: -{listing.discount_pct}%]" if listing.is_bargain else ""
            alert_text = (
                "\n" + "=" * 60 + "\n"
                f"[NTFY ALERT - {listing.source.upper()} - ID: {listing.source_id}]\n"
                f"Title:    {title}{bargain_tag}\n"
                f"Price:    ${listing.price_usd:,.0f} (${listing.price_per_m2:,.0f}/m²)\n"
                f"Location: {listing.city}, {listing.district or 'N/A'}, {listing.street or ''}\n"
                f"URL:      {listing.url}\n"
                + "=" * 60
            )
            _safe_print(alert_text)

        if not self.topic:
            return True

        # NTFY JSON Payload
        tags = ["house", "fire"] if listing.is_bargain else ["house", "moneybag"]
        priority = 4 if listing.is_bargain else 3

        payload = {
            "topic": self.topic,
            "title": title,
            "message": body,
            "priority": priority,
            "tags": tags,
            "click": listing.url,
            "actions": [
                {
                    "action": "view",
                    "label": "🔗 განცხადების გახსნა",
                    "url": listing.url,
                    "clear": True
                }
            ]
        }

        # Attach image if available
        if listing.images and len(listing.images) > 0:
            payload["attach"] = listing.images[0]

        headers = {
            "Content-Type": "application/json; charset=utf-8"
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        target_url = f"{self.server_url}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    target_url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        await asyncio.sleep(0.1)
                        return True
                    else:
                        error_text = await resp.text()
                        print(f"[NTFY Notification HTTP {resp.status} Error]: {error_text}")
        except Exception as e:
            print(f"[NTFY Notification Network Error]: {e}")

        return False
