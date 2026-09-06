from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from music_bot.config import Settings
from music_bot.database import Database
from music_bot.downloader import YouTubeProvider
from music_bot.handlers.callbacks import callback_query
from music_bot.handlers.search import text_search
from music_bot.handlers.start import menu_action, start
from music_bot.handlers.voice import voice_search
from music_bot.health import run_health_server
from music_bot.services import MusicService
from music_bot.top_charts import SpotifyProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


async def run() -> None:
    settings = Settings.from_env()
    database = Database(settings.database_url, Path("music_bot/.cache/music.sqlite3"))
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
    application = Application.builder().token(settings.bot_token).build()
    application.bot_data["music_service"] = service
    application.bot_data["enable_shazam"] = settings.enable_shazam
    spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if spotify_client_id and spotify_client_secret:
        application.bot_data["spotify_provider"] = SpotifyProvider(
            spotify_client_id,
            spotify_client_secret,
            os.getenv("SPOTIFY_TOP_PLAYLIST_ID"),
        )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_query))
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_search))
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & filters.Regex(r"^(Поиск песни|Топ 100|Поиск по исполнителю|Локальный топ|Поиск по голосу)$"),
            menu_action,
        )
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_search))

    ready = False
    health_server = await run_health_server(settings.port, lambda: ready)
    try:
        await application.initialize()
        await application.start()
        if not application.updater:
            raise RuntimeError("Telegram updater is unavailable")
        await application.updater.start_polling(allowed_updates=["message", "callback_query"])
        ready = True
        logger.info("Telegram music bot is running on health port %s", settings.port)
        await asyncio.Event().wait()
    finally:
        ready = False
        health_server.close()
        await health_server.wait_closed()
        if application.updater and application.updater.running:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await database.close()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass