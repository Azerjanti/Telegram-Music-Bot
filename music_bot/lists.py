"""Paginated inline lists (Топ 100, Локальный топ, результаты поиска).

One message, ⏪ / ⏩ buttons, and every tap edits the message instead of posting
a new one.
"""

from __future__ import annotations

from typing import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from music_bot.track_buttons import MAX_CALLBACK_DATA_BYTES

#: Tracks shown per page.
PAGE_SIZE = 10

PREV_LABEL = "⏪"
NEXT_LABEL = "⏩"


def total_pages(count: int, page_size: int = PAGE_SIZE) -> int:
    return max(1, (count + page_size - 1) // page_size)


def clamp_page(page: int, count: int, page_size: int = PAGE_SIZE) -> int:
    return max(0, min(int(page), total_pages(count, page_size) - 1))


def list_markup(
    rows: Sequence[tuple[str, str]],
    page: int,
    prev_callback: str,
    next_callback: str,
    page_size: int = PAGE_SIZE,
) -> InlineKeyboardMarkup:
    """Build one page of a track list plus the ⏪ page-indicator ⏩ row.

    ``rows`` is a list of ``(label, callback_data)`` for the *whole* list.
    """
    pages = total_pages(len(rows), page_size)
    page = clamp_page(page, len(rows), page_size)
    start = page * page_size
    keyboard: list[list[InlineKeyboardButton]] = []
    for label, data in rows[start : start + page_size]:
        # Keep every callback inside Telegram's 64-byte limit.
        assert len(data.encode()) <= MAX_CALLBACK_DATA_BYTES, data
        keyboard.append([InlineKeyboardButton(label, callback_data=data)])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(PREV_LABEL, callback_data=prev_callback))
    nav.append(InlineKeyboardButton(f"📄 {page + 1}/{pages}", callback_data="noop"))
    if start + page_size < len(rows):
        nav.append(InlineKeyboardButton(NEXT_LABEL, callback_data=next_callback))
    keyboard.append(nav)
    return InlineKeyboardMarkup(keyboard)


def page_rows(rows: Sequence[tuple[str, str]], page: int, page_size: int = PAGE_SIZE):
    """The slice of labels shown on ``page`` (used by tests and previews)."""
    page = clamp_page(page, len(rows), page_size)
    start = page * page_size
    return rows[start : start + page_size]
