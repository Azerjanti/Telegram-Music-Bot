"""Admin panel: /admin.

Provides (in Russian):
  * Статистика  - how many users started / are active / auto-blocked the bot / banned,
  * Обязательные каналы - add / list / delete subscription channels,
  * Блокировки - ban / unban users,
  * Администраторы - grant / revoke admin rights (owner-only for safety),
  * Голосовой поиск - turn voice/audio recognition on / off at runtime.

The panel is message based: buttons switch actions, and where text input is needed
(e.g. a channel @username or a user id) the bot sets ``user_data["admin_wait"]`` and
the next text message is routed here by ``admin_text``.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from music_bot.access import get_database, is_admin, is_owner
from music_bot.database import UserRecord

logger = logging.getLogger(__name__)

USER_PAGE_SIZE = 8


# ----------------------------------------------------------------------------
# UI helpers
# ----------------------------------------------------------------------------

def _name(record: UserRecord | None, fallback: str = "—") -> str:
    if not record:
        return fallback
    base = record.first_name or ""
    return f"{base} (@{record.username})" if record.username else (base or fallback)


def _user_status(record: UserRecord | None) -> str:
    if not record:
        return "не известен"
    marks = []
    if record.is_banned:
        marks.append("🔒 заблокирован")
    elif record.blocked_at:
        marks.append("🚫 блокировал бота")
    if record.is_admin:
        marks.append("👑 админ")
    if not marks:
        marks.append("активен")
    return ", ".join(marks)


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Статистика", callback_data="adm:stats")],
            [InlineKeyboardButton("📢 Обязательные каналы", callback_data="adm:channels")],
            [InlineKeyboardButton("⛔️ Блокировки (бан)", callback_data="adm:ban_menu")],
            [InlineKeyboardButton("👥 Администраторы", callback_data="adm:admins")],
            [InlineKeyboardButton("🎤 Голосовой поиск", callback_data="adm:voice")],
        ]
    )


def _back(text: str = "⬅️ Назад") -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text, callback_data="adm:menu")]]


def _quick_ban_keyboard(record: UserRecord) -> InlineKeyboardMarkup:
    row: list[InlineKeyboardButton] = []
    if record.is_banned:
        row.append(
            InlineKeyboardButton("🔓 Разблокировать", callback_data=f"adm:un:{record.user_id}")
        )
    else:
        row.append(
            InlineKeyboardButton("🔒 Заблокировать", callback_data=f"adm:ban:{record.user_id}")
        )
    return InlineKeyboardMarkup([row])


# ----------------------------------------------------------------------------
# Entry point (/admin) + text collector
# ----------------------------------------------------------------------------

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    if not await is_admin(user.id, context):
        try:
            await message.reply_text("⛔️ У вас нет доступа к панели администратора.")
        except Forbidden:
            pass
        return
    await _open_menu(message, context)


async def _open_menu(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("admin_wait", None)
    await message.reply_text(
        "🛠 Панель администратора\nВыберите действие:",
        reply_markup=_menu_keyboard(),
    )


async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Route pending admin text input (channel, user id, reason...).

    Returns True when the message was consumed by the admin flow.
    """
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not message.text:
        return False
    if not await is_admin(user.id, context):
        return False
    wait = context.user_data.get("admin_wait")
    if not wait:
        return False
    context.user_data["admin_wait"] = None
    value = message.text.strip()
    try:
        if wait == "channel_add":
            await _add_channel(update, context, value)
        elif wait == "ban":
            await _ban_by_text(update, context, value)
        elif wait == "unban":
            await _unban_by_text(update, context, value)
        elif wait == "add_admin":
            await _add_admin_by_text(update, context, value)
        else:
            return False
    except Forbidden:
        pass
    return True


# ----------------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------------

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    db = get_database(context)
    try:
        stats = await db.user_stats()
    except Exception:
        logger.exception("Could not load stats")
        await query.message.reply_text("Не удалось получить статистику.", reply_markup=InlineKeyboardMarkup(_back()))
        return
    text = (
        "📊 Статистика бота\n\n"
        f"👤 Всего начали бот: <b>{stats['total']}</b>\n"
        f"🟢 Активны за 7 дней: <b>{stats['active']}</b>\n"
        f"🚫 Заблокировали бота: <b>{stats['blocked']}</b>\n"
        f"🔒 Заблокированы вами: <b>{stats['banned']}</b>"
    )
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(_back()))


# ----------------------------------------------------------------------------
# Voice recognition toggle
# ----------------------------------------------------------------------------

async def show_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    current = bool(context.application.bot_data.get("enable_shazam"))
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Включён" if current else "Включить",
                callback_data="adm:voice_on" if not current else "noop",
            ),
            InlineKeyboardButton(
                "Включить" if current else "✅ Выключен",
                callback_data="adm:voice_off" if current else "noop",
            ),
        ],
        *_back(),
    ]
    await query.message.reply_text(
        "🎤 Распознавание голосовых/аудио сообщений\n\n"
        f"Текущий статус: <b>{'включено' if current else 'выключено'}</b>\n\n"
        "Оно определяет песню через Shazam и затем показывает результат поиском.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def set_voice(update: Update, context: ContextTypes.DEFAULT_TYPE, enabled: bool) -> None:
    query = update.callback_query
    if not query:
        return
    context.application.bot_data["enable_shazam"] = enabled
    await query.answer("Сохранено")
    await show_voice(update, context)


# ----------------------------------------------------------------------------
# Required channels
# ----------------------------------------------------------------------------

async def show_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    db = get_database(context)
    try:
        channels = await db.list_required_channels()
    except Exception:
        logger.exception("Could not list required channels")
        channels = []
    lines = ["📢 Обязательные каналы\n"]
    if not channels:
        lines.append("Каналов пока нет. Добавьте канал, чтобы пользователи обязаны были на него подписаться.")
    for channel in channels:
        name = channel.title or channel.username or str(channel.chat_id)
        lines.append(f"• {name} — @{channel.username}" if channel.username else f"• {name}")
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("➕ Добавить канал", callback_data="adm:channel_add")]
    ]
    for channel in channels:
        rows.append(
            [InlineKeyboardButton(f"🗑 Удалить: {channel.title or channel.username or channel.chat_id}", callback_data=f"adm:channel_del:{channel.id}")]
        )
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm:menu")])
    await query.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def _add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    message = update.effective_message
    if not message:
        return
    handle = value.strip()
    if handle.startswith("https://t.me/"):
        handle = handle.split("https://t.me/")[1]
    handle = handle.split("/")[0].lstrip("@")
    if not handle or handle.lower() in {"joinchat", "addtopic", "s"}:
        await message.reply_text("❌ Не понял ссылку. Пришлите username канала, например @MyChannel или ссылку https://t.me/MyChannel")
        return
    try:
        chat = await context.bot.get_chat(handle)
    except (BadRequest, Forbidden) as exc:
        logger.warning("Could not resolve channel %s: %s", handle, exc)
        await message.reply_text(
            "❌ Не удалось получить канал. Убедитесь, что бот добавлен в канал как администратор "
            "и что вы прислали верный @username или ссылку."
        )
        return
    # Only allow channels / groups / supergroups.
    if chat.type not in {"channel", "supergroup", "group"}:
        await message.reply_text("Это не канал/группа.")
        return
    username = chat.username.lstrip("@") if chat.username else None
    try:
        await get_database(context).add_required_channel(
            chat_id=chat.id, username=username, title=chat.title
        )
    except Exception:
        logger.exception("Could not save channel")
        await message.reply_text("❌ Не удалось сохранить канал.")
        return
    await message.reply_text(
        f"✅ Канал добавлен: <b>{chat.title}</b> (@{username})" if username
        else f"✅ Канал добавлен: <b>{chat.title}</b>",
        parse_mode="HTML",
    )
    await show_channels(update, context)


async def delete_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id: int) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        await get_database(context).remove_required_channel(channel_id)
        await query.answer("Канал удалён")
    except Exception:
        logger.exception("Could not delete channel")
        await query.answer("Не удалось удалить канал", show_alert=True)
        return
    await show_channels(update, context)


# ----------------------------------------------------------------------------
# Ban / unban
# ----------------------------------------------------------------------------

async def show_ban_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    rows = [
        [InlineKeyboardButton("🔒 Заблокировать пользователя", callback_data="adm:ban_prompt")],
        [InlineKeyboardButton("🔓 Разблокировать", callback_data="adm:unban_prompt")],
        [InlineKeyboardButton("📋 Список пользователей", callback_data="adm:list:0")],
        *_back(),
    ]
    await query.message.reply_text(
        "⛔️ Блокировки\n\nВыберите действие. Заблокированный пользователь не сможет пользоваться ботом.",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def ban_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, unban: bool) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    context.user_data["admin_wait"] = "unban" if unban else "ban"
    action = "разблокировать" if unban else "заблокировать"
    await query.message.reply_text(
        f"Пришлите ID или @username пользователя, которого хотите {action}.\n\n"
        "Например: 1234567890 или @username" + ("" if unban else "\n\nМожно добавить причину: 1234567890 спам")
    )


async def _ban_by_text(update: Update, context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    message = update.effective_message
    if not message:
        return
    target, reason = _parse_target(value)
    user_id = await _resolve_target(get_database(context), target)
    if user_id is None:
        await message.reply_text("Не нашёл такого пользователя. Пришлите ID или зарегистрированный @username.")
        return
    if user_id == update.effective_user.id:
        await message.reply_text("Нельзя заблокировать самого себя.")
        return
    try:
        await get_database(context).set_banned(user_id, True, reason)
    except Exception:
        logger.exception("Ban failed for %s", user_id)
        await message.reply_text("❌ Не удалось заблокировать пользователя.")
        return
    await message.reply_text(f"🔒 Пользователь <b>{user_id}</b> заблокирован.", parse_mode="HTML")
    await _try_send_banned_notice(update, context, user_id, reason)


async def _unban_by_text(update: Update, context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    message = update.effective_message
    if not message:
        return
    target, _ = _parse_target(value)
    user_id = await _resolve_target(get_database(context), target)
    if user_id is None:
        await message.reply_text("Не нашёл такого пользователя. Пришлите ID или зарегистрированный @username.")
        return
    try:
        await get_database(context).set_banned(user_id, False)
        await get_database(context).clear_user_blocked(user_id)
    except Exception:
        logger.exception("Unban failed for %s", user_id)
        await message.reply_text("❌ Не удалось разблокировать.")
        return
    await message.reply_text(f"🔓 Пользователь <b>{user_id}</b> разблокирован.", parse_mode="HTML")


async def quick_ban(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        await get_database(context).set_banned(user_id, True, "По решению администратора")
        await query.answer("Заблокирован")
    except Exception:
        logger.exception("Quick ban failed")
        await query.answer("Не удалось", show_alert=True)
        return
    await _try_send_banned_notice(update, context, user_id, "По решению администратора")
    await list_users(update, context, _current_list_page(context))


async def quick_unban(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        await get_database(context).set_banned(user_id, False)
        await get_database(context).clear_user_blocked(user_id)
        await query.answer("Разблокирован")
    except Exception:
        logger.exception("Quick unban failed")
        await query.answer("Не удалось", show_alert=True)
        return
    await list_users(update, context, _current_list_page(context))


def _parse_target(value: str) -> tuple[int | None, str | None]:
    """Extract (user_id, reason) from e.g. '123456 спам' or '@username'."""
    parts = value.split(None, 1)
    head = parts[0]
    reason = parts[1].strip() if len(parts) > 1 else None
    if head.startswith("@"):
        # Resolved later by the caller if the user exists in the DB.
        return ("@" + head[1:].lower(), reason)
    if head.lstrip("-").isdigit():
        return (int(head), reason)
    return (None, reason)


async def _resolve_target(db, target) -> int | None:
    if isinstance(target, int):
        return target
    username = str(target).lstrip("@").lower()
    users = await db.search_users(username)
    for record in users:
        if record.username and record.username.lower() == username:
            return record.user_id
    return None


def _current_list_page(context: ContextTypes.DEFAULT_TYPE) -> int:
    return int(context.chat_data.get("admin_list_page", 0))


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    """Paged list of users with per-row ban/unban buttons."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    db = get_database(context)
    offset = page * USER_PAGE_SIZE
    try:
        users = await db.list_users(offset=offset, limit=USER_PAGE_SIZE)
    except Exception:
        logger.exception("Could not list users")
        await query.message.reply_text("Не удалось загрузить список пользователей.")
        return
    if not users:
        await query.message.reply_text("Пользователей пока нет.", reply_markup=InlineKeyboardMarkup(_back()))
        return
    context.chat_data["admin_list_page"] = page
    lines = ["📋 Пользователи:\n"]
    for record in users:
        lines.append(f"• {_name(record)}\n  ID: <code>{record.user_id}</code> — {_user_status(record)}")
    rows: list[list[InlineKeyboardButton]] = []
    for record in users:
        if record.is_banned:
            rows.append([InlineKeyboardButton(f"🔓 {record.user_id} — {record.first_name or '?'}", callback_data=f"adm:un:{record.user_id}")])
        else:
            rows.append([InlineKeyboardButton(f"🔒 {record.user_id} — {record.first_name or '?'}", callback_data=f"adm:ban:{record.user_id}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"adm:list:{page - 1}"))
    nav.append(InlineKeyboardButton("➡️" if len(users) == USER_PAGE_SIZE else "·", callback_data="noop"))
    if len(users) == USER_PAGE_SIZE:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"adm:list:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm:ban_menu")])
    await query.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _try_send_banned_notice(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, reason: str | None
) -> None:
    """Try to inform the banned user; if it fails the user already blocked the bot."""
    text = "⛔️ Вы были заблокированы в музыкальном боте."
    if reason:
        text += f"\nПричина: {reason}"
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
    except Forbidden:
        await get_database(context).mark_user_blocked(user_id)


# ----------------------------------------------------------------------------
# Administrators management
# ----------------------------------------------------------------------------

async def show_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    db = get_database(context)
    settings = context.application.bot_data["settings"]
    rows: list[list[InlineKeyboardButton]] = []
    lines = ["👥 Администраторы\n"]
    for uid in sorted(settings.admin_ids):
        record = None
        try:
            record = await db.get_user(uid)
        except Exception:
            logger.exception("Could not load owner record")
        lines.append(f"• <b>{_name(record, 'Владелец')}</b> (ID <code>{uid}</code>) — владелец")
    db_admins = []
    try:
        users_page = await db.list_users(offset=0, limit=200)
    except Exception:
        users_page = []
    for record in users_page:
        if record.is_admin and record.user_id not in settings.admin_ids:
            db_admins.append(record)
            lines.append(f"• {_name(record)} (ID <code>{record.user_id}</code>)")
            if is_owner(query.from_user.id, context):
                rows.append([InlineKeyboardButton(f"🗑 Лишить прав {record.user_id} — {record.first_name or '?'}", callback_data=f"adm:revoke_admin:{record.user_id}")])
    if not db_admins:
        lines.append("Нет дополнительных администраторов.")
    if is_owner(query.from_user.id, context):
        rows.append([InlineKeyboardButton("➕ Добавить администратора", callback_data="adm:add_admin_prompt")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm:menu")])
    await query.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def add_admin_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_owner(query.from_user.id, context):
        await query.message.reply_text("Только владелец может выдавать права администратора.")
        return
    context.user_data["admin_wait"] = "add_admin"
    await query.message.reply_text("Пришлите ID или @username пользователя, которому выдать права администратора.")


async def _add_admin_by_text(update: Update, context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    message = update.effective_message
    if not message:
        return
    user_id, _ = _parse_target(value)
    if user_id is None:
        await message.reply_text("Не нашёл пользователя.")
        return
    if isinstance(user_id, str):
        user_id = await _resolve_target(get_database(context), user_id)
        if user_id is None:
            await message.reply_text("Пользователь не найден в базе. Пришлите числовой ID.")
            return
    await get_database(context).set_db_admin(user_id, True)
    await message.reply_text(f"✅ Пользователь <b>{user_id}</b> теперь администратор.", parse_mode="HTML")


async def revoke_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    query = update.callback_query
    if not query:
        return
    if not is_owner(query.from_user.id, context):
        await query.answer("Нет прав", show_alert=True)
        return
    await get_database(context).set_db_admin(user_id, False)
    await query.answer("Права отозваны")
    await show_admins(update, context)


async def dispatch_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Route an admin-panel callback. Returns True when the data belongs to the panel."""
    query = update.callback_query
    if not query or not query.data:
        return False
    data = query.data
    if not data.startswith("adm:"):
        return False
    if not await is_admin(query.from_user.id, context):
        await query.answer("Нет доступа", show_alert=True)
        return True

    if data == "adm:menu":
        await _open_menu(query.message, context)
    elif data == "adm:stats":
        await show_stats(update, context)
    elif data == "adm:channels":
        await show_channels(update, context)
    elif data == "adm:channel_add":
        context.user_data["admin_wait"] = "channel_add"
        await query.answer()
        await query.message.reply_text(
            "Пришлите @username канала или ссылку на него.\n\n"
            "⚠️ Бот должен быть администратором канала."
        )
    elif data == "adm:ban_menu":
        await show_ban_menu(update, context)
    elif data == "adm:ban_prompt":
        await ban_prompt(update, context, unban=False)
    elif data == "adm:unban_prompt":
        await ban_prompt(update, context, unban=True)
    elif data == "adm:voice":
        await show_voice(update, context)
    elif data == "adm:voice_on":
        await set_voice(update, context, True)
    elif data == "adm:voice_off":
        await set_voice(update, context, False)
    elif data == "adm:add_admin_prompt":
        await add_admin_prompt(update, context)
    elif data == "adm:admins":
        await show_admins(update, context)
    elif data.startswith("adm:list:"):
        try:
            page = int(data.split(":", 2)[2])
        except (IndexError, ValueError):
            page = 0
        await list_users(update, context, page)
    elif data.startswith("adm:channel_del:"):
        await delete_channel(update, context, int(data.rsplit(":", 1)[1]))
    elif data.startswith("adm:ban:"):
        await quick_ban(update, context, int(data.rsplit(":", 1)[1]))
    elif data.startswith("adm:un:"):
        await quick_unban(update, context, int(data.rsplit(":", 1)[1]))
    elif data.startswith("adm:revoke_admin:"):
        await revoke_admin(update, context, int(data.rsplit(":", 1)[1]))
    else:
        await query.answer()
    return True
