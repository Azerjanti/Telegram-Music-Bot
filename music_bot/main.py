from __future__ import annotations

import logging
import os

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

from music_bot.admin import admin_start, admin_text, show_my_id
from music_bot.config import Settings, load_dotenv_file
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
    env_file = load_dotenv_file()
    if env_file:
        logger.info("Loaded configuration from %s", env_file)
    settings = Settings.from_env()
    logger.info(
        "Bot owner (admin) IDs: %s%s",
        sorted(settings.admin_ids),
        f", usernames: {sorted(settings.admin_usernames)}" if settings.admin_usernames else "",
    )
    # Per task requirements: keep catalogue and favorites on the VPS itself
    # (local SQLite / Postgres), never on Supabase. Supabase is unreliable
    # and caused \"Не удалось сохранить трек в избранное.\" and the catalogue
    # never hitting the cache.
    # We therefore ignore SUPABASE_URL / SUPABASE_KEY even when set.
    if settings.supabase_url or settings.supabase_key:
        logger.info("Supabase env vars are present but ignored - using local VPS storage per requirements")
    database = None
    # Only use Postgres when DATABASE_URL looks like postgres; otherwise SQLite.
    db_url = settings.database_url if settings.database_url and settings.database_url.startswith(("postgres://", "postgresql://")) else None
    settings.music_db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        database = Database(db_url, settings.music_db_path)
        await database.connect()
    except Exception as exc:
        logger.exception("Database connection failed (%s), falling back to local SQLite", exc)
        if database:
            try:
                await database.close()
            except Exception:
                pass
        # force SQLite fallback
        database = Database(None, settings.music_db_path)
        await database.connect()
    logger.info(
        "Using the local VPS catalogue at %s%s",
        settings.music_db_path,
        " (WARNING: /tmp is wiped on restart - set MUSIC_DB_PATH to keep the local top)"
        if str(settings.music_db_path).startswith("/tmp")
        else "",
    )

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


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the failure and always release the pressed inline button.

    Without this a failing callback leaves Telegram showing the 'loading'
    spinner on the button forever, which looks exactly like a dead panel.
    """
    logger.exception("Update failed", exc_info=context.error)
    query = getattr(update, "callback_query", None)
    if query is not None:
        try:
            await query.answer("Ошибка. Попробуйте ещё раз.", show_alert=False)
        except Exception:
            logger.debug("Could not answer the failed callback", exc_info=True)


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_start))
    application.add_handler(CommandHandler(["id", "myid", "whoami"], show_my_id))
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
                r"^(?:🔍 |🎵 |🏆 |👤 |❤️ |📈 |🎤 )?(Поиск песни|Топ 100|По исполнителю|Поиск по исполнителю|Избранное|Локальный топ|Поиск по голосу)$"
            ),
            menu_action,
        )
    )
    # Route any other text message: pending admin input first, then normal search.
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _route_text)
    )
    application.add_error_handler(on_error)


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
    load_dotenv_file()
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    app = create_application(bot_token)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()