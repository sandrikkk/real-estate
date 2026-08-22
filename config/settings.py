import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Telegram Bot Notifier (Exclusive)
    TELEGRAM_BOT_TOKEN: str = "REDACTED_TELEGRAM_BOT_TOKEN"
    TELEGRAM_CHAT_ID: str = "REDACTED_CHAT_ID"

    # Proxy / Cloudflare Worker / ScraperAPI (For Cloud Runners / GitHub Actions)
    CLOUDFLARE_PROXY_URL: str = "https://your-worker.your-subdomain.workers.dev"
    SCRAPER_API_KEY: str = ""

    # Polling & Performance
    CHECK_INTERVAL_SECONDS: int = 180
    REQUEST_TIMEOUT_SECONDS: int = 15
    CONCURRENT_SCRAPERS: int = 3
    RATE_LIMIT_DELAY_SECONDS: float = 0.5

    # Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATABASE_PATH: str = str(Path(__file__).resolve().parent.parent / "data" / "properties.db")
    FILTERS_CONFIG_PATH: str = str(Path(__file__).resolve().parent.parent / "config" / "filters.json")

    # Logging
    LOG_LEVEL: str = "INFO"
    ENABLE_CONSOLE_NOTIFICATIONS: bool = True

    # Analytics
    BARGAIN_DISCOUNT_THRESHOLD_PCT: float = 15.0  # Flag if >= 15% cheaper than district avg


settings = Settings()
