"""Issues 2 & 3: the admin panel — «Статистика» and «Добавить канал».

Everything runs against the real handlers with fake Telegram objects, so the
panel text/markup the bot would have sent is asserted directly.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from telegram.error import BadRequest, Forbidden

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fakes import (  # noqa: E402
    FakeBot,
    FakeCallbackQuery,
    FakeChat,
    FakeMember,
    FakeUpdate,
    FakeUser,
    SentMessage,
    make_context,
    reset_message_counter,
)
from music_bot.access import is_admin  # noqa: E402
from music_bot.admin import (  # noqa: E402
    _add_channel,
    _channel_candidates,
    _open_menu,
    admin_start,
    admin_text,
    dispatch_admin_callback,
)
from music_bot.config import Settings  # noqa: E402
from music_bot.database import Database  # noqa: E402

OWNER_ID = 555


@pytest.fixture
async def database(tmp_path):
    db = Database(None, tmp_path / "music.sqlite3")
    await db.connect()
    yield db
    await db.close()


@pytest.fixture
def settings():
    return Settings(
        bot_token="x",
        database_url=None,
        supabase_url=None,
        supabase_key=None,
        port=8000,
        cache_dir=pathlib.Path("/tmp/music-bot-test"),
        enable_ytdlp=False,
        enable_shazam=True,
        max_concurrent_downloads=2,
        download_retries=3,
        max_telegram_file_mb=50,
        admin_ids=frozenset({OWNER_ID}),
        admin_usernames=frozenset({"owner_name"}),
    )


class StrictPanel(SentMessage):
    """A panel message that behaves like Telegram: a no-op edit is rejected."""

    async def edit_text(self, text, reply_markup=None, parse_mode=None, **kwargs):
        if text == self.text and reply_markup == self.reply_markup:
            raise BadRequest("Message is not modified: specified new message content is the same")
        return await super().edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)


async def _panel(context, admin: FakeUser) -> StrictPanel:
    panel = StrictPanel(chat_id=OWNER_ID, text="/admin", bot=context.bot)
    update = FakeUpdate(message=panel, user=admin, chat_id=OWNER_ID)
    await _open_menu(update, context)
    sent = panel.replies[-1]
    sent.__class__ = StrictPanel  # the panel the bot actually sent
    return sent


# --------------------------------------------------------------------------
# Issue 2 - Статистика
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stats_shows_all_counters_and_edits_in_place(database, settings):
    reset_message_counter()
    for uid in (OWNER_ID, 1001, 1002):
        await database.register_user(user_id=uid, username=f"u{uid}", first_name=f"U{uid}")
    await database.set_banned(1002, True, "spam")
    await database.mark_user_blocked(1001)
    await database.save_song(title="S", artist="A", file_id="f1", source_url=None)

    bot = FakeBot()
    context = make_context(bot, database=database, settings=settings, enable_shazam=True)
    admin = FakeUser(id=OWNER_ID, username="owner")
    panel = await _panel(context, admin)
    messages_before = len(bot.sent_messages)

    query = FakeCallbackQuery("adm:stats", panel, admin, bot)
    await dispatch_admin_callback(FakeUpdate(callback_query=query, user=admin, chat_id=OWNER_ID), context)

    print("\n--- STATS PANEL ---\n" + panel.text)
    assert "📊 Статистика бота" in panel.text
    assert "Всего начали бот: <b>3</b>" in panel.text
    assert "Активны за 7 дней: <b>3</b>" in panel.text
    assert "Заблокировали бота: <b>1</b>" in panel.text
    assert "Заблокированы вами: <b>1</b>" in panel.text
    assert "Треков в каталоге: <b>1</b>" in panel.text
    assert "Администраторов: <b>1</b>" in panel.text
    # Edited in place, nothing new was posted.
    assert len(bot.sent_messages) == messages_before, "stats must edit the panel message"
    assert panel.edits, "the panel message must have been edited"
    assert query.answers, "the callback must be answered"


@pytest.mark.asyncio
async def test_stats_survives_repeated_taps(database, settings):
    """Pressing the button again must not raise 'Message is not modified'."""
    reset_message_counter()
    await database.register_user(user_id=OWNER_ID, username="owner", first_name="Owner")
    bot = FakeBot()
    context = make_context(bot, database=database, settings=settings)
    admin = FakeUser(id=OWNER_ID, username="owner")
    panel = await _panel(context, admin)

    for _ in range(3):
        query = FakeCallbackQuery("adm:stats", panel, admin, bot)
        await dispatch_admin_callback(
            FakeUpdate(callback_query=query, user=admin, chat_id=OWNER_ID), context
        )
    assert "Статистика" in panel.text


@pytest.mark.asyncio
async def test_stats_falls_back_when_the_aggregate_query_fails(database, settings):
    reset_message_counter()
    await database.register_user(user_id=OWNER_ID, username="owner", first_name="Owner")
    await database.register_user(user_id=9001, username="other", first_name="Other")

    async def boom(*args, **kwargs):
        raise RuntimeError("column blocked_at does not exist")

    database.user_stats = boom  # type: ignore[assignment]

    bot = FakeBot()
    context = make_context(bot, database=database, settings=settings)
    admin = FakeUser(id=OWNER_ID, username="owner")
    panel = await _panel(context, admin)

    query = FakeCallbackQuery("adm:stats", panel, admin, bot)
    await dispatch_admin_callback(FakeUpdate(callback_query=query, user=admin, chat_id=OWNER_ID), context)

    print("\n--- FALLBACK STATS ---\n" + panel.text)
    assert "Всего начали бот: <b>2</b>" in panel.text
    assert "Не удалось получить статистику" not in panel.text


@pytest.mark.asyncio
async def test_stats_reports_storage_problems_instead_of_crashing(database, settings):
    reset_message_counter()

    async def boom(*args, **kwargs):
        raise RuntimeError("storage down")

    database.user_stats = boom  # type: ignore[assignment]
    database.list_users = boom  # type: ignore[assignment]

    bot = FakeBot()
    context = make_context(bot, database=database, settings=settings)
    admin = FakeUser(id=OWNER_ID, username="owner")
    panel = await _panel(context, admin)

    query = FakeCallbackQuery("adm:stats", panel, admin, bot)
    await dispatch_admin_callback(FakeUpdate(callback_query=query, user=admin, chat_id=OWNER_ID), context)
    assert "Не удалось получить статистику" in panel.text
    assert query.answers


@pytest.mark.asyncio
async def test_panel_navigation_keeps_editing_one_message(database, settings):
    reset_message_counter()
    bot = FakeBot()
    context = make_context(bot, database=database, settings=settings)
    admin = FakeUser(id=OWNER_ID, username="owner")
    panel = await _panel(context, admin)
    posted = len(bot.sent_messages)

    for data in ("adm:stats", "adm:menu", "adm:channels", "adm:menu", "adm:ban_menu", "adm:menu"):
        query = FakeCallbackQuery(data, panel, admin, bot)
        await dispatch_admin_callback(
            FakeUpdate(callback_query=query, user=admin, chat_id=OWNER_ID), context
        )

    assert len(bot.sent_messages) == posted, "every tap must reuse the panel message"
    assert "Панель администратора" in panel.text


# --------------------------------------------------------------------------
# Issue 3 - Добавить канал
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("@MyChannel", ["MyChannel"]),
        ("MyChannel", ["MyChannel"]),
        ("https://t.me/MyChannel", ["MyChannel"]),
        ("t.me/MyChannel", ["MyChannel"]),
        ("telegram.me/MyChannel", ["MyChannel"]),
        ("https://t.me/MyChannel/123", ["MyChannel"]),
        ("https://t.me/c/1234567890/1", ["-1001234567890"]),
        ("-1001234567890", ["-1001234567890"]),
        ("https://t.me/+AbCdEfGh", ["https://t.me/+AbCdEfGh"]),
        ("+AbCdEfGh", ["https://t.me/+AbCdEfGh"]),
        ("https://t.me/joinchat/AAAAA", ["https://t.me/joinchat/AAAAA"]),
        ("  @MyChannel  ", ["MyChannel"]),
    ],
)
def test_channel_candidates(value, expected):
    assert _channel_candidates(value) == expected


def test_channel_candidates_rejects_garbage():
    assert _channel_candidates("") == []
    assert _channel_candidates("   ") == []


@pytest.mark.asyncio
async def test_channel_is_accepted_when_bot_is_admin_via_admin_list(database, settings):
    reset_message_counter()
    bot = FakeBot(id=4242, username="music_bot")
    chat = FakeChat(id=-100123, type="channel", username="mychannel", title="My Channel")
    bot.chats["mychannel"] = chat
    other = FakeUser(id=1, username="someone")

    async def administrators(chat_id):
        return [FakeMember(user=other, status="creator"), FakeMember(user=FakeUser(id=4242, username="music_bot"), status="administrator")]

    bot.get_chat_administrators = administrators  # type: ignore[assignment]
    context = make_context(bot, database=database, settings=settings)
    message = SentMessage(text="@mychannel", bot=bot)
    update = FakeUpdate(message=message, user=FakeUser(id=OWNER_ID), chat_id=OWNER_ID)

    await _add_channel(update, context, "@mychannel")

    channels = await database.list_required_channels()
    assert len(channels) == 1, "the channel was not saved"
    assert channels[0].chat_id == -100123
    assert "Канал добавлен" in message.replies[0].text, message.replies[0].text
    assert "не является администратором" not in message.replies[0].text


@pytest.mark.asyncio
async def test_channel_is_accepted_when_only_get_chat_member_works(database, settings):
    reset_message_counter()
    bot = FakeBot(id=4242, username="music_bot")
    chat = FakeChat(id=-100456, type="channel", username="second", title="Second")
    bot.chats["https://t.me/second"] = chat
    bot.chats["second"] = chat
    bot.chat_members[(-100456, 4242)] = FakeMember(user=FakeUser(id=4242), status="administrator")

    async def administrators(chat_id):
        raise Forbidden("bot is not a member")

    bot.get_chat_administrators = administrators  # type: ignore[assignment]
    context = make_context(bot, database=database, settings=settings)
    message = SentMessage(text="https://t.me/second", bot=bot)
    update = FakeUpdate(message=message, user=FakeUser(id=OWNER_ID), chat_id=OWNER_ID)

    await _add_channel(update, context, "https://t.me/second")

    channels = await database.list_required_channels()
    assert [c.chat_id for c in channels] == [-100456], "channel not accepted via getChatMember"


@pytest.mark.asyncio
async def test_channel_is_accepted_when_admin_rights_cannot_be_verified(database, settings):
    """A Telegram error must not become 'make sure the bot is an admin'."""
    reset_message_counter()
    bot = FakeBot(id=4242, username="music_bot")
    chat = FakeChat(id=-100789, type="channel", username="third", title="Third")
    bot.chats["third"] = chat

    async def administrators(chat_id):
        raise BadRequest("have no rights")

    async def member(chat_id, user_id):
        raise Forbidden("bot is not a member")

    bot.get_chat_administrators = administrators  # type: ignore[assignment]
    bot.get_chat_member = member  # type: ignore[assignment]
    context = make_context(bot, database=database, settings=settings)
    message = SentMessage(text="@third", bot=bot)
    update = FakeUpdate(message=message, user=FakeUser(id=OWNER_ID), chat_id=OWNER_ID)

    await _add_channel(update, context, "@third")

    channels = await database.list_required_channels()
    assert [c.chat_id for c in channels] == [-100789], "channel must still be saved"
    assert "проверить не удалось" in message.replies[0].text, message.replies[0].text


@pytest.mark.asyncio
async def test_private_channel_link_is_resolved(database, settings):
    reset_message_counter()
    bot = FakeBot(id=4242, username="music_bot")
    chat = FakeChat(id=-100555, type="channel", username=None, title="Private")
    bot.chats[-100555] = chat
    bot.chat_members[(-100555, 4242)] = FakeMember(user=FakeUser(id=4242), status="creator")
    context = make_context(bot, database=database, settings=settings)
    message = SentMessage(text="https://t.me/c/555/1", bot=bot)
    update = FakeUpdate(message=message, user=FakeUser(id=OWNER_ID), chat_id=OWNER_ID)

    await _add_channel(update, context, "https://t.me/c/555/1")

    channels = await database.list_required_channels()
    assert [c.chat_id for c in channels] == [-100555]


@pytest.mark.asyncio
async def test_unknown_channel_explains_what_to_send(database, settings):
    reset_message_counter()
    bot = FakeBot(id=4242, username="music_bot")
    context = make_context(bot, database=database, settings=settings)
    message = SentMessage(text="@nosuchchannel", bot=bot)
    update = FakeUpdate(message=message, user=FakeUser(id=OWNER_ID), chat_id=OWNER_ID)

    await _add_channel(update, context, "@nosuchchannel")

    assert not await database.list_required_channels()
    assert "Не удалось открыть канал" in message.replies[0].text


@pytest.mark.asyncio
async def test_add_channel_flow_from_the_panel_redraws_in_place(database, settings):
    reset_message_counter()
    bot = FakeBot(id=4242, username="music_bot")
    chat = FakeChat(id=-100321, type="channel", username="flow", title="Flow")
    bot.chats["flow"] = chat
    bot.chat_members[(-100321, 4242)] = FakeMember(user=FakeUser(id=4242), status="administrator")
    context = make_context(bot, database=database, settings=settings)
    admin = FakeUser(id=OWNER_ID, username="owner")
    panel = await _panel(context, admin)
    posted = len(bot.sent_messages)

    # 1) open the channel list
    await dispatch_admin_callback(
        FakeUpdate(callback_query=FakeCallbackQuery("adm:channels", panel, admin, bot), user=admin),
        context,
    )
    assert "Обязательные каналы" in panel.text
    # 2) press «Добавить канал»
    await dispatch_admin_callback(
        FakeUpdate(callback_query=FakeCallbackQuery("adm:channel_add", panel, admin, bot), user=admin),
        context,
    )
    assert context.user_data["admin_wait"] == "channel_add"
    assert "Пришлите канал" in panel.text
    # 3) send the channel as a text message
    typed = SentMessage(chat_id=OWNER_ID, text="@flow", bot=bot)
    consumed = await admin_text(FakeUpdate(message=typed, user=admin, chat_id=OWNER_ID), context)
    assert consumed is True

    channels = await database.list_required_channels()
    assert [c.chat_id for c in channels] == [-100321]
    # the panel went back to the channel list, edited in place
    assert "Обязательные каналы" in panel.text
    assert "Flow" in panel.text
    assert len(bot.sent_messages) == posted + 1, "only the confirmation is a new message"


@pytest.mark.asyncio
async def test_membership_gate_uses_the_added_channel(database, settings):
    reset_message_counter()
    bot = FakeBot(id=4242, username="music_bot")
    await database.add_required_channel(chat_id=-100321, username="flow", title="Flow")
    bot.chat_members[(-100321, 999)] = FakeMember(user=FakeUser(id=999), status="left")
    context = make_context(bot, database=database, settings=settings)
    assert not await is_admin(999, context, username="stranger")
