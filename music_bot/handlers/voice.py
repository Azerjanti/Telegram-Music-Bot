from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from music_bot.access import ensure_access, register_user
from music_bot.services import MusicService

logger = logging.getLogger(__name__)


async def voice_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    media = message.voice or message.audio or message.document or message.video or message.video_note
    if not media:
        return
    if not await ensure_access(update, context):
        return
    try:
        await register_user(update, context)
    except Exception:
        pass
    service: MusicService = context.application.bot_data["music_service"]
    if not context.application.bot_data.get("enable_shazam", True):
        await message.reply_text("🎤 Распознавание голосом сейчас отключено администратором.")
        return
    try:
        from shazamio import Shazam
    except ImportError:
        logger.warning("shazamio is not installed; voice recognition unavailable")
        await message.reply_text(
            "🎤 Распознавание голосом пока недоступно: модуль распознавания не установлен. "
            "Администратор должен выполнить установку зависимости (shazamio) и ffmpeg."
        )
        return

    if not shutil.which("ffmpeg"):
        await message.reply_text(
            "🎤 Распознавание голосом недоступно: не установлен ffmpeg. "
            "Обратитесь к администратору."
        )
        return

    file_ref = message.voice or message.audio or message.document or message.video or message.video_note
    if message.document and not (
        _is_audio_document(message.document) or _is_video_document(message.document)
    ):
        await message.reply_text("Отправьте аудио- или видеофайл для распознавания.")
        return

    cache_dir = Path("/tmp/music-bot")
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
    if message.video_note:
        return ".mp4"
    media = message.audio or message.document or message.video
    filename = getattr(media, "file_name", None) or ""
    suffix = Path(filename).suffix.lower()
    if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".3gp", ".m4v"}:
        return suffix
    return suffix if suffix else (".mp4" if message.video else ".audio")


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

def _is_video_document(document) -> bool:
    mime_type = (document.mime_type or "").lower()
    filename = (document.file_name or "").lower()
    return mime_type.startswith("video/") or Path(filename).suffix in {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".avi",
        ".m4v",
        ".3gp",
        ".flv",
        ".ts",
    }
