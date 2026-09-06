from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from music_bot.handlers.favorites import favorites_markup
from music_bot.handlers.search import send_local_top
from music_bot.services import MusicService

logger = logging.getLogger(__name__)


async def callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    service: MusicService = context.application.bot_data["music_service"]
    data = query.data or ""
    user = query.from_user
    if data.startswith("favorite:add:") or data.startswith("favorite:remove:"):
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
            if query.message:
                await query.message.edit_reply_markup(
                    reply_markup=service.track_actions(song, is_favorite=True)
                )
        else:
            try:
                await service.database.remove_favorite(user.id, song.file_id)
            except Exception:
                logger.exception("Could not remove favorite for user %s", user.id)
                await query.answer("Не удалось удалить трек из избранного.", show_alert=True)
                return
            await query.answer("💔 Удалено из избранного")
            if query.message:
                await query.message.edit_reply_markup(
                    reply_markup=service.track_actions(song, is_favorite=False)
                )
        return
    if data.startswith("favorite:play:"):
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
            await query.message.reply_audio(
                audio=favorite.file_id,
                title=favorite.title,
                performer=favorite.artist,
                reply_markup=(
                    service.track_actions(song, is_favorite=True)
                    if song
                    else None
                ),
            )
        if song:
            await service.database.increment_play_count(song.id)
        await query.answer()
        return
    if data.startswith("favorite:delete:"):
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
        await send_local_top(update, context, limit=50, title="Следующие популярные треки")
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