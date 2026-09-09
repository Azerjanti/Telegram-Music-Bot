"""Inline ❤️ / ❌ buttons attached to *every* audio the bot sends.

Two Telegram limits shape this module:

* ``callback_data`` may not exceed 64 bytes, which is far too small for a
  Telegram ``file_id`` (~60-100 chars).  A freshly uploaded track therefore gets
  a short **token** first; the token is swapped for the permanent
  ``favorite:add:<song id>`` callback as soon as the track lands in the
  catalogue.
* editing a message with identical content raises
  ``BadRequest: Message is not modified`` — callers must not crash on it, so
  :func:`replace_markup` swallows exactly that error.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError

logger = logging.getLogger(__name__)

#: Hard Telegram limit for InlineKeyboardButton.callback_data.
MAX_CALLBACK_DATA_BYTES = 64

# Permanent, song-id based callbacks (kept for backwards compatibility).
FAVORITE_ADD = "favorite:add:{}"
FAVORITE_REMOVE = "favorite:remove:{}"
# Token based callbacks, used until the uploaded track has a catalogue id.
PENDING_ADD = "favnew:a:{}"
PENDING_REMOVE = "favnew:r:{}"
# "❌" under a track: hide the buttons and go back to search mode.
TRACK_CLOSE = "track:close"
# Legacy alias still accepted on old messages.
SEARCH_BACK = "search:back"

LIKE_LABEL = "❤️"
UNLIKE_LABEL = "💔"
CLOSE_LABEL = "❌"


def build_callback_data(template: str, *parts: Any) -> str:
    """Format a callback and refuse anything Telegram would reject (>64 bytes)."""
    data = template.format(*parts)
    size = len(data.encode("utf-8"))
    if size > MAX_CALLBACK_DATA_BYTES:
        raise ValueError(f"callback_data is {size} bytes, limit is {MAX_CALLBACK_DATA_BYTES}: {data!r}")
    return data


@dataclass(frozen=True)
class PendingTrack:
    """Audio metadata for a track that was uploaded but maybe not catalogued."""

    file_id: str
    title: str
    artist: str


class PendingTracks:
    """``token -> PendingTrack`` map kept in ``application.bot_data``.

    Tokens are reserved *before* the upload (the file_id is only known once
    Telegram answers), filled in right after it, and dropped once the track has
    a catalogue id.
    """

    LIMIT = 1000

    def __init__(self, limit: int = LIMIT) -> None:
        self._entries: dict[str, PendingTrack | None] = {}
        self._limit = max(1, limit)

    def reserve(self) -> str:
        token = f"{secrets.token_hex(4)}{len(self._entries):x}"
        # Keep the payload well inside the 64-byte callback_data budget.
        assert len(build_callback_data(PENDING_ADD, token).encode()) <= MAX_CALLBACK_DATA_BYTES
        self._entries[token] = None
        while len(self._entries) > self._limit:
            self._entries.pop(next(iter(self._entries)), None)
        return token

    def attach(self, token: str, file_id: str, title: str, artist: str) -> None:
        self._entries[token] = PendingTrack(file_id=file_id, title=title, artist=artist)

    def get(self, token: str) -> PendingTrack | None:
        return self._entries.get(token)

    def drop(self, token: str) -> None:
        self._entries.pop(token, None)

    def __len__(self) -> int:
        return len(self._entries)


def get_pending_tracks(context) -> PendingTracks:
    """Return (and lazily create) the pending-track registry in ``bot_data``."""
    store = context.application.bot_data.get("pending_tracks")
    if not isinstance(store, PendingTracks):
        store = PendingTracks()
        context.application.bot_data["pending_tracks"] = store
    return store


def track_markup(
    song=None,
    is_favorite: bool = False,
    token: str | None = None,
) -> InlineKeyboardMarkup:
    """The ❤️ / ❌ row that sits under every audio message.

    ``song`` (a catalogue row) is preferred because its callbacks survive a bot
    restart; ``token`` is the fallback for a track that has just been uploaded.
    """
    if song is not None and getattr(song, "id", 0):
        like_data = build_callback_data(
            FAVORITE_REMOVE if is_favorite else FAVORITE_ADD, song.id
        )
    elif token:
        like_data = build_callback_data(PENDING_REMOVE if is_favorite else PENDING_ADD, token)
    else:
        raise ValueError("track_markup needs either a saved song or a pending token")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    UNLIKE_LABEL if is_favorite else LIKE_LABEL,
                    callback_data=like_data,
                ),
                InlineKeyboardButton(CLOSE_LABEL, callback_data=TRACK_CLOSE),
            ]
        ]
    )


def is_not_modified(error: BaseException) -> bool:
    """True when Telegram rejected an edit because nothing changed."""
    return "not modified" in str(error).lower()


async def replace_markup(message, reply_markup: InlineKeyboardMarkup | None) -> bool:
    """Edit only the keyboard of ``message``; never raise on a no-op edit."""
    if message is None:
        return False
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
        return True
    except BadRequest as exc:
        if not is_not_modified(exc):
            logger.debug("Could not update the track buttons: %s", exc)
        return False
    except TelegramError as exc:
        logger.debug("Could not update the track buttons: %s", exc)
        return False
