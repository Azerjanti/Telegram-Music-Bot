"""Issue 4: the owner's numeric Telegram ID must be recognised.

Covers the .env file, messy values (quotes, spaces, comments, several ids),
the ADMIN_USERNAME fallback, the built-in default, and /admin telling a
stranger their own ID so they can add it.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fakes import (  # noqa: E402
    FakeBot,
    FakeUpdate,
    FakeUser,
    SentMessage,
    make_context,
    reset_message_counter,
)
from music_bot.access import is_admin, is_owner  # noqa: E402
from music_bot.admin import admin_start, show_my_id  # noqa: E402
from music_bot.config import DEFAULT_ADMIN_ID, Settings, _as_int_list, _as_username_list  # noqa: E402
from music_bot.database import Database  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "BOT_TOKEN",
        "ADMIN_ID",
        "ADMIN_IDS",
        "ADMIN_USERNAME",
        "ADMIN_USERNAMES",
        "AUDIO_CACHE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUDIO_CACHE_DIR", "/tmp/music-bot-config-test")
    yield


def _settings(**env) -> Settings:
    import os

    for key, value in env.items():
        os.environ[key] = value
    return Settings.from_env()


def test_plain_admin_id_is_recognised():
    settings = _settings(BOT_TOKEN="t", ADMIN_ID="123456789")
    assert settings.admin_ids == frozenset({123456789})


@pytest.mark.parametrize(
    "value, expected",
    [
        ("123456789", {123456789}),
        ("  123456789  ", {123456789}),
        ('"123456789"', {123456789}),
        ("'123456789'", {123456789}),
        ("`123456789`", {123456789}),
        ("123456789 # my id", {123456789}),
        ("123456789,", {123456789}),
        ("123456789,987654321", {123456789, 987654321}),
        ("123456789 987654321", {123456789, 987654321}),
        ("123456789;987654321", {123456789, 987654321}),
        ("123456789\n987654321", {123456789, 987654321}),
        ('"123456789", "987654321"', {123456789, 987654321}),
        ("@owner_name", set()),
        ("", set()),
    ],
)
def test_messy_admin_id_values_are_parsed(value, expected, monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", value)
    assert _as_int_list("ADMIN_IDS") == expected


def test_admin_ids_and_admin_id_are_merged():
    settings = _settings(BOT_TOKEN="t", ADMIN_ID="111", ADMIN_IDS="222,333")
    assert settings.admin_ids == frozenset({111, 222, 333})


def test_builtin_default_is_used_when_nothing_is_configured():
    settings = _settings(BOT_TOKEN="t")
    assert DEFAULT_ADMIN_ID in settings.admin_ids


def test_dotenv_file_is_loaded(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'BOT_TOKEN="123456:ABC"\n'
        "# owner\n"
        "ADMIN_ID=8377297659\n"
        'ADMIN_USERNAME = "OwnerName"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = Settings.from_env()
    assert settings.bot_token == "123456:ABC"
    assert settings.admin_ids == frozenset({8377297659})
    assert settings.admin_usernames == frozenset({"ownername"})


def test_real_environment_wins_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ADMIN_ID=111\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "token-from-env")
    monkeypatch.setenv("ADMIN_ID", "999")
    settings = Settings.from_env()
    assert settings.bot_token == "token-from-env"
    assert settings.admin_ids == frozenset({999})


def test_admin_username_config(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAMES", "@Owner_Name, second")
    assert _as_username_list("ADMIN_USERNAMES") == {"owner_name", "second"}


@pytest.fixture
async def database(tmp_path):
    db = Database(None, tmp_path / "music.sqlite3")
    await db.connect()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_configured_admin_id_opens_the_panel(database):
    reset_message_counter()
    settings = _settings(BOT_TOKEN="t", ADMIN_ID="555")
    context = make_context(FakeBot(), database=database, settings=settings)
    owner = FakeUser(id=555, username="owner")
    message = SentMessage(text="/admin", bot=context.bot)

    await admin_start(FakeUpdate(message=message, user=owner), context)

    panel = message.replies[-1]
    assert "Панель администратора" in panel.text, panel.text
    assert "adm:stats" in panel.callback_data


@pytest.mark.asyncio
async def test_stranger_is_told_their_numeric_id(database):
    reset_message_counter()
    settings = _settings(BOT_TOKEN="t", ADMIN_ID="555")
    context = make_context(FakeBot(), database=database, settings=settings)
    stranger = FakeUser(id=424242, username="stranger")
    message = SentMessage(text="/admin", bot=context.bot)

    await admin_start(FakeUpdate(message=message, user=stranger), context)

    reply = message.replies[-1]
    assert "нет доступа" in reply.text
    assert "424242" in reply.text, "the ID must be shown so it can be configured"


@pytest.mark.asyncio
async def test_id_command_prints_numeric_id(database):
    reset_message_counter()
    settings = _settings(BOT_TOKEN="t", ADMIN_ID="555")
    context = make_context(FakeBot(), database=database, settings=settings)
    user = FakeUser(id=31337, username="someone")
    message = SentMessage(text="/id", bot=context.bot)

    await show_my_id(FakeUpdate(message=message, user=user), context)

    assert "31337" in message.replies[-1].text


@pytest.mark.asyncio
async def test_admin_by_username_when_id_is_unknown(database):
    reset_message_counter()
    settings = _settings(BOT_TOKEN="t", ADMIN_USERNAME="Owner_Name")
    context = make_context(FakeBot(), database=database, settings=settings)
    assert await is_admin(1, context, username="owner_name")
    assert not await is_admin(2, context, username="other")
    assert is_owner(1, context) is False  # username grants panel access, not ownership


@pytest.mark.asyncio
async def test_admin_flag_from_the_database_still_works(database):
    reset_message_counter()
    settings = _settings(BOT_TOKEN="t", ADMIN_ID="555")
    context = make_context(FakeBot(), database=database, settings=settings)
    await database.set_db_admin(777, True)
    assert await is_admin(777, context)
    assert not await is_admin(888, context)
