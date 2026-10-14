import json
import logging
import time
from pathlib import Path
from typing import List, Optional
import urllib.request
import urllib.error

from config import settings
from core.models import UserSubscription

logger = logging.getLogger(__name__)

USER_CACHE_FILE = Path(settings.BASE_DIR) / "data" / "active_users_cache.json"


def save_cached_users(users: List[UserSubscription]):
    """Persists active users locally so rate limits or network blips don't reset filters."""
    try:
        USER_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [u.model_dump() for u in users]
        with open(USER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to cache active users: {e}")


def load_cached_users() -> List[UserSubscription]:
    """Loads active users from local cache if available."""
    if not USER_CACHE_FILE.exists():
        return []
    try:
        with open(USER_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            users = []
            for item in data:
                u = UserSubscription(**item)
                if u.is_active and u.chat_id:
                    users.append(u)
            return users
    except Exception as e:
        logger.warning(f"Failed to read cached active users: {e}")
        return []


def get_default_fallback_user() -> UserSubscription:
    """
    Constructs a fallback UserSubscription based on the local filters.json
    and the primary TELEGRAM_CHAT_ID from settings.
    """
    path = Path(settings.FILTERS_CONFIG_PATH)
    districts = []
    deal_type = "sale"
    price_min = 10000.0
    price_max = 100000.0
    rent_price_min = 300.0
    rent_price_max = 1500.0
    area_min = 10.0
    area_max = 100.0
    rooms_min = 2
    owner_type = "all"

    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                deal_type = data.get("deal_type", deal_type)
                price_min = data.get("price_min_usd", price_min)
                price_max = data.get("price_max_usd", price_max)
                rent_price_min = data.get("rent_price_min_usd", rent_price_min)
                rent_price_max = data.get("rent_price_max_usd", rent_price_max)
                area_min = data.get("area_min_m2", area_min)
                area_max = data.get("area_max_m2", area_max)
                rooms_min = data.get("rooms_min", rooms_min)
                owner_type = data.get("owner_type", owner_type) or "all"
                districts = data.get("whitelist_districts") or data.get("target_districts") or []
        except Exception as e:
            logger.warning(f"Failed to read local fallback filters: {e}")

    return UserSubscription(
        chat_id=settings.TELEGRAM_CHAT_ID or "default_user",
        username="admin",
        first_name="Admin",
        deal_type=deal_type,
        price_min_usd=price_min,
        price_max_usd=price_max,
        rent_price_min_usd=rent_price_min,
        rent_price_max_usd=rent_price_max,
        area_min_m2=area_min,
        area_max_m2=area_max,
        rooms_min=rooms_min,
        districts=districts,
        owner_type=owner_type,
        is_active=True,
    )


def fetch_active_users(
    worker_url: Optional[str] = None, sync_key: Optional[str] = None
) -> List[UserSubscription]:
    """
    Fetches the active user list from the Cloudflare Worker KV storage.
    If the worker is not configured or an error occurs, safely falls back
    to the single admin user defined in settings / filters.json.
    """
    base_url = (
        (worker_url if worker_url is not None else settings.CLOUDFLARE_PROXY_URL or "")
        .strip()
        .rstrip("/")
    )
    key = (sync_key if sync_key is not None else settings.CLOUDFLARE_SYNC_KEY or "").strip()

    # If worker is not configured or multi-user is disabled, return fallback
    if not base_url or not getattr(settings, "ENABLE_MULTI_USER", True):
        return [get_default_fallback_user()]

    api_url = f"{base_url}/api/users?key={key}&_t={int(time.time())}"
    headers = {
        "User-Agent": "RealEstate-Orchestrator/2.0",
        "Accept": "application/json",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }
    if key:
        headers["X-Sync-Key"] = key

    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                content = resp.read().decode("utf-8")
                raw_users = json.loads(content)
                users = []
                for item in raw_users:
                    try:
                        user = UserSubscription(**item)
                        if user.is_active and user.chat_id:
                            users.append(user)
                    except Exception as parse_err:
                        logger.warning(f"Error parsing user object {item}: {parse_err}")

                if users:
                    save_cached_users(users)
                    return users
                else:
                    logger.info("Cloudflare Worker returned 0 active users, checking cache.")
                    cached = load_cached_users()
                    if cached:
                        return cached
                    return [get_default_fallback_user()]
            else:
                logger.warning(f"Cloudflare Worker returned HTTP {resp.status}, checking cache.")
    except Exception as e:
        logger.warning(
            f"Could not reach Cloudflare Worker users API ({api_url}): {e}. Using cached or fallback filters."
        )

    cached = load_cached_users()
    if cached:
        logger.info("Using cached active users (%d subscribers).", len(cached))
        return cached

    return [get_default_fallback_user()]
