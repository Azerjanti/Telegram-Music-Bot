from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from music_bot.services import MusicService

logger = logging.getLogger(__name__)


def favorites_markup(favorites) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"▶️ Слушать — {favorite.title} — {favorite.artist}",
                    callback_data=f"favorite:play:{favorite.id}",
                ),
                InlineKeyboardButton("🗑 Удалить", callback_data=f"favorite:delete:{favorite.id}"),
            ]
            for favorite in favorites
        ]
    )


async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    service: MusicService = context.application.bot_data["music_service"]
    try:
        favorites = await service.database.list_favorites(user.id)
    except Exception:
        logger.exception("Could not load favorites for user %s", user.id)
        await message.reply_text("Избранное временно недоступно. Попробуйте ещё раз позже.")
        return
    if not favorites:
        await message.reply_text("В избранном пока нет треков.")
        return
    await message.reply_text(
        "❤️ Избранные треки:",
        reply_markup=favorites_markup(favorites),
    )