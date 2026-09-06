from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from music_bot.services import MusicService

logger = logging.getLogger(__name__)


async def voice_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not (message.voice or message.audio or message.document):
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

    file_ref = message.voice or message.audio or message.document
    if message.document and not _is_audio_document(message.document):
        await message.reply_text("Отправьте аудиофайл для распознавания.")
        return

    cache_dir = Path("music_bot/.cache")
    input_path = cache_dir / f"recognition-{message.message_id}{_input_suffix(message)}"
    mp3_path = cache_dir / f"recognition-{message.message_id}.mp3"
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        telegram_file = await context.bot.get_file(file_ref.file_id)
        await telegram_file.download_to_drive(input_path)
        recognition_path = await _convert_to_mp3(input_path, mp3_path)
        shazam = Shazam()
        if hasattr(shazam, "recognize"):
            result = await shazam.recognize(str(recognition_path))
        else:
            result = await shazam.recognize_song(str(recognition_path))
        track = result.get("track", {})
        title = track.get("title")
        artist = track.get("subtitle")
        if not title or not artist:
            await message.reply_text("Песня не распознана 😔")
            return
        await message.reply_text(f"Распознано: {title} — {artist}")
        await service.send_query(update, context, f"{artist} {title}")
    except Exception:
        logger.exception("Voice recognition failed")
        await message.reply_text("Песня не распознана 😔")
    finally:
        input_path.unlink(missing_ok=True)
        mp3_path.unlink(missing_ok=True)


async def _convert_to_mp3(input_path: Path, output_path: Path) -> Path:
    if input_path.suffix.lower() == ".mp3":
        return input_path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден в системе")
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        str(output_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0 or not output_path.exists():
        details = stderr.decode(errors="replace")[-500:]
        raise RuntimeError(f"Конвертация аудио не удалась: {details}")
    return output_path


def _input_suffix(message) -> str:
    if message.voice:
        return ".ogg"
    media = message.audio or message.document
    filename = getattr(media, "file_name", None) or ""
    suffix = Path(filename).suffix.lower()
    return suffix if suffix else ".audio"


def _is_audio_document(document) -> bool:
    mime_type = (document.mime_type or "").lower()
    filename = (document.file_name or "").lower()
    return mime_type.startswith("audio/") or Path(filename).suffix in {
        ".mp3",
        ".ogg",
        ".oga",
        ".wav",
        ".m4a",
        ".aac",
        ".flac",
        ".opus",
        ".webm",
    }