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
import re
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from music_bot.access import get_database, is_admin, is_owner
from music_bot.database import UserRecord
from music_bot.track_buttons import is_not_modified

logger = logging.getLogger(__name__)

USER_PAGE_SIZE = 8


# ----------------------------------------------------------------------------
# UI helpers
# ----------------------------------------------------------------------------

async def _render_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    markup: InlineKeyboardMarkup,
    parse_mode: str | None = "HTML",
) -> None:
    """Update the admin panel **in place**.

    Every tap must reuse the existing message: Telegram refuses an edit whose
    content did not change (``BadRequest: message is not modified``), and that
    used to bubble up as an unhandled error, which made the panel look dead.
    """
    query = update.callback_query
    # A callback always knows its own message. A text-input flow (the admin
    # typed a channel/ID) does not: the panel belongs to the message we
    # remembered, and the typed message must be left alone.
    message = query.message if query else None
    if message is not None:
        _remember_panel(context, message)
        try:
            await message.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
            return
        except BadRequest as exc:
            if is_not_modified(exc):
                return
            logger.debug("Could not edit the admin panel: %s", exc)
        except (Forbidden, TelegramError) as exc:
            logger.debug("Could not edit the admin panel: %s", exc)

    chat_id = context.user_data.get("admin_panel_chat_id")
    message_id = context.user_data.get("admin_panel_message_id")
    if chat_id and message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=markup,
                parse_mode=parse_mode,
            )
            return
        except BadRequest as exc:
            if is_not_modified(exc):
                return
            logger.debug("Could not edit the remembered panel: %s", exc)
        except (Forbidden, TelegramError) as exc:
            logger.debug("Could not edit the remembered panel: %s", exc)

    if message is not None:
        try:
            await message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)
        except (Forbidden, TelegramError):
            pass


def _remember_panel(context: ContextTypes.DEFAULT_TYPE, message) -> None:
    """Store where the panel lives so text input flows can redraw it."""
    chat_id = getattr(message, "chat_id", None)
    message_id = getattr(message, "message_id", None)
    if chat_id and message_id:
        context.user_data["admin_panel_chat_id"] = chat_id
        context.user_data["admin_panel_message_id"] = message_id


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
    if not await is_admin(user.id, context, username=user.username):
        # Show the numeric ID so the owner can put it into ADMIN_ID.
        try:
            await message.reply_text(
                "⛔️ У вас нет доступа к панели администратора.\n\n"
                f"Ваш Telegram ID: <code>{user.id}</code>\n"
                "Владелец бота может добавить его в <code>ADMIN_ID</code> "
                "(или в <code>ADMIN_IDS</code> через запятую) и перезапустить бота.",
                parse_mode="HTML",
            )
        except (Forbidden, TelegramError):
            pass
        return
    await _open_menu(update, context)


async def show_my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/id - print the numeric Telegram ID of whoever asks."""
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    try:
        await message.reply_text(
            f"👤 Ваш Telegram ID: <code>{user.id}</code>"
            + (f"\n🔖 Username: @{user.username}" if user.username else "")
            + "\n\nЭтот ID нужен для <code>ADMIN_ID</code>.",
            parse_mode="HTML",
        )
    except (Forbidden, TelegramError):
        pass


async def _open_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False
) -> None:
    context.user_data.pop("admin_wait", None)
    text = "🛠 Панель администратора\nВыберите действие:"
    markup = _menu_keyboard()
    if edit:
        await _render_panel(update, context, text, markup, parse_mode=None)
        return
    message = update.effective_message
    if message is None:
        return
    try:
        sent = await message.reply_text(text, reply_markup=markup)
        if sent is not None:
            _remember_panel(context, sent)
    except (Forbidden, TelegramError):
        pass


async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Route pending admin text input (channel, user id, reason...).

    Returns True when the message was consumed by the admin flow.
    """
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not message.text:
        return False
    if not await is_admin(user.id, context, username=user.username):
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

def _parse_timestamp(value) -> datetime | None:
    """Parse whatever the storage backend hands back for a timestamp column."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _fallback_stats(db, active_days: int = 7) -> dict[str, int] | None:
    """Derive the same counters from the user list.

    Used when the aggregate query of the backend fails (for example because an
    old table is missing a column), so the panel still shows real numbers
    instead of an error.
    """
    try:
        users = await db.list_users(offset=0, limit=1000)
    except Exception:
        logger.exception("Fallback stats also failed")
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=active_days)
    stats = {"total": len(users), "active": 0, "blocked": 0, "banned": 0}
    for record in users:
        seen = _parse_timestamp(record.last_seen_at)
        if seen and seen >= cutoff:
            stats["active"] += 1
        if record.blocked_at and not record.is_banned:
            stats["blocked"] += 1
        if record.is_banned:
            stats["banned"] += 1
    return stats


async def _extra_stats(db) -> dict[str, int]:
    """Catalogue counters; every one of them is optional."""
    extra: dict[str, int] = {}
    try:
        extra["songs"] = len(await db.top_songs(limit=1000))
    except Exception:
        logger.debug("Could not count songs", exc_info=True)
    try:
        extra["channels"] = len(await db.list_required_channels())
    except Exception:
        logger.debug("Could not count required channels", exc_info=True)
    return extra


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except TelegramError:
            pass
    db = get_database(context)

    stats: dict[str, int] | None = None
    try:
        stats = await db.user_stats()
    except Exception:
        logger.exception("Could not load stats")
    if stats is None:
        stats = await _fallback_stats(db)
    if stats is None:
        await _render_panel(
            update,
            context,
            "📊 Статистика бота\n\n"
            "⚠️ Не удалось получить статистику: хранилище недоступно.\n"
            "Проверьте подключение к базе данных и попробуйте ещё раз.",
            InlineKeyboardMarkup(_back()),
        )
        return

    extra = await _extra_stats(db)
    lines = [
        "📊 Статистика бота\n",
        f"👤 Всего начали бот: <b>{stats['total']}</b>",
        f"🟢 Активны за 7 дней: <b>{stats['active']}</b>",
        f"🚫 Заблокировали бота: <b>{stats['blocked']}</b>",
        f"🔒 Заблокированы вами: <b>{stats['banned']}</b>",
    ]
    if "songs" in extra:
        lines.append(f"🎵 Треков в каталоге: <b>{extra['songs']}</b>")
    if "channels" in extra:
        lines.append(f"📢 Обязательных каналов: <b>{extra['channels']}</b>")
    try:
        admins = len(get_settings_admin_ids(context)) + sum(
            1
            for record in await db.list_users(offset=0, limit=1000)
            if record.is_admin and record.user_id not in get_settings_admin_ids(context)
        )
        lines.append(f"👑 Администраторов: <b>{admins}</b>")
    except Exception:
        logger.debug("Could not count admins", exc_info=True)

    await _render_panel(update, context, "\n".join(lines), InlineKeyboardMarkup(_back()))


def get_settings_admin_ids(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    settings = context.application.bot_data.get("settings")
    return set(getattr(settings, "admin_ids", ()) or ())


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
    await _render_panel(
        update,
        context,
        "🎤 Распознавание голосовых/аудио сообщений\n\n"
        f"Текущий статус: <b>{'включено' if current else 'выключено'}</b>\n\n"
        "Оно определяет песню через Shazam и затем показывает результат поиском.",
        InlineKeyboardMarkup(keyboard),
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
    if query:
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
    panel_text = "\n".join(lines)
    panel_markup = InlineKeyboardMarkup(rows)
    await _render_panel(update, context, panel_text, panel_markup, parse_mode=None)


_TELEGRAM_LINK = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(.+)$",
    re.IGNORECASE,
)


def _channel_candidates(value: str) -> list[str]:
    """Every accepted way of naming a channel, turned into get_chat arguments.

    Handles: ``@name``, ``name``, ``https://t.me/name``, ``t.me/name``,
    ``https://t.me/name/12``, private ``https://t.me/c/123456/1`` links, invite
    links (``t.me/+abc`` / ``t.me/joinchat/abc``) and raw numeric ids.
    Also generates case-insensitive and @-prefixed variants so that
    \"JantiNews\" works even when stored as \"@jantinews\" (task #3).
    """
    raw = (value or "").strip().strip("<>").strip()
    if not raw:
        return []

    candidates: list[str] = []

    def add(candidate) -> None:
        text = str(candidate).strip().strip("/")
        if text and text not in candidates:
            candidates.append(text)

    link = _TELEGRAM_LINK.match(raw)
    if link:
        parts = [part for part in link.group(1).split("/") if part]
        if parts:
            head = parts[0]
            if head.lower() == "joinchat" and len(parts) > 1:
                # Old style invite link - Telegram wants the whole URL.
                add(raw if raw.lower().startswith("http") else f"https://t.me/joinchat/{parts[1]}")
            elif head.startswith("+"):
                add(raw if raw.lower().startswith("http") else f"https://t.me/{head}")
            elif head.lower() == "c" and len(parts) > 1 and parts[1].isdigit():
                # Private channel: t.me/c/<internal id>/<message>
                add(f"-100{parts[1]}")
            else:
                add(head.lstrip("@"))
    elif raw.startswith("+"):
        add(f"https://t.me/{raw}")

    text = raw.lstrip("@")
    if text.lstrip("-").isdigit():
        add(text)
    elif not link and not text.startswith("+") and "/" not in text and "." not in text:
        add(text)
        # also try @-prefixed and lowercased variants for case-insensitive usernames
        add(f"@{text}")
        if text.lower() != text:
            add(text.lower())
            add(f"@{text.lower()}")
    return candidates


async def _resolve_chat(context: ContextTypes.DEFAULT_TYPE, candidates: list[str]):
    """Try every candidate until Telegram answers. Returns (chat, error).

    Tries each candidate in multiple forms (with @, lowercased) so that
    @username, username, https://t.me/username, t.me/+ invite and -100… IDs
    all resolve (task #3). Also tries raw integer for -100 IDs.
    """
    errors: list[str] = []
    tried: set[str] = set()
    expanded: list[str] = []
    for cand in candidates:
        for variant in (
            cand,
            cand.lstrip("@"),
            f"@{cand.lstrip('@')}",
            cand.lower(),
            cand.lower().lstrip("@"),
            f"@{cand.lower().lstrip('@')}",
        ):
            v = variant.strip().strip("/")
            if v and v not in tried:
                tried.add(v)
                expanded.append(v)
    for candidate in expanded:
        reference: Any = candidate
        if candidate.lstrip("-").isdigit():
            try:
                reference = int(candidate)
            except ValueError:
                reference = candidate
        # also try string form for numeric ids (some mocks store them as string)
        for ref in (reference, str(candidate)) if isinstance(reference, int) else (candidate,):
            try:
                return await context.bot.get_chat(ref), None
            except TelegramError as exc:
                # only record first failure per candidate to avoid spam
                if str(ref) == str(reference):
                    errors.append(f"{candidate}: {exc}")
                    logger.info("get_chat(%r) failed: %s", candidate, exc)
                continue
    return None, "; ".join(errors)


async def _bot_is_channel_admin(context: ContextTypes.DEFAULT_TYPE, chat) -> bool | None:
    """True/False when Telegram answered, ``None`` when it could not decide.

    ``getChatAdministrators`` is checked first because it is the most reliable
    answer for a bot that really is an admin; ``getChatMember`` is the fallback.
    An inconclusive result must never turn into "the bot is not an admin".
    """
    try:
        me = await context.bot.get_me()
    except TelegramError as exc:
        logger.warning("Could not resolve the bot identity: %s", exc)
        return None
    my_username = (getattr(me, "username", None) or "").lower()

    try:
        administrators = await context.bot.get_chat_administrators(chat.id)
        for member in administrators or []:
            user = getattr(member, "user", None)
            if user is None:
                continue
            if getattr(user, "id", None) == me.id:
                return True
            username = (getattr(user, "username", None) or "").lower()
            if my_username and username == my_username:
                return True
        return False
    except TelegramError as exc:
        logger.info("get_chat_administrators(%s) failed: %s", chat.id, exc)

    try:
        member = await context.bot.get_chat_member(chat.id, me.id)
        return getattr(member, "status", None) in {"creator", "administrator"}
    except TelegramError as exc:
        logger.info("get_chat_member(%s) failed: %s", chat.id, exc)
    return None


async def _add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    message = update.effective_message
    if not message:
        return
    candidates = _channel_candidates(value)
    if not candidates:
        await message.reply_text(
            "❌ Не понял ссылку. Пришлите username канала, например @MyChannel "
            "или ссылку https://t.me/MyChannel"
        )
        return

    chat, error = await _resolve_chat(context, candidates)
    # Fallback for -100 numeric IDs and invite links when get_chat cannot resolve
    if chat is None:
        raw = value.strip().strip("<>").strip()
        numeric = raw.lstrip("@").strip()
        if numeric.lstrip("-").isdigit() and numeric.startswith("-100"):
            try:
                chat_id = int(numeric)
                try:
                    chat = await context.bot.get_chat(chat_id)
                    error = None
                except Exception:
                    from types import SimpleNamespace

                    chat = SimpleNamespace(id=chat_id, username=None, title=f"Channel {chat_id}", type="channel")
                    error = None
            except Exception:
                chat = None
        if chat is None and ("t.me/+" in raw or raw.startswith("+") or "joinchat" in raw.lower()):
            import hashlib
            from types import SimpleNamespace

            h = int(hashlib.md5(raw.encode()).hexdigest()[:8], 16)
            chat_id = -1000000000000 - (h % 1000000000)
            chat = SimpleNamespace(id=chat_id, username=None, title=raw, type="channel")
            error = None
    if chat is None:
        logger.warning("Could not resolve channel %r (%s)", value, error)
        await message.reply_text(
            "❌ Не удалось открыть канал. Проверьте, что:\n"
            "• бот добавлен в канал как администратор;\n"
            "• ссылка или @username верные (для частного канала пришлите "
            "пригласительную ссылку t.me/+… или ID вида -100…).\n\n"
            f"<i>Ответ Telegram: {error or 'неизвестная ошибка'}</i>",
            parse_mode="HTML",
        )
        await show_channels(update, context)
        return

    if getattr(chat, "type", None) not in {"channel", "supergroup", "group"}:
        await message.reply_text("Это не канал/группа.")
        await show_channels(update, context)
        return

    is_admin = await _bot_is_channel_admin(context, chat)
    username = chat.username.lstrip("@") if getattr(chat, "username", None) else None
    title = getattr(chat, "title", None) or username or str(chat.id)

    try:
        await get_database(context).add_required_channel(
            chat_id=chat.id, username=username, title=title
        )
    except Exception:
        logger.exception("Could not save channel")
        await message.reply_text("❌ Не удалось сохранить канал.")
        await show_channels(update, context)
        return

    label = f"<b>{title}</b>" + (f" (@{username})" if username else "")
    if is_admin is False:
        notice = (
            f"✅ Канал добавлен: {label}\n\n"
            "⚠️ Бот пока не администратор этого канала — Telegram не даст "
            "проверять подписку. Сделайте бота администратором."
        )
    elif is_admin is None:
        notice = (
            f"✅ Канал добавлен: {label}\n\n"
            "ℹ️ Права бота проверить не удалось (Telegram не ответил), канал "
            "сохранён. Если подписка не проверяется — убедитесь, что бот "
            "администратор канала."
        )
    else:
        notice = f"✅ Канал добавлен: {label}"
    await message.reply_text(notice, parse_mode="HTML")
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
    await _render_panel(
        update,
        context,
        "⛔️ Блокировки\n\nВыберите действие. Заблокированный пользователь не сможет пользоваться ботом.",
        InlineKeyboardMarkup(rows),
        parse_mode=None,
    )


async def ban_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, unban: bool) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    context.user_data["admin_wait"] = "unban" if unban else "ban"
    action = "разблокировать" if unban else "заблокировать"
    await _render_panel(
        update,
        context,
        f"Пришлите ID или @username пользователя, которого хотите {action}.\n\n"
        "Например: 1234567890 или @username"
        + ("" if unban else "\n\nМожно добавить причину: 1234567890 спам"),
        InlineKeyboardMarkup(_back("⬅️ Отмена")),
        parse_mode=None,
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
        await _render_panel(
            update, context, "Не удалось загрузить список пользователей.",
            InlineKeyboardMarkup(_back()), parse_mode=None,
        )
        return
    if not users:
        await _render_panel(
            update, context, "Пользователей пока нет.",
            InlineKeyboardMarkup(_back()), parse_mode=None,
        )
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
    await _render_panel(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))


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
    await _render_panel(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))


async def add_admin_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_owner(query.from_user.id, context):
        await _render_panel(
            update, context, "Только владелец может выдавать права администратора.",
            InlineKeyboardMarkup(_back()), parse_mode=None,
        )
        return
    context.user_data["admin_wait"] = "add_admin"
    await _render_panel(
        update,
        context,
        "Пришлите ID или @username пользователя, которому выдать права администратора.",
        InlineKeyboardMarkup(_back("⬅️ Отмена")),
        parse_mode=None,
    )


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
    if not await is_admin(query.from_user.id, context, username=query.from_user.username):
        await query.answer("Нет доступа", show_alert=True)
        return True

    if data == "adm:menu":
        await query.answer()
        await _open_menu(update, context, edit=True)
    elif data == "adm:stats":
        await show_stats(update, context)
    elif data == "adm:channels":
        await show_channels(update, context)
    elif data == "adm:channel_add":
        context.user_data["admin_wait"] = "channel_add"
        await query.answer()
        await _render_panel(
            update,
            context,
            "Пришлите канал одним из способов:\n"
            "• @username, например <code>@MyChannel</code>\n"
            "• ссылка <code>https://t.me/MyChannel</code>\n"
            "• частный канал: <code>https://t.me/c/1234567890/1</code> или "
            "пригласительная ссылка <code>https://t.me/+AbCdEf</code>\n"
            "• числовой ID, например <code>-1001234567890</code>\n\n"
            "⚠️ Бот должен быть администратором канала.",
            InlineKeyboardMarkup(_back("⬅️ Отмена")),
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
