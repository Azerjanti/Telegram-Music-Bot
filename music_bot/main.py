from __future__ import annotations

import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from music_bot.admin import admin_start, admin_text
from music_bot.config import Settings
from music_bot.database import Database, SupabaseDatabase
from music_bot.downloader import YouTubeProvider
from music_bot.handlers.callbacks import callback_query
from music_bot.handlers.favorites import show_favorites
from music_bot.handlers.search import text_search
from music_bot.handlers.start import menu_action, start
from music_bot.handlers.voice import voice_search
from music_bot.health import run_health_server, stop_health_server
from music_bot.services import MusicService
from music_bot.top_charts import SpotifyProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


async def initialize(application: Application) -> None:
    settings = Settings.from_env()
    database = None
    if settings.supabase_url and settings.supabase_key:
        try:
            database = SupabaseDatabase(settings.supabase_url, settings.supabase_key)
            await database.connect()
            logger.info("Using Supabase songs cache")
        except Exception:
            logger.exception("Supabase cache unavailable; using local database fallback")
            if database:
                await database.close()
            database = None

    if database is None:
        database = Database(settings.database_url, Path("/tmp/music-bot/music.sqlite3"))
        await database.connect()

    provider = (
        YouTubeProvider(settings.cache_dir, settings.download_retries)
        if settings.enable_ytdlp
        else None
    )
    service = MusicService(
        database=database,
        provider=provider,
        max_file_mb=settings.max_telegram_file_mb,
        max_concurrent_downloads=settings.max_concurrent_downloads,
    )
    application.bot_data["music_service"] = service
    application.bot_data["database"] = database
    application.bot_data["settings"] = settings
    application.bot_data["enable_shazam"] = settings.enable_shazam

    # Pre-register any admins from ADMIN_IDS so the panel works immediately even
    # before those users press /start.
    for admin_id in settings.admin_ids:
        try:
            await database.set_db_admin(admin_id, True)
            await database.register_user(user_id=admin_id)
        except Exception:
            logger.debug("Could not pre-register admin %s", admin_id, exc_info=True)

    spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if spotify_client_id and spotify_client_secret:
        application.bot_data["spotify_provider"] = SpotifyProvider(
            spotify_client_id,
            spotify_client_secret,
            os.getenv("SPOTIFY_TOP_PLAYLIST_ID"),
        )

    application.bot_data["health_server"] = await run_health_server(settings.port)
    logger.info("Telegram music bot is running on health port %s", settings.port)


async def shutdown(application: Application) -> None:
    await stop_health_server(application.bot_data.get("health_server"))
    database = application.bot_data.get("database")
    if database:
        await database.close()


async def _route_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pending admin text input (add channel / ban target / add admin),
    otherwise fall back to the regular music search."""
    if await admin_text(update, context):
        return
    await text_search(update, context)


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_start))
    application.add_handler(CommandHandler(["like", "favorites"], show_favorites))
    application.add_handler(CallbackQueryHandler(callback_query))
    application.add_handler(
        MessageHandler(
            filters.VOICE
            | filters.AUDIO
            | filters.VIDEO
            | filters.VIDEO_NOTE
            | filters.Document.AUDIO
            | filters.Document.VIDEO,
            voice_search,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & filters.Regex(
                r"^(Поиск песни|Топ 100|По исполнителю|Поиск по исполнителю|Избранное|Локальный топ|Поиск по голосу)$"
            ),
            menu_action,
        )
    )
    # Route any other text message: pending admin input first, then normal search.
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _route_text)
    )


def create_application(bot_token: str) -> Application:
    application = (
        ApplicationBuilder()
        .token(bot_token)
        .post_init(initialize)
        .post_shutdown(shutdown)
        .build()
    )
    register_handlers(application)
    return application


def run() -> None:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    app = create_application(bot_token)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()