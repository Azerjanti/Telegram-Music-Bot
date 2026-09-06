from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str | None
    port: int
    cache_dir: Path
    enable_ytdlp: bool
    enable_shazam: bool
    max_concurrent_downloads: int
    download_retries: int
    max_telegram_file_mb: int

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN must be set in Replit Secrets")

        cache_dir = Path(os.getenv("AUDIO_CACHE_DIR", "music_bot/.cache/audio"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            bot_token=token,
            database_url=os.getenv("DATABASE_URL"),
            port=int(os.getenv("PORT", "8000")),
            cache_dir=cache_dir,
            enable_ytdlp=_as_bool("ENABLE_YTDLP_DOWNLOADS", True),
            enable_shazam=_as_bool("ENABLE_SHAZAM", True),
            max_concurrent_downloads=max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))),
            download_retries=max(1, int(os.getenv("DOWNLOAD_RETRIES", "3"))),
            max_telegram_file_mb=max(1, int(os.getenv("MAX_TELEGRAM_FILE_MB", "50"))),
        )