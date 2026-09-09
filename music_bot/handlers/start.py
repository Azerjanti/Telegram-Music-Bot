import logging

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from music_bot.access import ensure_access, is_admin, register_user

logger = logging.getLogger(__name__)


BTN_SEARCH = "🔍 Поиск песни"
BTN_TOP = "🏆 Топ 100"
BTN_ARTIST = "👤 По исполнителю"
BTN_FAVORITES = "❤️ Избранное"
BTN_LOCAL_TOP = "📈 Локальный топ"
BTN_VOICE = "🎤 Поиск по голосу"

MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_SEARCH), KeyboardButton(BTN_TOP)],
        [KeyboardButton(BTN_ARTIST), KeyboardButton(BTN_FAVORITES)],
        [KeyboardButton(BTN_LOCAL_TOP)],
        [KeyboardButton(BTN_VOICE)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# Old labels (without emoji) still match so leftover keyboards keep working.
_SEARCH = {BTN_SEARCH, "Поиск песни", "🎵 Поиск песни"}
_ARTIST = {BTN_ARTIST, "По исполнителю", "Поиск по исполнителю"}
_VOICE = {BTN_VOICE, "Поиск по голосу"}
_TOP = {BTN_TOP, "Топ 100"}
_LOCAL = {BTN_LOCAL_TOP, "Локальный топ"}
_FAVORITES = {BTN_FAVORITES, "Избранное"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    user = update.effective_user
    if user and not user.is_bot:
        try:
            record = await register_user(update, context)
        except Exception:
            logger.exception("Could not register user")
            record = None
        banned = bool(record and record.is_banned)
        if banned:
            await message.reply_text(
                "⛔️ Вы заблокированы и не можете пользоваться ботом."
            )
            return
        if record and record.blocked_at:
            # User came back after unblocking the bot - clear the auto-detection.
            try:
                await context.application.bot_data["database"].clear_user_blocked(user.id)
            except Exception:
                logger.debug("Could not clear blocked marker", exc_info=True)

    admin_note = ""
    if user and await is_admin(user.id, context):
        admin_note = "\n\n🛠 Вы администратор — доступна команда /admin."
    await message.reply_text(
        "Добро пожаловать в музыкальный бот!\n"
        "Выбери одну из опций ниже:" + admin_note,
        reply_markup=MENU,
    )


async def menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    if not await ensure_access(update, context):
        return
    choice = (message.text or "").strip()
    if choice in _SEARCH:
        context.user_data["mode"] = "song"
        await message.reply_text("Напишите название песни или исполнителя.")
    elif choice in _ARTIST:
        context.user_data["mode"] = "artist"
        await message.reply_text("Напишите имя исполнителя.")
    elif choice in _VOICE:
        context.user_data["mode"] = "voice"
        await message.reply_text("Отправьте голосовое сообщение или аудиофайл.")
    elif choice in _TOP:
        from music_bot.handlers.search import send_spotify_top

        await send_spotify_top(update, context, limit=50)
    elif choice in _LOCAL:
        from music_bot.handlers.search import send_local_top

        await send_local_top(update, context, limit=50, title="Локальный топ")
    elif choice in _FAVORITES:
        from music_bot.handlers.favorites import show_favorites

        await show_favorites(update, context)
