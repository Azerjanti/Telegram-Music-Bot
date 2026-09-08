from telegram import Update
from telegram.ext import ContextTypes

from music_bot.access import ensure_access, register_user
from music_bot.services import MusicService
from music_bot.top_charts.hardcoded import TOP_100_FALLBACK
from music_bot.top_charts import SpotifyProvider


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
    if mode == "artist":
        songs = await service.database.artist_songs(message.text)
        if not songs:
            await message.reply_text("В локальном каталоге пока нет песен этого исполнителя.")
            return
        lines = [f"{index}. {song.title}" for index, song in enumerate(songs[:50], start=1)]
        await message.reply_text("Песни исполнителя:\n\n" + "\n".join(lines))
        return
    if mode == "local_top":
        await send_local_top(update, context)
        return
    await service.show_search_results(update, context, message.text.strip())


async def send_local_top(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    limit: int = 50,
    title: str = "Локальный топ",
) -> None:
    message = update.effective_message
    service: MusicService = context.application.bot_data["music_service"]
    songs = await service.database.top_songs(limit)
    if not songs:
        if message:
            await message.reply_text(f"{title} пока пуст. Найдите первую песню через поиск.")
        return
    keyboard = [
        [f"{index}. {song.title} — {song.artist}", f"song:{song.id}"]
        for index, song in enumerate(songs, start=1)
    ]
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    if message:
        await message.reply_text(
            f"{title}:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(label, callback_data=data)] for label, data in keyboard]
            ),
        )


async def send_spotify_top(update: Update, context: ContextTypes.DEFAULT_TYPE, limit: int = 50) -> None:
    provider: SpotifyProvider | None = context.application.bot_data.get("spotify_provider")
    tracks = TOP_100_FALLBACK[:limit]
    if provider:
        try:
            spotify_tracks = await provider.top_tracks(limit)
            if spotify_tracks:
                tracks = spotify_tracks
        except Exception:
            pass
    if not tracks or not update.effective_message:
        await send_local_top(update, context, limit=limit, title="Топ 100 из локального каталога")
        return

    context.chat_data["top_tracks"] = {str(index): track.query for index, track in enumerate(tracks, start=1)}
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    await update.effective_message.reply_text(
        "Топ 100:",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"{index}. {track.title} — {track.artist}",
                        callback_data=f"top:{index}",
                    )
                ]
                for index, track in enumerate(tracks, start=1)
            ]
        ),
    )