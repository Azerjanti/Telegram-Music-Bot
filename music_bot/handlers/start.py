from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes


MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Поиск песни"), KeyboardButton("Топ 100")],
        [KeyboardButton("По исполнителю"), KeyboardButton("Избранное")],
        [KeyboardButton("Локальный топ")],
        [KeyboardButton("Поиск по голосу")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "Добро пожаловать в музыкальный бот!\nВыбери одну из опций ниже:",
            reply_markup=MENU,
        )


async def menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    choice = message.text or ""
    if choice == "Поиск песни":
        context.user_data["mode"] = "song"
        await message.reply_text("Напишите название песни или исполнителя.")
    elif choice in {"Поиск по исполнителю", "По исполнителю"}:
        context.user_data["mode"] = "artist"
        await message.reply_text("Напишите имя исполнителя.")
    elif choice == "Поиск по голосу":
        context.user_data["mode"] = "voice"
        await message.reply_text("Отправьте голосовое сообщение или аудиофайл.")
    elif choice == "Топ 100":
        from music_bot.handlers.search import send_spotify_top

        await send_spotify_top(update, context, limit=50)
    elif choice == "Локальный топ":
        from music_bot.handlers.search import send_local_top

        await send_local_top(update, context, limit=50, title="Локальный топ")
    elif choice == "Избранное":
        from music_bot.handlers.favorites import show_favorites

        await show_favorites(update, context)