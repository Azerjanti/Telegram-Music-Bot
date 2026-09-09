from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_dotenv_file() -> Path | None:
    """Load a ``.env`` file (if python-dotenv is installed).

    Existing environment variables always win, so Replit Secrets / Render env
    vars keep priority over the file.  Returns the file that was used.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional
        return None
    for candidate in (Path.cwd() / ".env", _project_root() / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate
    return None


def _as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().strip("\"'`").lower() in {"1", "true", "yes", "on"}


# Primary bot owner Telegram user id (used when ADMIN_ID / ADMIN_IDS are unset).
# This is the numeric Telegram ID of the bot owner - the same value you can put
# into ADMIN_ID / ADMIN_IDS in Replit Secrets, Render or a local .env file.
DEFAULT_ADMIN_ID = 8377297659

_SPLIT = re.compile(r"[,;\s]+")


def _clean_token(part: str) -> str:
    """Normalise one entry of ADMIN_ID/ADMIN_IDS.

    Accepts the value with surrounding quotes, backticks, spaces, a trailing
    comma or an inline ``# comment`` - all of which people paste into .env files
    and which used to make the whole value be silently ignored.
    """
    text = part.strip()
    text = text.split("#", 1)[0].strip()
    return text.strip().strip(",;").strip().strip("\"'`").strip()


def _as_int_list(*names: str) -> set[int]:
    """Parse a comma/space separated list of numeric Telegram IDs from env vars."""
    ids: set[int] = set()
    for name in names:
        raw = os.getenv(name, "") or ""
        for part in _SPLIT.split(raw.replace(";", ",").replace("\n", ",")):
            token = _clean_token(part)
            if not token:
                continue
            if token.startswith("@"):
                continue  # usernames are handled by _as_username_list
            if token.lstrip("+").isdigit():
                ids.add(int(token.lstrip("+")))
    return ids


def _as_username_list(*names: str) -> set[str]:
    """Parse ADMIN_USERNAME / ADMIN_USERNAMES (@name, name, comma separated)."""
    usernames: set[str] = set()
    for name in names:
        raw = os.getenv(name, "") or ""
        for part in _SPLIT.split(raw.replace(";", ",").replace("\n", ",")):
            token = _clean_token(part).lstrip("@").lower()
            if token and not token.isdigit():
                usernames.add(token)
    return usernames


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
    # Where the SQLite catalogue lives. For VPS persistence use a non-/tmp path
    # (e.g. data/music.sqlite3). /tmp is wiped on every Replit/Render restart.
    music_db_path: Path = Path("data/music.sqlite3")
    # Telegram numeric IDs of the primary bot owners (the people who can open the
    # admin panel and transfer admin rights to others).
    admin_ids: frozenset[int] = frozenset()
    # Optional usernames, for owners who do not know their numeric ID yet.
    admin_usernames: frozenset[str] = field(default_factory=frozenset)

    def is_owner_id(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def is_owner_username(self, username: str | None) -> bool:
        return bool(username) and username.lower().lstrip("@") in self.admin_usernames

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv_file()
        token = os.getenv("BOT_TOKEN", "").strip().strip("\"'`")
        if not token:
            raise RuntimeError("BOT_TOKEN must be set in Replit Secrets")

        cache_dir = Path(os.getenv("AUDIO_CACHE_DIR", "/tmp/music-bot/audio"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Owner id: ADMIN_ID (single) and/or ADMIN_IDS (comma-separated).
        configured_ids = _as_int_list("ADMIN_ID", "ADMIN_IDS")
        admin_ids = configured_ids or {DEFAULT_ADMIN_ID}
        admin_usernames = _as_username_list("ADMIN_USERNAME", "ADMIN_USERNAMES")
        if configured_ids:
            logger.info("Admin Telegram IDs from configuration: %s", sorted(configured_ids))
        else:
            logger.info(
                "ADMIN_ID/ADMIN_IDS not set - using the built-in owner ID %s",
                DEFAULT_ADMIN_ID,
            )
        if admin_usernames:
            logger.info("Admin usernames from configuration: %s", sorted(admin_usernames))
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
            music_db_path=Path(
                os.getenv("MUSIC_DB_PATH", "data/music.sqlite3").strip().strip("\"'`")
            ),
            admin_ids=frozenset(admin_ids),
            admin_usernames=frozenset(admin_usernames),
        )
