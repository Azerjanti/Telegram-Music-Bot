from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from music_bot.database import Database, Song
from music_bot.downloader import SearchResult, YouTubeProvider

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

    async def show_search_results(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        query: str,
        limit: int = 10,
    ) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        await message.reply_text(f"🔍 Поиск по запросу: {query}")
        if _is_media_url(query):
            await self.send_query(update, context, query, source_url=query)
            return

        cached = await self.database.search_song(query)
        results: list[tuple[str, str]] = []
        youtube_results: list[SearchResult] = []
        if cached:
            results.append((f"cached:{cached.id}", f"{cached.artist} — {cached.title} (кэш)"))

        if self.provider:
            try:
                youtube_results = await self.provider.search(query, limit=limit)
            except Exception:
                logger.exception("Search failed for query=%r", query)
                youtube_results = []
            for index, result in enumerate(youtube_results):
                results.append((f"result:{index}", result.label))

        results = results[:limit]
        if not results:
            await message.reply_text("По запросу ничего не найдено. Попробуйте изменить запрос.")
            return

        context.chat_data["search_results"] = {
            str(index): result.url
            for index, result in enumerate(
                youtube_results[: max(0, limit - (1 if cached else 0))],
            )
        }
        from telegram import InlineKeyboardButton

        await message.reply_text(
            "🎵 Выберите песню:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(label, callback_data=callback)] for callback, label in results]
            ),
        )

    async def send_query(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        query: str,
        source_url: str | None = None,
    ) -> Song | None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return None

        loading_message = await message.reply_text("⏳ Скачиваю трек...")
        cached = await self.database.search_song(query)
        try:
            if cached:
                await self._send_cached(update, cached)
                await self.database.increment_play_count(cached.id)
                return cached

            if not self.provider:
                await message.reply_text(
                    "Песня пока не найдена в локальном каталоге. Загрузите аудиофайл в бот или включите разрешённый источник аудио."
                )
                return None

            async with self.download_slots:
                try:
                    downloaded = await self.provider.download(source_url or query)
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
            await sent.edit_reply_markup(reply_markup=self.track_actions(song, is_favorite=False))
            return song
        finally:
            try:
                await loading_message.delete()
            except Exception:
                logger.debug("Could not delete loading message", exc_info=True)

    async def _send_cached(self, update: Update, song: Song) -> None:
        message = update.effective_message
        user = update.effective_user
        if not message:
            return
        is_favorite = bool(user and await self.database.is_favorite(user.id, song.file_id))
        await message.reply_audio(
            audio=song.file_id,
            title=song.title,
            performer=song.artist,
            reply_markup=self.track_actions(song, is_favorite),
        )

    @staticmethod
    def track_actions(song: Song, is_favorite: bool = False) -> InlineKeyboardMarkup:
        favorite_label = "💔 Удалить из избранного" if is_favorite else "❤️"
        favorite_action = "remove" if is_favorite else "add"
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        favorite_label,
                        callback_data=f"favorite:{favorite_action}:{song.id}",
                    ),
                    InlineKeyboardButton(
                        "🎤 Исполнитель",
                        callback_data=f"artist:{song.artist[:50]}",
                    ),
                ],
            ]
        )

    async def send_song(self, update: Update, song: Song) -> None:
        message = update.effective_message
        user = update.effective_user
        if message:
            is_favorite = bool(user and await self.database.is_favorite(user.id, song.file_id))
            await message.reply_audio(
                audio=song.file_id,
                title=song.title,
                performer=song.artist,
                reply_markup=self.track_actions(song, is_favorite),
            )
            await self.database.increment_play_count(song.id)

    @staticmethod
    def cleanup_cache(cache_dir: Path) -> None:
        for path in cache_dir.glob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)


def _is_media_url(value: str) -> bool:
    return value.startswith(("https://", "http://")) and (
        "youtube.com" in value
        or "youtu.be" in value
        or "tiktok.com" in value
    )