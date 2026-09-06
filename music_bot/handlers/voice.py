from __future__ import annotations

import logging
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from music_bot.services import MusicService

logger = logging.getLogger(__name__)


async def voice_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not (message.voice or message.audio):
        return
    service: MusicService = context.application.bot_data["music_service"]
    if not context.application.bot_data.get("enable_shazam", True):
        await message.reply_text("Распознавание голосом отключено в конфигурации.")
        return
    try:
        from shazamio import Shazam
    except ImportError:
        await message.reply_text("Распознавание голосом ещё не подключено. Используйте поиск текстом.")
        return

    file_ref = message.voice or message.audio
    temp_path = Path("music_bot/.cache") / f"voice-{message.message_id}.ogg"
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        telegram_file = await context.bot.get_file(file_ref.file_id)
        await telegram_file.download_to_drive(temp_path)
        shazam = Shazam()
        if hasattr(shazam, "recognize"):
            result = await shazam.recognize(str(temp_path))
        else:
            result = await shazam.recognize_song(str(temp_path))
        track = result.get("track", {})
        title = track.get("title")
        artist = track.get("subtitle")
        if not title or not artist:
            await message.reply_text("Песня не распознана 😔 Попробуйте ещё раз")
            return
        await message.reply_text(f"Найдена: {title} — {artist}")
        await service.send_query(update, context, f"{artist} {title}")
    except Exception:
        logger.exception("Voice recognition failed")
        await message.reply_text("Песня не распознана 😔 Попробуйте ещё раз")
    finally:
        temp_path.unlink(missing_ok=True)