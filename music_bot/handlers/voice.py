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
    media = getattr(message, "voice", None) or getattr(message, "audio", None) or getattr(message, "document", None) or getattr(message, "video", None) or getattr(message, "video_note", None)
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

    file_ref = getattr(message, "voice", None) or getattr(message, "audio", None) or getattr(message, "document", None) or getattr(message, "video", None) or getattr(message, "video_note", None)
    if getattr(message, "document", None) and not (
        _is_audio_document(message.document) or _is_video_document(message.document)
    ):
        await message.reply_text("Отправьте аудио- или видеофайл для распознавания.")
        return

    cache_dir = Path("/tmp/music-bot")
    input_path = cache_dir / f"recognition-{message.message_id}{_input_suffix(message)}"
    mp3_path = cache_dir / f"recognition-{message.message_id}.mp3"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Single status message that we edit - never spam new messages (task #5)
    status_msg = None
    try:
        status_msg = await message.reply_text("🎧 Слушаю аудио, подождите…")
    except Exception:
        logger.debug("Could not send status message", exc_info=True)

    try:
        telegram_file = await context.bot.get_file(file_ref.file_id)
        await telegram_file.download_to_drive(input_path)
        # Convert to mp3 for Shazam (handles .ogg opus from voice messages)
        try:
            recognition_path = await _convert_to_mp3(input_path, mp3_path)
        except Exception as conv_exc:
            logger.warning("MP3 conversion failed, trying original file: %s", conv_exc)
            # Shazam can sometimes handle ogg directly
            recognition_path = input_path

        shazam = Shazam()
        result = None
        # Try primary method, then fallback - with timeout so we really listen
        try:
            if hasattr(shazam, "recognize"):
                result = await asyncio.wait_for(shazam.recognize(str(recognition_path)), timeout=25)
            else:
                result = await asyncio.wait_for(shazam.recognize_song(str(recognition_path)), timeout=25)
        except asyncio.TimeoutError:
            logger.warning("Shazam recognition timed out")
            result = None
        except Exception as e:
            logger.debug("Primary Shazam method failed: %s", e, exc_info=True)
            # fallback to alternative method
            try:
                if hasattr(shazam, "recognize_song"):
                    result = await asyncio.wait_for(shazam.recognize_song(str(recognition_path)), timeout=25)
                elif hasattr(shazam, "recognize"):
                    result = await asyncio.wait_for(shazam.recognize(str(recognition_path)), timeout=25)
            except Exception as e2:
                logger.debug("Fallback Shazam also failed: %s", e2, exc_info=True)
                result = None

        track = {}
        if isinstance(result, dict):
            track = result.get("track", {}) or {}
            # Some shazamio versions return nested under 'track'
            if not track and "matches" in result:
                # try to extract from matches
                pass

        title = track.get("title")
        artist = track.get("subtitle")
        # fallback: try other keys
        if not title:
            title = track.get("share") and track.get("share", {}).get("subject")
        if not title or not artist:
            if status_msg:
                try:
                    await status_msg.edit_text("Песня не распознана 😔")
                except Exception:
                    await message.reply_text("Песня не распознана 😔")
            else:
                await message.reply_text("Песня не распознана 😔")
            return
        if status_msg:
            try:
                await status_msg.edit_text(f"Распознано: {title} — {artist}")
            except Exception:
                await message.reply_text(f"Распознано: {title} — {artist}")
        else:
            await message.reply_text(f"Распознано: {title} — {artist}")
        await service.send_query(update, context, f"{artist} {title}")
    except Exception:
        logger.exception("Voice recognition failed")
        if status_msg:
            try:
                await status_msg.edit_text("Песня не распознана 😔")
            except Exception:
                await message.reply_text("Песня не распознана 😔")
        else:
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
    # Use high-quality mp3 conversion for opus/ogg voice messages
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-q:a",
        "2",
        str(output_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0 or not output_path.exists():
        details = stderr.decode(errors="replace")[-800:]
        raise RuntimeError(f"Конвертация аудио не удалась: {details}")
    return output_path


def _input_suffix(message) -> str:
    if getattr(message, "voice", None):
        return ".ogg"
    if getattr(message, "video_note", None):
        return ".mp4"
    media = getattr(message, "audio", None) or getattr(message, "document", None) or getattr(message, "video", None)
    filename = getattr(media, "file_name", None) or ""
    suffix = Path(filename).suffix.lower()
    if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".3gp", ".m4v"}:
        return suffix
    return suffix if suffix else (".mp4" if getattr(message, "video", None) else ".audio")


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
