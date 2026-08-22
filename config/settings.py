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

    # NTFY Notification Settings
    NTFY_TOPIC: str = ""                       # e.g., "my_tbilisi_apartments_123"
    NTFY_SERVER_URL: str = "https://ntfy.sh"   # Default ntfy.sh public server or self-hosted
    NTFY_AUTH_TOKEN: Optional[str] = None     # Optional Bearer token for protected topics

    # Telegram Bot (Optional fallback)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

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
