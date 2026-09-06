from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from music_bot.handlers.search import send_local_top
from music_bot.services import MusicService


async def callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    service: MusicService = context.application.bot_data["music_service"]
    data = query.data or ""
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
        for song in await service.database.top_songs(100):
            if song.id == song_id:
                await service.send_song(update, song)
                return
    if data.startswith("top:"):
        track_query = context.chat_data.get("top_tracks", {}).get(data.removeprefix("top:"))
        if track_query:
            await service.send_query(update, context, track_query)