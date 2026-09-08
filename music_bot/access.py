"""Access-control helpers shared by all handlers.

Responsibilities:
  * registering users / updating their last-seen activity,
  * deciding who is an admin,
  * enforcing the mandatory-subscription ("required channels") gate,
  * returning banned-user information.

Admins are the primary owners from ``ADMIN_IDS`` (settings) plus any user who
was granted the flag in the database through the admin panel.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from music_bot.config import Settings
from music_bot.database import Database, RequiredChannel, UserRecord

logger = logging.getLogger(__name__)


def get_database(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["database"]


def get_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> UserRecord | None:
    """Store/touch the user row. Returns None if there is no user to store."""
    user = update.effective_user
    db = get_database(context)
    if not user or user.is_bot:
        return None
    return await db.register_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=getattr(user, "last_name", None),
    )


async def is_admin(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = get_settings(context)
    if user_id in settings.admin_ids:
        return True
    db = get_database(context)
    try:
        record = await db.get_user(user_id)
    except Exception:
        logger.exception("Could not load admin status for %s", user_id)
        return False
    return bool(record and record.is_admin)


def is_owner(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return user_id in get_settings(context).admin_ids


async def user_block_reason(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str | None:
    """Return the ban reason if the user is banned, else None."""
    db = get_database(context)
    try:
        record = await db.get_user(user_id)
    except Exception:
        logger.exception("Could not check ban for %s", user_id)
        return None
    if record and record.is_banned:
        return record.banned_reason
    return None


def any_user_is_banned(record: UserRecord | None) -> bool:
    return bool(record and record.is_banned)


# --------------------------------------------------------------------------
# Mandatory subscription ("required channels") gate
# --------------------------------------------------------------------------

async def required_channels(context: ContextTypes.DEFAULT_TYPE) -> list[RequiredChannel]:
    db = get_database(context)
    try:
        return await db.list_required_channels()
    except Exception:
        logger.exception("Could not list required channels")
        return []


async def _member_of(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except BadRequest:
        return False
    except Forbidden:
        return False
    except Exception:
        logger.warning("Membership check failed for chat %s user %s", chat_id, user_id)
        return False
    return member.status in {"creator", "administrator", "member"}


async def missing_channels(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> list[RequiredChannel]:
    """Return required channels the user has NOT joined yet."""
    missing: list[RequiredChannel] = []
    for channel in await required_channels(context):
        joined = await _member_of(context, channel.chat_id, user_id)
        if not joined:
            missing.append(channel)
    return missing


def channel_invite_link(channel: RequiredChannel) -> str:
    if channel.username:
        return f"https://t.me/{channel.username.lstrip('@')}"
    return f"https://t.me/c/{str(abs(channel.chat_id))[1:]}/1"


def required_channels_markup(missing: list[RequiredChannel]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for channel in missing:
        label = f"🔗 {channel.title or channel.username or channel.chat_id}"
        rows.append([InlineKeyboardButton(label, url=channel_invite_link(channel))])
    rows.append([InlineKeyboardButton("✅ Я подписался — проверить", callback_data="mrecheck")])
    return InlineKeyboardMarkup(rows)


async def require_subscriptions(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Block a request unless the user joined every required channel.

    Returns True when the user may proceed (no required channels, or all joined).
    Otherwise sends the join prompt and returns False.
    """
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return True
    if await is_admin(user.id, context):
        return True
    missing = await missing_channels(context, user.id)
    if not missing:
        return True
    try:
        await message.reply_text(
            "⛔️ Чтобы пользоваться ботом, сначала подпишитесь на каналы ниже и "
            "нажмите кнопку «Я подписался».",
            reply_markup=required_channels_markup(missing),
        )
    except Forbidden:
        pass
    return False


async def ensure_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Combined gate: banned -> denied; not subscribed -> join prompt.

    Returns True if the user may continue with the requested action.
    """
    user = update.effective_user
    message = update.effective_message
    if not user:
        return True
    if await is_admin(user.id, context):
        return True
    reason = await user_block_reason(context, user.id)
    if reason is not None:
        if message:
            try:
                await message.reply_text(
                    "⛔️ Вы заблокированы и не можете пользоваться ботом."
                )
            except Forbidden:
                pass
        return False
    return await require_subscriptions(update, context)


async def mark_blocked_if_reachable_failed(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> None:
    """Call right after a message to a user fails so the bot can't reach them."""
    try:
        await get_database(context).mark_user_blocked(user_id)
    except Exception:
        logger.exception("Could not mark user %s as blocked", user_id)
