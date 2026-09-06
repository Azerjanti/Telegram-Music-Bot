from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from music_bot.database import Database, Song
from music_bot.downloader import YouTubeProvider

logger = logging.getLogger(__name__)


class MusicService:
    def __init__(
        self,
        database: Database,
        provider: YouTubeProvider | None,
        max_file_mb: int,
        max_concurrent_downloads: int,
    ) -> None:
        self.database = database
        self.provider = provider
        self.max_file_mb = max_file_mb
        self.download_slots = asyncio.Semaphore(max_concurrent_downloads)

    async def send_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> Song | None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return None

        await context.bot.send_chat_action(chat.id, ChatAction.TYPING)
        cached = await self.database.search_song(query)
        if cached:
            await self._send_cached(message, cached)
            await self.database.increment_play_count(cached.id)
            return cached

        if not self.provider:
            await message.reply_text(
                "Песня пока не найдена в локальном каталоге. Загрузите аудиофайл в бот или включите разрешённый источник аудио."
            )
            return None

        async with self.download_slots:
            try:
                downloaded = await self.provider.download(query)
                if downloaded.path.stat().st_size > self.max_file_mb * 1024 * 1024:
                    raise RuntimeError("Файл превышает лимит Telegram")
                with downloaded.path.open("rb") as audio:
                    sent = await message.reply_audio(
                        audio=audio,
                        title=downloaded.metadata.title,
                        performer=downloaded.metadata.artist,
                        caption="Сохранено в каталоге бота",
                    )
            except Exception:
                logger.exception("Audio download failed for query=%r", query)
                await message.reply_text("Песня не найдена. Попробуйте другой запрос.")
                return None
            finally:
                if "downloaded" in locals() and downloaded.path.exists():
                    downloaded.path.unlink(missing_ok=True)

        if not sent.audio:
            return None
        song = await self.database.save_song(
            title=downloaded.metadata.title,
            artist=downloaded.metadata.artist,
            file_id=sent.audio.file_id,
            source_url=downloaded.metadata.source_url,
        )
        await self.database.increment_play_count(song.id)
        await message.reply_text(
            f"Найдено: {song.title} — {song.artist}",
            reply_markup=self.track_actions(song),
        )
        return song

    async def _send_cached(self, message, song: Song) -> None:
        await message.reply_audio(
            audio=song.file_id,
            title=song.title,
            performer=song.artist,
            reply_markup=self.track_actions(song),
        )

    @staticmethod
    def track_actions(song: Song) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Песни исполнителя", callback_data=f"artist:{song.artist[:50]}")],
                [InlineKeyboardButton("Следующая", callback_data="next")],
            ]
        )

    async def send_song(self, update: Update, song: Song) -> None:
        message = update.effective_message
        if message:
            await message.reply_audio(
                audio=song.file_id,
                title=song.title,
                performer=song.artist,
                reply_markup=self.track_actions(song),
            )
            await self.database.increment_play_count(song.id)

    @staticmethod
    def cleanup_cache(cache_dir: Path) -> None:
        for path in cache_dir.glob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)