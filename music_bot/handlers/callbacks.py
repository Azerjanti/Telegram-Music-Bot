from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from music_bot.admin import dispatch_admin_callback
from music_bot.access import missing_channels
from music_bot.database import Song
from music_bot.handlers.favorites import favorites_markup
from music_bot.handlers.search import (
    paginate_local_top,
    paginate_top,
    send_local_top,
)
from music_bot.services import MusicService
from music_bot.services.music import SEARCH_PAGE_SIZE, _search_page_markup, clamp_search_page
from music_bot.track_buttons import (
    PENDING_ADD,
    PENDING_REMOVE,
    TRACK_CLOSE,
    get_pending_tracks,
    replace_markup,
    track_markup,
)

logger = logging.getLogger(__name__)


async def callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    service: MusicService = context.application.bot_data["music_service"]
    data = query.data or ""
    user = query.from_user

    # ---- Admin panel -------------------------------------------------------
    if await dispatch_admin_callback(update, context):
        return

    # ---- Nop (page indicator etc.) ----------------------------------------
    if data == "noop":
        await query.answer()
        return

    # ⏪ under a track → hide the buttons and go back to song search / search list.
    # First press on an audio returns to the search result list (which is still
    # visible underneath), second press on the list returns to the main prompt.
    # Both edit the *existing* message (task #2, #6).
    if data == TRACK_CLOSE:
        context.user_data["mode"] = "song"
        await query.answer("⏪ Назад")
        # If a search list is still remembered, refresh it so the user lands
        # back on the list without a new message.
        items = context.chat_data.get("search_items")
        if items and query.message and getattr(query.message, "audio", None):
            try:
                chat_id = context.chat_data.get("search_list_chat_id")
                msg_id = context.chat_data.get("search_list_message_id")
                if chat_id and msg_id:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text="🎵 Выберите песню:",
                            reply_markup=_search_page_markup(items, int(context.chat_data.get("search_page", 0))),
                        )
                    except Exception:
                        logger.debug("Could not refresh search list on track back", exc_info=True)
            except Exception:
                pass
        await replace_markup(query.message, None)
        return

    # ⏪ on the search result list itself → main prompt.
    # Also keeps compatibility with old ❌ messages (search:back).
    if data == "search:back":
        context.user_data["mode"] = "song"
        await query.answer()
        if query.message:
            if getattr(query.message, "audio", None):
                await replace_markup(query.message, None)
            else:
                try:
                    await query.message.edit_text(
                        "Напишите название песни или исполнителя."
                    )
                except Exception:
                    logger.debug("Could not prompt search after ⏪", exc_info=True)
        return

    # ❤️ / 💔 for a track that was just uploaded and may not be catalogued yet.
    if data.startswith(PENDING_ADD.split("{")[0]) or data.startswith(
        PENDING_REMOVE.split("{")[0]
    ):
        await _pending_favorite(update, context, adding=data.startswith(PENDING_ADD.split("{")[0]))
        return

    # ---- Mandatory channel re-check ---------------------------------------
    if data == "mrecheck":
        if not user:
            await query.answer()
            return
        missing = await missing_channels(context, user.id)
        if not missing:
            await query.answer("✅ Подписка подтверждена!")
            if query.message:
                try:
                    await query.message.edit_text(
                        "✅ Подписка подтверждена! Теперь вы можете пользоваться ботом. "
                        "Выберите пункт меню ещё раз."
                    )
                except Exception:
                    logger.debug("Could not edit recheck message", exc_info=True)
        else:
            await query.answer(
                f"Подпишитесь ещё на {len(missing)} канал(а/ов) ниже.",
                show_alert=True,
            )
        return

    # ---- Search results pagination ----------------------------------------
    if data in {"snext", "sprev"}:
        items = context.chat_data.get("search_items") or []
        page = int(context.chat_data.get("search_page", 0))
        total = len(items)
        if total == 0:
            await query.answer("Список пуст")
            return
        total_pages = max(1, (total + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
        if data == "snext":
            page += 1
        else:
            page -= 1
        page = clamp_search_page(page, total)
        context.chat_data["search_page"] = page
        if query.message:
            try:
                await query.message.edit_reply_markup(
                    reply_markup=_search_page_markup(items, page)
                )
            except Exception:
                logger.debug("Could not paginate", exc_info=True)
            await query.answer(f"Страница {page + 1} из {total_pages}")
        else:
            await query.answer()
        return

    # ---- Топ 100 / Локальный топ pagination (⏪ ⏩) -------------------------
    if data in {"tnext", "tprev"}:
        page = int(context.chat_data.get("top_page", 0))
        await paginate_top(update, context, page + 1 if data == "tnext" else page - 1)
        return
    if data in {"lnext", "lprev"}:
        page = int(context.chat_data.get("local_top_page", 0))
        await paginate_local_top(update, context, page + 1 if data == "lnext" else page - 1)
        return

    if data.startswith("favorite:add:") or data.startswith("favorite:remove:"):
        if not user:
            await query.answer()
            return
        song_id = int(data.rsplit(":", 1)[1])
        song = await service.database.get_song(song_id)
        if not song:
            await query.answer("Трек больше недоступен.", show_alert=True)
            return
        if data.startswith("favorite:add:"):
            try:
                await service.database.add_favorite(user.id, song)
            except Exception:
                logger.exception("Could not add favorite for user %s", user.id)
                await query.answer("Не удалось сохранить трек в избранное.", show_alert=True)
                return
            await query.answer("❤️ Добавлено в избранное!")
            await replace_markup(query.message, service.track_actions(song, is_favorite=True))
        else:
            try:
                await service.database.remove_favorite(user.id, song.file_id)
            except Exception:
                logger.exception("Could not remove favorite for user %s", user.id)
                await query.answer("Не удалось удалить трек из избранного.", show_alert=True)
                return
            await query.answer("💔 Удалено из избранного")
            await replace_markup(query.message, service.track_actions(song, is_favorite=False))
        return
    if data.startswith("favorite:play:"):
        if not user:
            await query.answer()
            return
        favorite_id = int(data.rsplit(":", 1)[1])
        try:
            favorite = await service.database.get_favorite(user.id, favorite_id)
        except Exception:
            logger.exception("Could not load favorite %s for user %s", favorite_id, user.id)
            await query.answer("Избранное временно недоступно.", show_alert=True)
            return
        if not favorite:
            await query.answer("Трек уже удалён.", show_alert=True)
            return
        song = await service.database.get_song_by_file_id(favorite.file_id)
        if query.message:
            # The ❤️/❌ row must be there even when the catalogue lost the track.
            if song:
                markup = service.track_actions(song, is_favorite=True)
            else:
                pending = get_pending_tracks(context)
                token = pending.reserve()
                pending.attach(
                    token,
                    file_id=favorite.file_id,
                    title=favorite.title,
                    artist=favorite.artist,
                )
                markup = track_markup(token=token, is_favorite=True)
            await query.message.reply_audio(
                audio=favorite.file_id,
                title=favorite.title,
                performer=favorite.artist,
                reply_markup=markup,
            )
        if song:
            await service.database.increment_play_count(song.id)
        await query.answer()
        return
    if data.startswith("favorite:delete:"):
        if not user:
            await query.answer()
            return
        favorite_id = int(data.rsplit(":", 1)[1])
        try:
            favorite = await service.database.get_favorite(user.id, favorite_id)
        except Exception:
            logger.exception("Could not load favorite %s for user %s", favorite_id, user.id)
            await query.answer("Избранное временно недоступно.", show_alert=True)
            return
        if not favorite:
            await query.answer("Трек уже удалён.", show_alert=True)
            return
        try:
            await service.database.delete_favorite(user.id, favorite_id)
            remaining = await service.database.list_favorites(user.id)
        except Exception:
            logger.exception("Could not delete favorite %s for user %s", favorite_id, user.id)
            await query.answer("Не удалось удалить трек из избранного.", show_alert=True)
            return
        await query.answer("🗑 Удалено из избранного")
        if query.message:
            if remaining:
                await query.message.edit_text(
                    "❤️ Избранные треки:",
                    reply_markup=favorites_markup(remaining),
                )
            else:
                await query.message.edit_text("В избранном пока нет треков.")
        return
    await query.answer()
    if data.startswith("artist:"):
        artist = data.removeprefix("artist:")
        songs = await service.database.artist_songs(artist)
        if songs:
            await query.message.reply_text("\n".join(f"{i}. {song.title}" for i, song in enumerate(songs, 1)))
        else:
            await query.message.reply_text("Песен этого исполнителя пока нет в каталоге.")
        return
    if data == "next":
        await send_local_top(update, context, title="Следующие популярные треки")
        return
    if data == "local_top":
        await send_local_top(update, context)
        return
    if data.startswith("song:"):
        song_id = int(data.removeprefix("song:"))
        song = await service.database.get_song(song_id)
        if song:
            await service.send_song(update, song)
        return
    if data.startswith("cached:"):
        song_id = int(data.removeprefix("cached:"))
        song = await service.database.get_song(song_id)
        if song:
            await service.send_song(update, song)
        return
    if data.startswith("result:"):
        result_id = data.removeprefix("result:")
        source_url = context.chat_data.get("search_results", {}).get(result_id)
        if source_url:
            await service.send_query(update, context, source_url, source_url=source_url)
        return
    if data.startswith("top:"):
        track_query = context.chat_data.get("top_tracks", {}).get(data.removeprefix("top:"))
        if track_query:
            await service.send_query(update, context, track_query)


async def _pending_favorite(
    update: Update, context: ContextTypes.DEFAULT_TYPE, adding: bool
) -> None:
    """Handle ❤️/💔 for a track that was uploaded but has no catalogue id yet.

    Telegram caps callback_data at 64 bytes, so such a track is referenced by a
    short token (see music_bot.track_buttons.PendingTracks).
    """
    query = update.callback_query
    if not query:
        return
    user = query.from_user
    service: MusicService = context.application.bot_data["music_service"]
    data = query.data or ""
    token = data.rsplit(":", 1)[1]
    pending = get_pending_tracks(context)
    info = pending.get(token)
    if not user or not info:
        await query.answer("Трек больше недоступен — найдите песню ещё раз.", show_alert=True)
        return

    # Prefer the catalogue row when it exists, so the buttons become permanent.
    song = None
    try:
        song = await service.database.get_song_by_file_id(info.file_id)
    except Exception:
        logger.debug("Could not look up song by file_id", exc_info=True)
    if song is None:
        song = Song(
            id=0,
            title=info.title,
            artist=info.artist,
            file_id=info.file_id,
            source_url=None,
            play_count=0,
        )

    if adding:
        try:
            await service.database.add_favorite(user.id, song)
        except Exception:
            logger.exception("Could not add favorite for user %s", user.id)
            await query.answer("Не удалось сохранить трек в избранное.", show_alert=True)
            return
        await query.answer("❤️ Добавлено в избранное!")
    else:
        try:
            await service.database.remove_favorite(user.id, info.file_id)
        except Exception:
            logger.exception("Could not remove favorite for user %s", user.id)
            await query.answer("Не удалось удалить трек из избранного.", show_alert=True)
            return
        await query.answer("💔 Удалено из избранного")

    # Keep the row: only the heart flips, and the message is edited in place.
    await replace_markup(
        query.message,
        track_markup(
            song=song if song.id else None,
            is_favorite=adding,
            token=None if song.id else token,
        ),
    )
