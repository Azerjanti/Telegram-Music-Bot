from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from music_bot.search_engine import TrackMetadata, parse_track_title

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadedTrack:
    path: Path
    metadata: TrackMetadata


@dataclass(frozen=True)
class SearchResult:
    title: str
    artist: str
    url: str

    @property
    def label(self) -> str:
        return f"{self.artist} — {self.title}"[:60]


# ---------------------------------------------------------------------------
# Global rate-limiting for IP protection (avoid flagging by YouTube)
# ---------------------------------------------------------------------------
_last_download_ts: float = 0.0
_rate_limit_lock = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("Invalid int for %s=%r, using default %s", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        logger.warning("Invalid float for %s=%r, using default %s", name, raw, default)
        return default


def _env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw != "" else default


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    parts = re.split(r"[,\s]+", raw.strip())
    cleaned = [p.strip() for p in parts if p.strip()]
    return cleaned if cleaned else default


def _has_curl_cffi() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def _get_player_clients() -> List[str]:
    """
    Get YouTube player clients to try.
    Default: tv,web_embedded,tv_embedded,visionos,ios
    These are cookie-less and don't require PO-token, unlike web/android.
    Env: YTDLP_PLAYER_CLIENTS or YTDLP_CLIENTS or YTDLP_YOUTUBE_CLIENTS
    If YTDLP_USE_YTDLP_DEFAULT_CLIENTS=true, return [] to let yt-dlp use its own default rotation.
    """
    if _env_bool("YTDLP_USE_YTDLP_DEFAULT_CLIENTS", False):
        return []

    for env_name in ("YTDLP_PLAYER_CLIENTS", "YTDLP_CLIENTS", "YTDLP_YOUTUBE_CLIENTS"):
        raw = os.getenv(env_name)
        if raw and raw.strip():
            clients = _env_list(env_name, [])
            if clients:
                allow_po_clients = _env_bool("YTDLP_ALLOW_PO_CLIENTS", False)
                if not allow_po_clients:
                    risky = {"android", "web", "web_music", "web_creator", "mweb"}
                    filtered = [c for c in clients if c not in risky or c in {"web_embedded", "tv_embedded"}]
                    if filtered:
                        clients = filtered
                    else:
                        logger.warning("All requested clients were filtered as PO-token risky, keeping original list")
                logger.info("Using player clients from %s: %s", env_name, clients)
                return clients

    default_clients = ["tv", "web_embedded", "tv_embedded", "visionos", "ios"]
    return default_clients


def _get_client_sets_to_try() -> List[List[str]]:
    primary = _get_player_clients()
    if not primary:
        primary = ["tv", "web_embedded", "tv_embedded", "visionos", "ios"]

    sets: List[List[str]] = []
    sets.append(primary)

    if len(primary) > 3:
        sets.append(["tv", "web_embedded", "tv_embedded"])
        sets.append(["visionos", "ios", "tv"])
        sets.append(["tv", "web_embedded"])
        sets.append(["tv"])
        sets.append(["web_embedded"])
        sets.append(["visionos"])
    elif len(primary) > 1:
        for c in primary:
            if [c] not in sets:
                sets.append([c])

    deduped: List[List[str]] = []
    seen = set()
    for s in sets:
        key = tuple(s)
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    return deduped


def _get_player_skip() -> List[str]:
    default_skip = ["configs"]
    raw = os.getenv("YTDLP_PLAYER_SKIP")
    if raw is not None:
        raw_stripped = raw.strip().lower()
        if raw_stripped in {"", "none", "false", "0"}:
            return []
        return _env_list("YTDLP_PLAYER_SKIP", default_skip)
    return default_skip


def _get_impersonate_target() -> Optional[Any]:
    """Return yt-dlp's typed impersonation target, never a plain string.

    yt-dlp 2026.x validates ``opts['impersonate']`` with an assertion.  Keep
    this optional: installations without curl_cffi must continue using the
    normal client rotation, and a bad environment value must not kill a
    download before the first client is tried.
    """
    if not _env_bool("YTDLP_ENABLE_IMPERSONATION", True) or not _has_curl_cffi():
        return None

    value: Optional[str] = None
    for env_name in ("YTDLP_IMPERSONATE", "YTDLP_DEFAULT_IMPERSONATE", "YTDLP_IMPERSONATION"):
        candidate = _env_str(env_name, None)
        if candidate:
            if candidate.strip().lower() in {"0", "false", "none", "off", "disable", "disabled"}:
                logger.info("Impersonation disabled via %s", env_name)
                return None
            value = candidate.strip()
            break
    if not value:
        value = "chrome"

    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget

        target = ImpersonateTarget.from_str(value)
        logger.info("Using typed yt-dlp impersonation target: %s", value)
        return target
    except (ImportError, AttributeError, TypeError, ValueError, AssertionError) as exc:
        logger.warning("Disabling invalid yt-dlp impersonation target %r: %s", value, exc)
        return None


def _get_format() -> str:
    return _env_str("YTDLP_FORMAT", "251/250/140/m4a/bestaudio/best") or "251/250/140/m4a/bestaudio/best"


def _get_socket_timeout() -> int:
    return _env_int("YTDLP_SOCKET_TIMEOUT", 30)


def _get_retries() -> int:
    return _env_int("YTDLP_RETRIES", 3)


def _get_fragment_retries() -> int:
    return _env_int("YTDLP_FRAGMENT_RETRIES", 3)


def _get_extractor_retries() -> int:
    return _env_int("YTDLP_EXTRACTOR_RETRIES", 3)


def _get_file_access_retries() -> int:
    return _env_int("YTDLP_FILE_ACCESS_RETRIES", 3)


def _get_sleep_interval() -> float:
    return _env_float("YTDLP_SLEEP_INTERVAL", 1.0)


def _get_max_sleep_interval() -> float:
    return _env_float("YTDLP_MAX_SLEEP_INTERVAL", 5.0)


def _get_sleep_requests() -> float:
    return _env_float("YTDLP_SLEEP_REQUESTS", 1.0)


def _get_min_download_interval() -> float:
    return _env_float("YTDLP_MIN_DOWNLOAD_INTERVAL", 2.0)


def _get_geo_bypass() -> bool:
    return _env_bool("YTDLP_GEO_BYPASS", True)


def _get_geo_bypass_country() -> Optional[str]:
    return _env_str("YTDLP_GEO_BYPASS_COUNTRY", "US")


def _get_force_ipv4() -> bool:
    return _env_bool("YTDLP_FORCE_IPV4", False)


def _get_visitor_data() -> Optional[str]:
    return _env_str("YTDLP_VISITOR_DATA", None)


def _get_throttled_rate() -> Optional[str]:
    return _env_str("YTDLP_THROTTLED_RATE", None)


def _get_http_chunk_size() -> Optional[int]:
    raw = os.getenv("YTDLP_HTTP_CHUNK_SIZE")
    if raw:
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _enforce_rate_limit() -> None:
    if not _env_bool("YTDLP_ENABLE_RATE_LIMIT", True):
        return

    min_interval = _get_min_download_interval()
    if min_interval <= 0:
        return

    global _last_download_ts
    with _rate_limit_lock:
        now = time.monotonic()
        elapsed = now - _last_download_ts
        if _last_download_ts != 0 and elapsed < min_interval:
            jitter = random.uniform(0.3, 1.2) if _env_bool("YTDLP_RANDOM_JITTER", True) else 0
            sleep_needed = (min_interval - elapsed) + jitter
            logger.info("Rate limiting: sleeping %.2fs to protect IP (elapsed %.2fs < min %.2fs)", sleep_needed, elapsed, min_interval)
            time.sleep(sleep_needed)
        _last_download_ts = time.monotonic()


def _get_base_ydl_options() -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": not _env_bool("YTDLP_DEBUG", False),
        "no_warnings": not _env_bool("YTDLP_DEBUG", False),
        "noplaylist": True,
        "retries": _get_retries(),
        "fragment_retries": _get_fragment_retries(),
        "extractor_retries": _get_extractor_retries(),
        "file_access_retries": _get_file_access_retries(),
        "socket_timeout": _get_socket_timeout(),
        "geo_bypass": _get_geo_bypass(),
        "concurrent_fragment_downloads": _env_int("YTDLP_CONCURRENT_FRAGMENTS", 1),
        "sleep_interval": _get_sleep_interval(),
        "max_sleep_interval": _get_max_sleep_interval(),
        "sleep_interval_requests": _get_sleep_requests(),
        "http_chunk_size": _get_http_chunk_size() or 10485760,
        "nocheckcertificate": False,
        "prefer_free_formats": False,
        "http_headers": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": _env_str("YTDLP_ACCEPT_LANGUAGE", "en-US,en;q=0.9"),
            "Sec-Fetch-Mode": "navigate",
        },
    }

    if _get_geo_bypass_country():
        opts["geo_bypass_country"] = _get_geo_bypass_country()

    if _get_force_ipv4():
        opts["force_ipv4"] = True

    if _get_throttled_rate():
        opts["throttled_rate"] = _get_throttled_rate()

    impersonate = _get_impersonate_target()
    if impersonate:
        opts["impersonate"] = impersonate
    else:
        custom_ua = _env_str("YTDLP_USER_AGENT", None)
        if custom_ua:
            opts["http_headers"]["User-Agent"] = custom_ua

    js_runtimes_env = _env_str("YTDLP_JS_RUNTIMES", None)
    if js_runtimes_env:
        opts["js_runtimes"] = {rt.strip(): {} for rt in js_runtimes_env.split(",") if rt.strip()}

    if _env_bool("YTDLP_DEBUG", False):
        opts["verbose"] = True

    return opts


def _get_extractor_args(clients: List[str]) -> Dict[str, Dict[str, List[str]]]:
    args: Dict[str, List[str]] = {}

    if clients:
        args["player_client"] = clients

    player_skip = _get_player_skip()
    if player_skip:
        args["player_skip"] = player_skip

    args["max_comments"] = _env_list("YTDLP_MAX_COMMENTS", ["0"])
    args["include_live_dash"] = ["false"]

    visitor_data = _get_visitor_data()
    if visitor_data:
        args["visitor_data"] = [visitor_data]

    player_params = _env_str("YTDLP_PLAYER_PARAMS", None)
    if player_params:
        args["player_params"] = [player_params]

    if args:
        return {"youtube": args}
    return {}


def _is_bot_wall_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    indicators = [
        "sign in to confirm you're not a bot",
        "sign in to confirm you are not a bot",
        "confirm you're not a bot",
        "not a bot",
        "use --cookies-from-browser or --cookies for the authentication",
        "http error 429",
        "too many requests",
    ]
    return any(ind in msg for ind in indicators)


class YouTubeProvider:
    """Optional yt-dlp provider; enable only for content you are allowed to use."""

    def __init__(self, output_dir: Path, retries: int = 3) -> None:
        self.output_dir = output_dir
        self.retries = retries

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return await asyncio.to_thread(self._search_sync, query, limit)

    def _search_sync(self, query: str, limit: int) -> list[SearchResult]:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("yt-dlp is not installed") from exc

        _enforce_rate_limit()

        base_opts = _get_base_ydl_options()
        search_opts = {
            **base_opts,
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "default_search": f"ytsearch{limit}",
            "noplaylist": False,
        }

        clients = _get_player_clients()
        if clients:
            search_opts["extractor_args"] = _get_extractor_args(clients)

        if _env_bool("YTDLP_ENABLE_RATE_LIMIT", True):
            time.sleep(random.uniform(0.2, 0.8))

        logger.debug("Searching YouTube with opts: clients=%s query=%r", clients, query)

        try:
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        except Exception as exc:
            logger.warning("YouTube search failed for query=%r: %s", query, exc)
            fallback_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "default_search": f"ytsearch{limit}",
                "noplaylist": False,
                "extractor_args": {"youtube": {"player_client": ["tv"]}},
                "geo_bypass": True,
                "socket_timeout": 30,
            }
            try:
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            except Exception as exc2:
                logger.error("YouTube search fallback also failed: %s", exc2)
                return []

        results: list[SearchResult] = []
        for entry in (info or {}).get("entries", []):
            if not entry:
                continue
            video_id = entry.get("id")
            url = entry.get("webpage_url") or entry.get("url")
            if not url and video_id:
                url = f"https://www.youtube.com/watch?v={video_id}"
            if not url:
                continue
            metadata = parse_track_title(
                entry.get("track") or entry.get("title") or query,
                entry.get("artist") or entry.get("uploader"),
            )
            results.append(SearchResult(metadata.title, metadata.artist, url))
        return results[:limit]

    async def download(self, query: str, progress_callback=None) -> DownloadedTrack:
        return await asyncio.to_thread(self._download_sync, query, progress_callback)

    def _download_sync(self, query: str, progress_callback=None) -> DownloadedTrack:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("yt-dlp is not installed") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", query).strip("-").lower()[:70] or "track"
        output_template = str(self.output_dir / f"{safe_id}-%(id)s.%(ext)s")

        _enforce_rate_limit()

        if _env_bool("YTDLP_ENABLE_RATE_LIMIT", True):
            jitter_delay = random.uniform(0.5, 1.5) if _env_bool("YTDLP_RANDOM_JITTER", True) else 0.5
            time.sleep(jitter_delay)

        base_opts = _get_base_ydl_options()
        if os.getenv("YTDLP_RETRIES") is None:
            base_opts["retries"] = self.retries

        format_str = _get_format()
        preferred_codec = _env_str("YTDLP_PREFERRED_CODEC", "mp3") or "mp3"
        preferred_quality = _env_str("YTDLP_PREFERRED_QUALITY", "192") or "192"

        # Build progress hook for Telegram editing (task #6)
        progress_hooks = []
        if progress_callback:

            def _yt_progress(d):
                try:
                    if d.get("status") == "downloading":
                        total = d.get("total_bytes") or d.get("total_bytes_estimate")
                        downloaded = d.get("downloaded_bytes") or 0
                        if total:
                            percent = int(downloaded * 100 / total)
                            progress_callback(percent)
                        else:
                            # fallback when total unknown - use fragment counts
                            frag_index = d.get("fragment_index")
                            frag_count = d.get("fragment_count")
                            if frag_index and frag_count:
                                percent = int(frag_index * 100 / frag_count)
                                progress_callback(percent)
                    elif d.get("status") == "finished":
                        progress_callback(100)
                except Exception:
                    logger.debug("Progress hook failed", exc_info=True)

            progress_hooks.append(_yt_progress)

        common_opts = {
            **base_opts,
            "format": format_str,
            "noplaylist": True,
            "outtmpl": output_template,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": preferred_codec,
                    "preferredquality": preferred_quality,
                }
            ],
        }
        if progress_hooks:
            common_opts["progress_hooks"] = progress_hooks

        client_sets = _get_client_sets_to_try()
        logger.info("Starting download for query=%r with %d client sets to try", query, len(client_sets))
        logger.debug("Client sets: %s", client_sets)

        last_exc: Optional[Exception] = None

        for attempt_idx, clients in enumerate(client_sets):
            try:
                opts = dict(common_opts)
                extractor_args = _get_extractor_args(clients)
                if "extractor_args" in base_opts:
                    merged = dict(base_opts.get("extractor_args", {}))
                    youtube_args = merged.get("youtube", {})
                    new_youtube_args = extractor_args.get("youtube", {})
                    combined_youtube = {**youtube_args, **new_youtube_args}
                    merged["youtube"] = combined_youtube
                    opts["extractor_args"] = merged
                else:
                    opts["extractor_args"] = extractor_args

                logger.info(
                    "Download attempt %d/%d for %r with clients=%s impersonate=%s",
                    attempt_idx + 1,
                    len(client_sets),
                    query,
                    clients,
                    opts.get("impersonate"),
                )

                with yt_dlp.YoutubeDL(opts) as ydl:
                    search_query = query if _is_url(query) else f"ytsearch1:{query} official audio"
                    info = ydl.extract_info(search_query, download=True)
                    if not info:
                        raise RuntimeError("Аудио не найдено")
                    entry = info["entries"][0] if info.get("entries") else info
                    source_url = entry.get("webpage_url")
                    raw_title = entry.get("track") or entry.get("title") or query
                    metadata = parse_track_title(raw_title, entry.get("artist") or entry.get("uploader"))
                    requested = Path(ydl.prepare_filename(entry))
                    path = requested.with_suffix(f".{preferred_codec}")
                    if not path.exists():
                        matches = list(self.output_dir.glob(f"{requested.stem}*.{preferred_codec}"))
                        if matches:
                            path = matches[0]
                        else:
                            mp3_matches = list(self.output_dir.glob(f"{requested.stem}*.mp3"))
                            if mp3_matches:
                                path = mp3_matches[0]
                    if not path.exists():
                        raise RuntimeError("Конвертированный mp3-файл не создан")
                    logger.info("Successfully downloaded %r -> %s using clients %s", query, path, clients)
                    return DownloadedTrack(
                        path=path,
                        metadata=TrackMetadata(metadata.title, metadata.artist, source_url),
                    )

            except Exception as exc:
                last_exc = exc
                is_bot_wall = _is_bot_wall_error(exc)
                if not is_bot_wall:
                    cause = getattr(exc, "__cause__", None)
                    if cause and _is_bot_wall_error(cause):
                        is_bot_wall = True

                if is_bot_wall:
                    logger.warning(
                        "Client set %s hit bot-wall for query=%r (attempt %d/%d): %s",
                        clients,
                        query,
                        attempt_idx + 1,
                        len(client_sets),
                        exc,
                    )
                    if attempt_idx < len(client_sets) - 1:
                        backoff = random.uniform(1.0, 3.0) * (attempt_idx + 1)
                        if _env_bool("YTDLP_ENABLE_RATE_LIMIT", True):
                            backoff += random.uniform(0.5, 1.5)
                        logger.info("Backing off %.2fs before trying next client set", backoff)
                        time.sleep(backoff)
                        continue
                else:
                    logger.warning(
                        "Download failed with client set %s for query=%r: %s (attempt %d/%d)",
                        clients,
                        query,
                        exc,
                        attempt_idx + 1,
                        len(client_sets),
                    )
                    if attempt_idx < len(client_sets) - 1:
                        time.sleep(random.uniform(0.5, 1.5))
                        continue
                break

        if last_exc:
            logger.error("All client sets failed for query=%r, last error: %s", query, last_exc)
            raise last_exc
        raise RuntimeError("Download failed after trying all client sets")


def _is_url(value: str) -> bool:
    return value.startswith(("https://", "http://")) and (
        "youtube.com" in value or "youtu.be" in value or "tiktok.com" in value
    )
