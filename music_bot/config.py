from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int_list(name: str) -> set[int]:
    """Parse a comma/space separated list of numeric IDs from an env var."""
    raw = os.getenv(name, "")
    ids: set[int] = set()
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str | None
    supabase_url: str | None
    supabase_key: str | None
    port: int
    cache_dir: Path
    enable_ytdlp: bool
    enable_shazam: bool
    max_concurrent_downloads: int
    download_retries: int
    max_telegram_file_mb: int
    # Telegram numeric IDs of the primary bot owners (the people who can open the
    # admin panel and transfer admin rights to others).
    admin_ids: frozenset[int]

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN must be set in Replit Secrets")

        cache_dir = Path(os.getenv("AUDIO_CACHE_DIR", "/tmp/music-bot/audio"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Default owner id kept for convenience; override via ADMIN_IDS env var
        # (comma separated list), e.g. ADMIN_IDS=8490020175.
        admin_ids = _as_int_list("ADMIN_IDS") or {8490020175}
        return cls(
            bot_token=token,
            database_url=os.getenv("DATABASE_URL"),
            supabase_url=os.getenv("SUPABASE_URL"),
            supabase_key=os.getenv("SUPABASE_KEY"),
            port=int(os.getenv("PORT", "8000")),
            cache_dir=cache_dir,
            enable_ytdlp=_as_bool("ENABLE_YTDLP_DOWNLOADS", True),
            enable_shazam=_as_bool("ENABLE_SHAZAM", True),
            max_concurrent_downloads=max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))),
            download_retries=max(1, int(os.getenv("DOWNLOAD_RETRIES", "3"))),
            max_telegram_file_mb=max(1, int(os.getenv("MAX_TELEGRAM_FILE_MB", "50"))),
            admin_ids=frozenset(admin_ids),
        )