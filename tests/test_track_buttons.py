"""Issue 1: every audio the bot sends carries working ❤️ / ❌ inline buttons.

The buttons must be bound in the *sending* call itself (not attached in a later
edit that can be skipped), their callback_data must stay within Telegram's
64-byte limit, and pressing them must edit the existing message instead of
posting a new one.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fakes import (  # noqa: E402
    FakeBot,
    FakeCallbackQuery,
    FakeUpdate,
    FakeUser,
    SentMessage,
    make_context,
    make_downloaded,
    reset_message_counter,
)
from music_bot.database import Database  # noqa: E402
from music_bot.handlers.callbacks import callback_query  # noqa: E402
from music_bot.services.music import MusicService  # noqa: E402
from music_bot.track_buttons import MAX_CALLBACK_DATA_BYTES, track_markup  # noqa: E402

USER = FakeUser(id=777, username="listener")


class FakeProvider:
    def __init__(self, tmp_path: pathlib.Path, fail_save: bool = False) -> None:
        self.tmp_path = tmp_path

    async def search(self, query: str, limit: int = 5):
        return []

    async def download(self, query: str):
        path = self.tmp_path / "track.mp3"
        path.write_bytes(b"ID3fake-audio-bytes")
        return make_downloaded(path)


class BrokenCatalogue(Database):
    """Catalogue writes fail - the buttons must survive anyway."""

    async def save_song(self, *args, **kwargs):  # type: ignore[override]
        raise RuntimeError("catalogue unavailable")


@pytest.fixture
async def database(tmp_path):
    db = Database(None, tmp_path / "music.sqlite3")
    await db.connect()
    yield db
    await db.close()


@pytest.fixture
async def broken_database(tmp_path):
    db = BrokenCatalogue(None, tmp_path / "broken.sqlite3")
    await db.connect()
    yield db
    await db.close()


def _service(db, provider=None) -> MusicService:
    return MusicService(
        database=db, provider=provider, max_file_mb=50, max_concurrent_downloads=2
    )


def _assert_buttons(message: SentMessage, when: str) -> None:
    """A track row always has a heart (❤️ or 💔 when already liked) plus ⏪ (back)."""
    labels = message.initial_button_labels
    assert "❤️" in labels or "💔" in labels, f"no heart button {when}: {labels}"
    # Task #2 replaces ❌ with ⏪ - accept either for backward compatibility
    assert "❌" in labels or "⏪" in labels, f"no back button (⏪/❌) {when}: {labels}"
    for data in message.initial_callback_data:
        assert len(data.encode()) <= MAX_CALLBACK_DATA_BYTES, f"{data!r} exceeds 64 bytes"


@pytest.mark.asyncio
async def test_downloaded_audio_is_sent_with_heart_and_cross(database, tmp_path):
    reset_message_counter()
    service = _service(database, FakeProvider(tmp_path))
    context = make_context(music_service=service, database=database)
    message = SentMessage(text="some song", bot=context.bot)
    update = FakeUpdate(message=message, user=USER)

    song = await service.send_query(update, context, "some song")

    assert song is not None, "the track was not downloaded/saved"
    audios = [m for m in message.replies if m.audio is not None]
    assert len(audios) == 1, "exactly one audio message expected"
    # The keyboard must already be part of the sendAudio call.
    _assert_buttons(audios[0], "at send time")
    assert audios[0].callback_data[0].startswith("favorite:add:"), audios[0].callback_data
    assert audios[0].callback_data[1] == "track:close"


@pytest.mark.asyncio
async def test_buttons_survive_a_broken_catalogue(broken_database, tmp_path):
    reset_message_counter()
    service = _service(broken_database, FakeProvider(tmp_path))
    context = make_context(music_service=service, database=broken_database)
    message = SentMessage(text="some song", bot=context.bot)
    update = FakeUpdate(message=message, user=USER)

    await service.send_query(update, context, "some song")

    audios = [m for m in message.replies if m.audio is not None]
    assert len(audios) == 1
    _assert_buttons(audios[0], "at send time (catalogue down)")
    # Token based callback keeps working even though save_song raised.
    assert audios[0].callback_data[0].startswith("favnew:a:"), audios[0].callback_data


@pytest.mark.asyncio
async def test_heart_on_uncatalogued_track_still_saves_favorite(broken_database, tmp_path):
    reset_message_counter()
    service = _service(broken_database, FakeProvider(tmp_path))
    context = make_context(music_service=service, database=broken_database)
    message = SentMessage(text="some song", bot=context.bot)
    await service.send_query(FakeUpdate(message=message, user=USER), context, "some song")
    audio = [m for m in message.replies if m.audio is not None][-1]

    query = FakeCallbackQuery(audio.callback_data[0], audio, USER, context.bot)
    await callback_query(FakeUpdate(callback_query=query, user=USER), context)

    favorites = await broken_database.list_favorites(USER.id)
    assert len(favorites) == 1, favorites
    assert favorites[0].title == "Song"
    assert query.answers and "избранное" in query.answers[0]["text"].lower()
    assert "💔" in audio.button_labels, audio.button_labels
    # Edited in place - no extra message was posted.
    assert not audio.replies, "⏪/❌/❤️ must not post a new message"


@pytest.mark.asyncio
async def test_cached_and_top_chart_audio_carry_buttons(database):
    reset_message_counter()
    song = await database.save_song(
        title="Cached Song", artist="Cached Artist", file_id="file-cached", source_url=None
    )
    service = _service(database)
    context = make_context(music_service=service, database=database)

    message = SentMessage(text="Cached Song", bot=context.bot)
    await service.send_query(FakeUpdate(message=message, user=USER), context, "Cached Song")
    _assert_buttons([m for m in message.replies if m.audio][-1], "cached track")

    message2 = SentMessage(text="top", bot=context.bot)
    await service.send_song(FakeUpdate(message=message2, user=USER), song)
    _assert_buttons([m for m in message2.replies if m.audio][-1], "send_song")


@pytest.mark.asyncio
async def test_favorite_play_sends_audio_with_buttons(database):
    reset_message_counter()
    song = await database.save_song(
        title="Fav", artist="Artist", file_id="fav-file", source_url=None
    )
    await database.add_favorite(USER.id, song)
    favorites = await database.list_favorites(USER.id)
    service = _service(database)
    context = make_context(music_service=service, database=database)
    panel = SentMessage(text="❤️ Избранные треки:", bot=context.bot)

    query = FakeCallbackQuery(f"favorite:play:{favorites[0].id}", panel, USER, context.bot)
    await callback_query(FakeUpdate(callback_query=query, user=USER), context)

    audios = [m for m in context.bot.sent_messages if m.audio is not None]
    assert audios, "no audio was sent from the favorites list"
    _assert_buttons(audios[-1], "favorite:play")


@pytest.mark.asyncio
async def test_cross_button_clears_keyboard_without_new_message(database):
    reset_message_counter()
    service = _service(database)
    context = make_context(music_service=service, database=database)
    song = await database.save_song(title="T", artist="A", file_id="f1", source_url=None)
    audio = SentMessage(audio=True, reply_markup=track_markup(song=song), bot=context.bot)
    before = len(context.bot.sent_messages)

    query = FakeCallbackQuery("track:close", audio, USER, context.bot)
    await callback_query(FakeUpdate(callback_query=query, user=USER), context)

    assert audio.reply_markup is None, "the ⏪/❌ button must remove the row"
    assert len(context.bot.sent_messages) == before, "⏪/❌ must not send a new message"
    assert context.user_data["mode"] == "song"
    assert query.answers, "the user must get feedback"


@pytest.mark.asyncio
async def test_legacy_search_back_callback_still_handled(database):
    reset_message_counter()
    service = _service(database)
    context = make_context(music_service=service, database=database)
    song = await database.save_song(title="T", artist="A", file_id="f2", source_url=None)
    audio = SentMessage(audio=True, reply_markup=track_markup(song=song), bot=context.bot)

    query = FakeCallbackQuery("search:back", audio, USER, context.bot)
    await callback_query(FakeUpdate(callback_query=query, user=USER), context)

    assert audio.reply_markup is None
    assert context.user_data["mode"] == "song"


@pytest.mark.asyncio
async def test_heart_button_toggles_favorite_in_place(database):
    reset_message_counter()
    service = _service(database)
    context = make_context(music_service=service, database=database)
    song = await database.save_song(title="T", artist="A", file_id="f3", source_url=None)
    audio = SentMessage(audio=True, reply_markup=track_markup(song=song), bot=context.bot)

    add = FakeCallbackQuery(f"favorite:add:{song.id}", audio, USER, context.bot)
    await callback_query(FakeUpdate(callback_query=add, user=USER), context)
    assert await database.is_favorite(USER.id, song.file_id)
    assert "💔" in audio.button_labels

    remove = FakeCallbackQuery(f"favorite:remove:{song.id}", audio, USER, context.bot)
    await callback_query(FakeUpdate(callback_query=remove, user=USER), context)
    assert not await database.is_favorite(USER.id, song.file_id)
    assert "❤️" in audio.button_labels
    assert not audio.replies, "toggling must edit the message, not post a new one"


def test_callback_data_never_exceeds_telegram_limit():
    markup = track_markup(song=type("S", (), {"id": 9_999_999_999_999})())
    for row in markup.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= MAX_CALLBACK_DATA_BYTES
