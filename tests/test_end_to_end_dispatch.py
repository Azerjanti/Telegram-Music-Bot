"""End-to-end: real python-telegram-bot dispatch through register_handlers().

Nothing here is mocked except the Bot API transport itself, so this proves the
handlers added/changed for the four issues are actually reachable from a real
telegram.Update.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

import pytest
from telegram import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
    User,
)
from telegram.ext import Application

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fakes import FakeBot, make_downloaded  # noqa: E402
from music_bot.config import Settings  # noqa: E402
from music_bot.database import Database  # noqa: E402
from music_bot.main import register_handlers  # noqa: E402
from music_bot.services.music import MusicService  # noqa: E402

OWNER_ID = 555
NOW = dt.datetime.now(dt.timezone.utc)


class RecordingBot(FakeBot):
    """FakeBot plus the attributes PTB's Application touches."""

    def __init__(self) -> None:
        super().__init__(id=4242, username="music_bot")
        self.sent = []
        self.answered = []
        self.edited_markups = []

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, **kwargs):
        message = await super().send_message(
            chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
        )
        self.sent.append(message)
        return message

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None, parse_mode=None, **kwargs):
        await super().edit_message_text(
            chat_id, message_id, text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
        )
        self.edited_markups.append(reply_markup)
        return True

    async def answer_callback_query(self, callback_query_id, text=None, show_alert=False, **kwargs):
        self.answered.append({"text": text, "show_alert": show_alert})
        return True

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None, **kwargs):
        self.edited_markups.append(reply_markup)
        return True

    async def delete_message(self, chat_id, message_id, **kwargs):
        return True


class Provider:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path

    async def search(self, query, limit=5):
        from music_bot.downloader import SearchResult

        return [
            SearchResult(title=f"Track {i}", artist=query, url=f"https://youtu.be/{i}")
            for i in range(1, min(limit, 5) + 1)
        ]

    async def download(self, query):
        path = self.tmp_path / "t.mp3"
        path.write_bytes(b"ID3data")
        return make_downloaded(path)


@pytest.fixture
async def app(tmp_path):
    db = Database(None, tmp_path / "e2e.sqlite3")
    await db.connect()
    settings = Settings(
        bot_token="4242:token",
        database_url=None,
        supabase_url=None,
        supabase_key=None,
        port=8000,
        cache_dir=tmp_path / "audio",
        enable_ytdlp=True,
        enable_shazam=True,
        max_concurrent_downloads=2,
        download_retries=3,
        max_telegram_file_mb=50,
        admin_ids=frozenset({OWNER_ID}),
    )
    application = Application.builder().token("4242:token").build()
    # `bot` is a plain slot on Application, so the transport can be swapped.
    application.bot = RecordingBot()
    # Skip Application.initialize(): it would call bot.get_me() over the network.
    application._initialized = True
    application.bot_data["database"] = db
    application.bot_data["settings"] = settings
    application.bot_data["enable_shazam"] = True
    application.bot_data["music_service"] = MusicService(
        database=db, provider=Provider(tmp_path), max_file_mb=50, max_concurrent_downloads=2
    )
    register_handlers(application)
    yield application, db
    await db.close()


def _user(id: int, username: str | None = None) -> User:
    return User(id=id, first_name="Test", is_bot=False, username=username)


def _chat(id: int) -> Chat:
    return Chat(id=id, type="private")


def _text_update(user: User, text: str, chat_id: int = 100) -> Update:
    # A real Telegram update marks commands with a bot_command entity; without
    # it filters.COMMAND (and therefore CommandHandler) never matches.
    entities = None
    if text.startswith("/"):
        entities = (MessageEntity(MessageEntity.BOT_COMMAND, 0, len(text.split()[0])),)
    message = Message(
        message_id=1,
        date=NOW,
        chat=_chat(chat_id),
        from_user=user,
        text=text,
        entities=entities,
    )
    message.set_bot(_BOT_HOLDER["bot"])
    return Update(update_id=1, message=message)


def _callback_update(user: User, data: str, chat_id: int = 100) -> Update:
    message = Message(message_id=7, date=NOW, chat=_chat(chat_id), from_user=user)
    # PTB stores the bot on the message; answer()/edit_* go through it.
    message.set_bot(_BOT_HOLDER["bot"])
    query = CallbackQuery(
        id="cbq", from_user=user, chat_instance="ci", data=data, message=message
    )
    query.set_bot(_BOT_HOLDER["bot"])
    return Update(update_id=2, callback_query=query)


_BOT_HOLDER: dict = {}


@pytest.mark.asyncio
async def test_admin_command_opens_panel_for_configured_id(app):
    application, _db = app
    _BOT_HOLDER["bot"] = application.bot
    owner = _user(OWNER_ID, "owner")

    await application.process_update(_text_update(owner, "/admin", chat_id=OWNER_ID))

    bot: RecordingBot = application.bot
    panels = [m for m in bot.sent_messages if "Панель администратора" in (m.text or "")]
    assert panels, f"no admin panel was sent; got {[m.text for m in bot.sent_messages]}"
    assert "adm:stats" in panels[-1].callback_data


@pytest.mark.asyncio
async def test_admin_command_reveals_id_to_stranger(app):
    application, _db = app
    _BOT_HOLDER["bot"] = application.bot
    stranger = _user(424242, "stranger")

    await application.process_update(_text_update(stranger, "/admin", chat_id=424242))

    reply = application.bot.sent_messages[-1]
    assert "424242" in reply.text


@pytest.mark.asyncio
async def test_id_command_is_registered(app):
    application, _db = app
    _BOT_HOLDER["bot"] = application.bot
    user = _user(31337, "someone")

    await application.process_update(_text_update(user, "/id"))

    assert "31337" in application.bot.sent_messages[-1].text


@pytest.mark.asyncio
async def test_stats_button_reaches_the_panel(app):
    application, db = app
    _BOT_HOLDER["bot"] = application.bot
    await db.register_user(user_id=OWNER_ID, username="owner", first_name="Owner")
    owner = _user(OWNER_ID, "owner")

    await application.process_update(_callback_update(owner, "adm:stats", chat_id=OWNER_ID))

    bot: RecordingBot = application.bot
    assert bot.answered, "the callback was never answered"
    texts = [e for e in bot.edited_messages if isinstance(e.get("text"), str)]
    assert any("Статистика бота" in t["text"] for t in texts), bot.edited_messages


@pytest.mark.asyncio
async def test_downloaded_track_carries_heart_and_cross(app):
    application, _db = app
    _BOT_HOLDER["bot"] = application.bot
    user = _user(777, "listener")

    # 1) the user searches
    await application.process_update(_text_update(user, "some new song"))
    listing = application.bot.sent_messages[-1]
    assert "Выберите песню" in listing.text, listing.text
    first = listing.callback_data[0]
    assert first.startswith("result:"), listing.callback_data

    # 2) the user picks a result -> the bot downloads and sends the audio
    await application.process_update(_callback_update(user, first))

    audios = [m for m in application.bot.sent_messages if m.audio is not None]
    assert audios, [m.text for m in application.bot.sent_messages]
    audio = audios[-1]
    labels = [
        b.text
        for row in (audio.initial_markup or InlineKeyboardMarkup([])).inline_keyboard
        for b in row
    ]
    assert "❤️" in labels and ("❌" in labels or "⏪" in labels), labels
    callbacks = audio.initial_callback_data
    assert any(c.startswith(("favorite:add:", "favnew:a:")) for c in callbacks), callbacks
    assert "track:close" in callbacks


@pytest.mark.asyncio
async def test_cross_button_callback_is_dispatched(app):
    application, _db = app
    _BOT_HOLDER["bot"] = application.bot
    user = _user(777, "listener")

    await application.process_update(_callback_update(user, "track:close"))

    assert application.bot.answered, "❌ must answer the callback"
    # the keyboard removal went through the bot as an edit, not a new message
    assert application.bot.edited_markups, "the message was never edited"
    assert application.bot.edited_markups[-1] is None, "the ❌ row was not removed"
