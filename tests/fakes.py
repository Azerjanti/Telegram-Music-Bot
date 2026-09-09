"""Lightweight Telegram stand-ins used by the handler tests.

They are intentionally minimal: they only implement the handful of Bot API
methods the production code calls, and they record every call so tests can
assert *what the bot would have sent* (text, markup, edits vs. new messages).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardMarkup


class SentMessage:
    """A message the bot 'sent'. Mirrors telegram.Message's mutable surface."""

    def __init__(
        self,
        chat_id: int = 100,
        message_id: int = 1,
        text: str | None = None,
        audio: Any = None,
        reply_markup: InlineKeyboardMarkup | None = None,
        caption: str | None = None,
        bot: "FakeBot | None" = None,
    ) -> None:
        self.chat_id = chat_id
        self.message_id = message_id
        self.text = text
        self.caption = caption
        self.audio = audio
        self.reply_markup = reply_markup
        # What the markup was when the message was first sent (before any edit).
        self.initial_markup = reply_markup
        self.bot = bot
        self.deleted = False
        self.edits: list[dict[str, Any]] = []
        self.replies: list["SentMessage"] = []

    # -- helpers used by tests ------------------------------------------------
    @property
    def initial_callback_data(self) -> list[str]:
        if not self.initial_markup:
            return []
        return [
            button.callback_data
            for row in self.initial_markup.inline_keyboard
            for button in row
            if button.callback_data
        ]

    @property
    def initial_button_labels(self) -> list[str]:
        if not self.initial_markup:
            return []
        return [
            button.text for row in self.initial_markup.inline_keyboard for button in row
        ]

    @property
    def callback_data(self) -> list[str]:
        if not self.reply_markup:
            return []
        return [
            button.callback_data
            for row in self.reply_markup.inline_keyboard
            for button in row
            if button.callback_data
        ]

    @property
    def button_labels(self) -> list[str]:
        if not self.reply_markup:
            return []
        return [
            button.text
            for row in self.reply_markup.inline_keyboard
            for button in row
        ]

    async def reply_text(self, text: str, reply_markup=None, parse_mode=None, **kwargs):
        message = SentMessage(
            chat_id=self.chat_id,
            message_id=_next_message_id(self.chat_id),
            text=text,
            reply_markup=reply_markup,
            bot=self.bot,
        )
        self.replies.append(message)
        if self.bot is not None:
            self.bot.sent_messages.append(message)
            self.bot.register_message(message)
        return message

    async def reply_audio(
        self,
        audio=None,
        title=None,
        performer=None,
        caption=None,
        reply_markup=None,
        **kwargs,
    ):
        message_id = _next_message_id(self.chat_id)
        # Telegram always returns an Audio object with a server file_id, even
        # when the caller uploaded a raw file handle.
        if isinstance(audio, str):
            returned_audio: Any = FakeAudio(file_id=audio)
        elif audio is not None and hasattr(audio, "file_id"):
            returned_audio = audio
        else:
            returned_audio = FakeAudio(file_id=f"AgACAgIAAxk-uploaded-{message_id}")
        message = SentMessage(
            chat_id=self.chat_id,
            message_id=message_id,
            audio=returned_audio,
            reply_markup=reply_markup,
            caption=caption,
            bot=self.bot,
        )
        message.audio_title = title
        message.audio_performer = performer
        self.replies.append(message)
        if self.bot is not None:
            self.bot.sent_messages.append(message)
            self.bot.register_message(message)
        return message

    async def edit_text(self, text: str, reply_markup=None, parse_mode=None, **kwargs):
        self.edits.append({"text": text, "reply_markup": reply_markup, "parse_mode": parse_mode})
        self.text = text
        if reply_markup is not None:
            self.reply_markup = reply_markup
        return self

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self.edits.append({"reply_markup": reply_markup})
        self.reply_markup = reply_markup
        return self

    async def edit_caption(self, caption=None, reply_markup=None, **kwargs):
        self.edits.append({"caption": caption, "reply_markup": reply_markup})
        self.caption = caption
        if reply_markup is not None:
            self.reply_markup = reply_markup
        return self

    async def delete(self, **kwargs):
        self.deleted = True
        return True


class FakeAudio:
    """Stands in for telegram.Audio (only file_id is used by the bot)."""

    def __init__(self, file_id: str = "AgACAgIAAxk-file-id") -> None:
        self.file_id = file_id


_MESSAGE_COUNTER: dict[int, int] = {}


def _next_message_id(chat_id: int) -> int:
    _MESSAGE_COUNTER[chat_id] = _MESSAGE_COUNTER.get(chat_id, 0) + 1
    return _MESSAGE_COUNTER[chat_id]


def reset_message_counter() -> None:
    _MESSAGE_COUNTER.clear()


class FakeUser:
    def __init__(
        self,
        id: int,
        username: str | None = None,
        first_name: str = "Test",
        is_bot: bool = False,
    ) -> None:
        self.id = id
        self.username = username
        self.first_name = first_name
        self.last_name: str | None = None
        self.is_bot = is_bot


class FakeBot:
    def __init__(self, id: int = 4242, username: str = "music_bot") -> None:
        self.id = id
        self.username = username
        self.sent_messages: list[SentMessage] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.chat_members: dict[tuple[int, int], Any] = {}
        self.chats: dict[Any, Any] = {}
        # (chat_id, message_id) -> message, so bot.edit_message_text can reach it
        self.message_by_id: dict[tuple[int, int], "SentMessage"] = {}

    def register_message(self, message: "SentMessage") -> "SentMessage":
        self.message_by_id[(message.chat_id, message.message_id)] = message
        return message

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, **kwargs):
        message = SentMessage(
            chat_id=chat_id,
            message_id=_next_message_id(chat_id),
            text=text,
            reply_markup=reply_markup,
            bot=self,
        )
        self.sent_messages.append(message)
        self.register_message(message)
        return message

    async def send_audio(self, chat_id, audio, **kwargs):
        return await (await self.send_message(chat_id, None)).reply_audio(audio=audio, **kwargs)

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None, parse_mode=None, **kwargs):
        self.edited_messages.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": parse_mode,
            }
        )
        target = self.message_by_id.get((chat_id, message_id))
        if target is not None:
            # Route through the message itself so Telegram-like rules
            # (e.g. 'message is not modified') are exercised by the tests.
            await target.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None, **kwargs):
        self.edited_messages.append(
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
        )
        return True

    async def get_me(self):
        return FakeUser(id=self.id, username=self.username, is_bot=True)

    async def get_chat(self, chat_id):
        if chat_id in self.chats:
            return self.chats[chat_id]
        from telegram.error import BadRequest

        raise BadRequest(f"chat not found: {chat_id}")

    async def get_chat_member(self, chat_id, user_id):
        if (chat_id, user_id) in self.chat_members:
            return self.chat_members[(chat_id, user_id)]
        from telegram.error import BadRequest

        raise BadRequest("user not found")

    async def get_chat_administrators(self, chat_id):
        from telegram.error import BadRequest

        raise BadRequest("not implemented")


class FakeCallbackQuery:
    def __init__(self, data: str, message: SentMessage, from_user: FakeUser, bot: FakeBot) -> None:
        self.data = data
        self.message = message
        self.from_user = from_user
        self.id = "cbq-1"
        self.bot = bot
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs):
        self.answers.append({"text": text, "show_alert": show_alert})
        return True


class FakeUpdate:
    def __init__(
        self,
        message: SentMessage | None = None,
        user: FakeUser | None = None,
        callback_query: FakeCallbackQuery | None = None,
        chat_id: int = 100,
    ) -> None:
        self.message = message
        self.callback_query = callback_query
        self.effective_user = user or (callback_query.from_user if callback_query else None)
        self.effective_message = message or (callback_query.message if callback_query else None)
        self.effective_chat = type("Chat", (), {"id": chat_id, "type": "private"})()
        self.update_id = 1


class FakeApplication:
    def __init__(self, bot: FakeBot) -> None:
        self.bot = bot
        self.bot_data: dict[str, Any] = {}


class FakeContext:
    def __init__(self, application: FakeApplication) -> None:
        self.application = application
        self.bot = application.bot
        self.bot_data = application.bot_data
        self.chat_data: dict[str, Any] = {}
        self.user_data: dict[str, Any] = {}


@dataclass
class FakeChat:
    id: int
    type: str = "channel"
    username: str | None = "mychannel"
    title: str | None = "My Channel"


@dataclass
class FakeMember:
    user: FakeUser
    status: str = "administrator"


@dataclass
class Downloaded:
    path: Path
    metadata: Any


def make_downloaded(path: Path, title: str = "Song", artist: str = "Artist", url: str = "https://youtu.be/x"):
    from music_bot.downloader import SearchResult  # noqa: F401  (import check only)

    metadata = type(
        "Metadata",
        (),
        {"title": title, "artist": artist, "source_url": url, "duration": 100},
    )()
    return Downloaded(path=path, metadata=metadata)


def make_context(bot: FakeBot | None = None, **bot_data) -> FakeContext:
    bot = bot or FakeBot()
    application = FakeApplication(bot)
    application.bot_data.update(bot_data)
    return FakeContext(application)


def markup_buttons(markup: InlineKeyboardMarkup | None) -> list[tuple[str, str]]:
    if not markup:
        return []
    return [
        (button.text, button.callback_data or "")
        for row in markup.inline_keyboard
        for button in row
    ]
