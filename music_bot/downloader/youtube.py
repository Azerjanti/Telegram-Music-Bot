from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from music_bot.search_engine import TrackMetadata, parse_track_title

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadedTrack:
    path: Path
    metadata: TrackMetadata


class YouTubeProvider:
    """Optional yt-dlp provider; enable only for content you are allowed to use."""

    def __init__(self, output_dir: Path, retries: int = 3) -> None:
        self.output_dir = output_dir
        self.retries = retries

    async def download(self, query: str) -> DownloadedTrack:
        return await asyncio.to_thread(self._download_sync, query)

    def _download_sync(self, query: str) -> DownloadedTrack:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("yt-dlp is not installed") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", query).strip("-").lower()[:70] or "track"
        output_template = str(self.output_dir / f"{safe_id}-%(id)s.%(ext)s")
        options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": self.retries,
            "outtmpl": output_template,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query} official audio", download=True)
            if not info or not info.get("entries"):
                raise RuntimeError("Аудио не найдено")
            entry = info["entries"][0]
            source_url = entry.get("webpage_url")
            raw_title = entry.get("track") or entry.get("title") or query
            metadata = parse_track_title(raw_title, entry.get("artist") or entry.get("uploader"))
            requested = Path(ydl.prepare_filename(entry))
            path = requested.with_suffix(".mp3")
            if not path.exists():
                matches = list(self.output_dir.glob(f"{requested.stem}*.mp3"))
                if matches:
                    path = matches[0]
            if not path.exists():
                raise RuntimeError("Конвертированный mp3-файл не создан")
            return DownloadedTrack(
                path=path,
                metadata=TrackMetadata(metadata.title, metadata.artist, source_url),
            )