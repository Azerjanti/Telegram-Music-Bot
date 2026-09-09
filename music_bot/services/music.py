from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from music_bot.database import Database, Song
from music_bot.downloader import SearchResult, YouTubeProvider
from music_bot.track_buttons import (
    get_pending_tracks,
    replace_markup,
    track_markup,
)

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
        limit: int = 30,
        artist_mode: bool = False,
    ) -> None:
        """Search the cache and the audio provider, then show all found tracks in
        paginated pages with ‹ › arrows (page size = SEARCH_PAGE_SIZE).

        ``artist_mode`` ("По исполнителю") additionally lists every catalogue
        track by that artist instead of only the single best match.
        """
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        prompt = "👤 Поиск по исполнителю" if artist_mode else "🔍 Поиск по запросу"
        await message.reply_text(f"{prompt}: {query}")
        if _is_media_url(query):
            await self.send_query(update, context, query, source_url=query)
            return

        items: list[tuple[str, str]] = []
        if artist_mode:
            try:
                catalogue_songs = await self.database.artist_songs(query)
            except Exception:
                logger.exception("Could not load catalogue songs for artist %r", query)
                catalogue_songs = []
            for song in catalogue_songs:
                items.append((f"cached:{song.id}", f"☑️ {song.artist} — {song.title} (в каталоге)"))
        else:
            cached = await self.database.search_song(query)
            if cached:
                items.append(
                    (f"cached:{cached.id}", f"☑️ {cached.artist} — {cached.title} (в каталоге)")
                )

        youtube_results: list[SearchResult] = []
        if self.provider:
            try:
                youtube_results = await self.provider.search(query, limit=limit)
            except Exception:
                logger.exception("Search failed for query=%r", query)
                youtube_results = []
        for index, result in enumerate(youtube_results):
            items.append((f"result:{index}", result.label))

        if not items:
            await message.reply_text("По запросу ничего не найдено. Попробуйте изменить запрос.")
            return

        # Keep the chosen-track lookup, keyed by the real YouTube result index.
        context.chat_data["search_results"] = {
            str(index): result.url for index, result in enumerate(youtube_results)
        }
        context.chat_data["search_items"] = items
        context.chat_data["search_page"] = 0
        context.chat_data["search_query"] = query

        sent_list = await message.reply_text(
            "🎵 Выберите песню:",
            reply_markup=_search_page_markup(items, 0),
        )
        # Remember where the list lives so the ⏪ on a track can bring it back
        # without posting a new message (task #2 navigation).
        try:
            context.chat_data["search_list_chat_id"] = sent_list.chat_id if hasattr(sent_list, "chat_id") else chat.id
            context.chat_data["search_list_message_id"] = getattr(sent_list, "message_id", None)
        except Exception:
            pass

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

        loading_message = await message.reply_text("⏳ Скачиваю трек… 0%")
        # Helpers to update the single loading message with 10%-step progress
        # without spamming (task #6). The download runs in a thread, so we
        # schedule edits via the running loop.
        loop = asyncio.get_running_loop()
        last_percent = {"value": 0}

        async def _safe_edit_loading(msg, text: str) -> None:
            try:
                await msg.edit_text(text)
            except Exception:
                logger.debug("Could not edit loading progress", exc_info=True)

        def _edit_progress(text: str) -> None:
            try:
                # If we're on the main event loop, schedule directly; otherwise
                # use thread-safe call (download runs in a thread via to_thread).
                try:
                    running = asyncio.get_running_loop()
                except RuntimeError:
                    running = None
                if running is loop:
                    # Same loop - don't block, just create a task
                    loop.create_task(_safe_edit_loading(loading_message, text))
                else:
                    asyncio.run_coroutine_threadsafe(
                        _safe_edit_loading(loading_message, text), loop
                    )
            except Exception:
                logger.debug("Could not schedule progress edit", exc_info=True)

        def _progress_hook(percent: int) -> None:
            # clamp and only emit every 10%
            p = max(0, min(100, int(percent)))
            stepped = (p // 10) * 10
            if stepped != last_percent["value"] and stepped % 10 == 0:
                last_percent["value"] = stepped
                _edit_progress(f"⏳ Скачиваю трек… {stepped}%")

        cached = await self.database.search_song(query)
        try:
            if cached:
                # cached is instant - show 100% briefly then send
                try:
                    await loading_message.edit_text("⏳ Скачиваю трек… 100%")
                except Exception:
                    pass
                await self._send_cached(update, cached)
                await self.database.increment_play_count(cached.id)
                return cached

            if not self.provider:
                await message.reply_text(
                    "Песня пока не найдена в локальном каталоге. Загрузите аудиофайл в бот или включите разрешённый источник аудио."
                )
                return None

            async with self.download_slots:
                sent = None
                token: str | None = None
                try:
                    # Provider may be a fake in tests that doesn't accept progress_callback
                    try:
                        downloaded = await self.provider.download(source_url or query, progress_callback=_progress_hook)  # type: ignore[call-arg]
                    except TypeError:
                        # fallback for providers without progress_callback param
                        downloaded = await self.provider.download(source_url or query)
                        # simulate at least 100% for test providers
                        _progress_hook(100)
                    if downloaded.path.stat().st_size > self.max_file_mb * 1024 * 1024:
                        raise RuntimeError("Файл превышает лимит Telegram")
                    # The file_id is only known once Telegram accepts the upload,
                    # so the ❤️ button first points at a short-lived token; it is
                    # replaced by the permanent song callback right below.
                    token = get_pending_tracks(context).reserve()
                    with downloaded.path.open("rb") as audio:
                        sent = await message.reply_audio(
                            audio=audio,
                            title=downloaded.metadata.title,
                            performer=downloaded.metadata.artist,
                            caption="Сохранено в каталоге бота",
                            reply_markup=track_markup(token=token, is_favorite=False),
                        )
                except Exception:
                    logger.exception("Audio download failed for query=%r", query)
                    await message.reply_text("Песня не найдена. Попробуйте другой запрос.")
                    return None
                finally:
                    if "downloaded" in locals() and downloaded.path.exists():
                        downloaded.path.unlink(missing_ok=True)

            if not sent or not sent.audio:
                return None
            file_id = sent.audio.file_id
            get_pending_tracks(context).attach(
                token,
                file_id=file_id,
                title=downloaded.metadata.title,
                artist=downloaded.metadata.artist,
            )
            song: Song | None = None
            try:
                song = await self.database.save_song(
                    title=downloaded.metadata.title,
                    artist=downloaded.metadata.artist,
                    file_id=file_id,
                    source_url=downloaded.metadata.source_url,
                )
                await self.database.increment_play_count(song.id)
            except Exception:
                # The track is already in the chat with working buttons - a broken
                # catalogue must never take them away again.
                logger.exception("Could not store downloaded track in the catalogue")
            if song is not None:
                is_favorite = False
                user = update.effective_user
                if user:
                    try:
                        is_favorite = await self.database.is_favorite(user.id, file_id)
                    except Exception:
                        logger.debug("Could not load favorite state", exc_info=True)
                get_pending_tracks(context).drop(token)
                await replace_markup(sent, track_markup(song=song, is_favorite=is_favorite))
            return song
        finally:
            try:
                await loading_message.delete()
            except Exception:
                logger.debug("Could not delete loading message", exc_info=True)

    async def _send_cached(self, update: Update, song: Song) -> None:
        await self._send_audio(update, song)

    @staticmethod
    def track_actions(song: Song, is_favorite: bool = False) -> InlineKeyboardMarkup:
        """❤️ / 💔 + ⏪ row for a track that is already in the catalogue."""
        return track_markup(song=song, is_favorite=is_favorite)

    async def _favorite_state(self, update: Update, song: Song) -> bool:
        user = update.effective_user
        if not user:
            return False
        try:
            return bool(await self.database.is_favorite(user.id, song.file_id))
        except Exception:
            # A failing favourite lookup must never stop the track being sent.
            logger.debug("Could not load favorite state for %s", song.file_id, exc_info=True)
            return False

    async def _send_audio(self, update: Update, song: Song) -> None:
        """Send a catalogue track. The ❤️/⏪ row is attached in the same call, so
        the buttons exist from the very first moment the audio is visible."""
        message = update.effective_message
        if not message:
            return
        is_favorite = await self._favorite_state(update, song)
        await message.reply_audio(
            audio=song.file_id,
            title=song.title,
            performer=song.artist,
            reply_markup=track_markup(song=song, is_favorite=is_favorite),
        )

    async def send_song(self, update: Update, song: Song) -> None:
        message = update.effective_message
        if message:
            await self._send_audio(update, song)
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

SEARCH_PAGE_SIZE = 8


def _search_page_markup(
    items: list[tuple[str, str]], page: int
) -> InlineKeyboardMarkup:
    """Build the paginated inline keyboard for a search-result list.

    The last row is always ⏪ Назад which returns to the main prompt
    \"Напишите название песни или исполнителя.\" without posting a new
    message (edits the current one) - task #2.
    """
    from telegram import InlineKeyboardButton

    from music_bot.track_buttons import SEARCH_BACK, SEARCH_BACK_LABEL

    total_pages = max(1, (len(items) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * SEARCH_PAGE_SIZE
    rows = [
        [InlineKeyboardButton(label, callback_data=callback)]
        for callback, label in items[start : start + SEARCH_PAGE_SIZE]
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data="sprev"))
    nav.append(
        InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop")
    )
    if start + SEARCH_PAGE_SIZE < len(items):
        nav.append(InlineKeyboardButton("▶️", callback_data="snext"))
    if nav:
        rows.append(nav)
    # Back button - always present so the user can return to the main screen
    rows.append([InlineKeyboardButton(SEARCH_BACK_LABEL, callback_data=SEARCH_BACK)])
    return InlineKeyboardMarkup(rows)


def clamp_search_page(page: int, total: int) -> int:
    total_pages = max(1, (total + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
    return max(0, min(int(page), total_pages - 1))
