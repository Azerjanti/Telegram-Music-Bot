from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from music_bot.access import ensure_access, register_user
from music_bot.lists import PAGE_SIZE, clamp_page, list_markup
from music_bot.services import MusicService
from music_bot.top_charts.hardcoded import TOP_100_FALLBACK
from music_bot.top_charts import SpotifyProvider

logger = logging.getLogger(__name__)

#: How far the charts reach.
TOP_LIMIT = 100


async def text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return
    if not await ensure_access(update, context):
        return
    try:
        await register_user(update, context)
    except Exception:
        pass
    mode = context.user_data.pop("mode", "song")
    service: MusicService = context.application.bot_data["music_service"]
    query = message.text.strip()
    if mode == "artist":
        # "По исполнителю": show a playable list (catalogue + audio source),
        # not a plain text dump that cannot be pressed.
        await service.show_search_results(
            update, context, query, limit=30, artist_mode=True
        )
        return
    if mode == "local_top":
        await send_local_top(update, context)
        return
    await service.show_search_results(update, context, query)


async def send_local_top(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    limit: int = TOP_LIMIT,
    title: str = "Локальный топ",
    page: int = 0,
    edit: bool = False,
) -> None:
    """The bot's own catalogue, most played first, paginated with ⏪ / ⏩."""
    message = update.effective_message
    query = update.callback_query
    service: MusicService = context.application.bot_data["music_service"]
    try:
        songs = await service.database.top_songs(limit)
    except Exception:
        logger.exception("Could not load the local top")
        songs = []

    context.chat_data["local_top_title"] = title
    context.chat_data["local_top_page"] = clamp_page(page, len(songs))

    if not songs:
        text = (
            f"📈 {title} пока пуст.\n\n"
            "Сюда попадают треки, которые бот уже скачивал или находил в своём "
            "каталоге. Найдите первую песню через «🔍 Поиск песни» — и она "
            "появится здесь."
        )
        if query and query.message:
            await query.message.edit_text(text)
        elif message:
            await message.reply_text(text)
        return

    rows = [
        (f"{index}. {song.title} — {song.artist}", f"song:{song.id}")
        for index, song in enumerate(songs, start=1)
    ]
    markup = list_markup(rows, page, "lprev", "lnext")
    if edit and query and query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=markup)
            await query.answer(f"Страница {clamp_page(page, len(songs)) + 1}")
            return
        except Exception:
            logger.debug("Could not paginate the local top", exc_info=True)
    if message:
        await message.reply_text(f"📈 {title}:", reply_markup=markup)


async def send_spotify_top(
    update: Update, context: ContextTypes.DEFAULT_TYPE, limit: int = TOP_LIMIT
) -> None:
    """Топ 100: Spotify Global chart when configured, otherwise the built-in list."""
    provider: SpotifyProvider | None = context.application.bot_data.get("spotify_provider")
    tracks = list(TOP_100_FALLBACK[:limit])
    if provider:
        try:
            spotify_tracks = await provider.top_tracks(limit)
            if spotify_tracks:
                tracks = spotify_tracks[:limit]
        except Exception:
            logger.exception("Spotify chart unavailable, using the built-in list")
    if not tracks:
        await send_local_top(update, context, limit=limit, title="Топ 100 из локального каталога")
        return

    context.chat_data["top_tracks"] = {
        str(index): track.query for index, track in enumerate(tracks, start=1)
    }
    context.chat_data["top_page"] = 0
    rows = [
        (f"{index}. {track.title} — {track.artist}", f"top:{index}")
        for index, track in enumerate(tracks, start=1)
    ]
    context.chat_data["top_rows"] = rows

    message = update.effective_message
    if message:
        await message.reply_text(
            f"🏆 Топ {len(tracks)}:",
            reply_markup=list_markup(rows, 0, "tprev", "tnext", page_size=PAGE_SIZE),
        )


async def paginate_top(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """⏪ / ⏩ for Топ 100 - edits the same message."""
    query = update.callback_query
    rows = context.chat_data.get("top_rows") or []
    if not query or not rows:
        if query:
            await query.answer("Список пуст")
        return
    page = clamp_page(page, len(rows))
    context.chat_data["top_page"] = page
    try:
        await query.message.edit_reply_markup(
            reply_markup=list_markup(rows, page, "tprev", "tnext")
        )
        await query.answer(f"Страница {page + 1} из {(len(rows) + PAGE_SIZE - 1) // PAGE_SIZE}")
    except Exception:
        logger.debug("Could not paginate the chart", exc_info=True)
        await query.answer()


async def paginate_local_top(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """⏪ / ⏩ for Локальный топ - re-reads the catalogue and edits the message."""
    query = update.callback_query
    if not query:
        return
    await send_local_top(
        update,
        context,
        limit=TOP_LIMIT,
        title=context.chat_data.get("local_top_title", "Локальный топ"),
        page=page,
        edit=True,
    )
