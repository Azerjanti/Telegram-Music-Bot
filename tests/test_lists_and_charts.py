"""Issues 5-7: «По исполнителю», «Топ 100» (paginated) and «Локальный топ»."""

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
from music_bot.handlers.search import send_local_top, send_spotify_top  # noqa: E402
from music_bot.handlers.start import menu_action  # noqa: E402
from music_bot.services.music import MusicService  # noqa: E402
from music_bot.top_charts import SpotifyProvider  # noqa: E402
from music_bot.top_charts.spotify import SpotifyTrack  # noqa: E402
from music_bot.config import Settings  # noqa: E402


def _settings():
    return Settings(
        bot_token="t", database_url=None, supabase_url=None, supabase_key=None,
        port=8000, cache_dir=pathlib.Path("/tmp/music-bot-test"), enable_ytdlp=False,
        enable_shazam=True, max_concurrent_downloads=2, download_retries=3,
        max_telegram_file_mb=50, admin_ids=frozenset({555}),
    )

USER = FakeUser(id=777, username="listener")


class FakeProvider:
    """Returns 30 results for any query, like a real audio source would."""

    def __init__(self, tmp_path: pathlib.Path, results: int = 30) -> None:
        self.tmp_path = tmp_path
        self.results = results
        self.searched: list[str] = []

    async def search(self, query: str, limit: int = 5):
        self.searched.append(query)
        from music_bot.downloader import SearchResult

        return [
            SearchResult(title=f"Track {i}", artist=query, url=f"https://youtu.be/{i}")
            for i in range(1, min(limit, self.results) + 1)
        ]

    async def download(self, query: str):
        path = self.tmp_path / "track.mp3"
        path.write_bytes(b"ID3fake-audio-bytes")
        return make_downloaded(path, title="Downloaded Track", artist="Downloaded Artist")


@pytest.fixture
async def database(tmp_path):
    db = Database(None, tmp_path / "music.sqlite3")
    await db.connect()
    yield db
    await db.close()


# --------------------------------------------------------------------------
# 5) «По исполнителю»
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_artist_search_returns_playable_results(database, tmp_path):
    reset_message_counter()
    provider = FakeProvider(tmp_path)
    service = MusicService(database=database, provider=provider, max_file_mb=50, max_concurrent_downloads=2)
    context = make_context(music_service=service, database=database, settings=_settings())

    start_message = SentMessage(text="👤 По исполнителю", bot=context.bot)
    await menu_action(FakeUpdate(message=start_message, user=USER), context)
    assert context.user_data["mode"] == "artist"

    typed = SentMessage(text="Sabrina Carpenter", bot=context.bot)
    from music_bot.handlers.search import text_search

    await text_search(FakeUpdate(message=typed, user=USER), context)

    print("\n--- ARTIST REPLIES ---")
    for reply in typed.replies:
        print(repr(reply.text), reply.button_labels[:3])
    # The user must get a list they can actually play.
    listed = [r for r in typed.replies if r.callback_data]
    assert listed, "artist search returned no playable list"
    assert any(cb.startswith(("result:", "cached:", "song:", "top:")) for cb in listed[-1].callback_data)


@pytest.mark.asyncio
async def test_artist_search_uses_catalogue_songs(database, tmp_path):
    reset_message_counter()
    await database.save_song(title="Song A", artist="The Artist", file_id="a", source_url=None)
    await database.save_song(title="Song B", artist="The Artist", file_id="b", source_url=None)
    service = MusicService(database=database, provider=None, max_file_mb=50, max_concurrent_downloads=2)
    context = make_context(music_service=service, database=database, settings=_settings())
    context.user_data["mode"] = "artist"

    typed = SentMessage(text="The Artist", bot=context.bot)
    from music_bot.handlers.search import text_search

    await text_search(FakeUpdate(message=typed, user=USER), context)

    all_callbacks = [cb for r in typed.replies for cb in r.callback_data]
    assert any(cb.startswith("cached:") for cb in all_callbacks), all_callbacks


# --------------------------------------------------------------------------
# 6) «Топ 100»
# --------------------------------------------------------------------------

class FakeSpotify(SpotifyProvider):
    def __init__(self, count: int = 100) -> None:
        super().__init__("id", "secret")
        self.count = count

    async def top_tracks(self, limit: int = 50):
        return [SpotifyTrack(f"Song {i}", f"Artist {i}") for i in range(1, min(limit, self.count) + 1)]


@pytest.mark.asyncio
async def test_top_100_is_paginated_with_arrows(database):
    reset_message_counter()
    service = MusicService(database=database, provider=None, max_file_mb=50, max_concurrent_downloads=2)
    context = make_context(
        music_service=service, database=database, spotify_provider=FakeSpotify(100)
    )
    message = SentMessage(text="🏆 Топ 100", bot=context.bot)

    await send_spotify_top(FakeUpdate(message=message, user=USER), context, limit=100)

    listing = message.replies[-1]
    print("\n--- TOP LISTING ---", len(listing.button_labels), "buttons")
    print(listing.button_labels)
    assert "1. Song 1" in listing.button_labels[0]
    # one page only - not all 100 in a single message
    assert len(listing.button_labels) < 100, "the list must be paginated"
    assert any(label in ("⏩", "▶️", "⏭") for label in listing.button_labels), listing.button_labels


@pytest.mark.asyncio
async def test_top_pagination_walks_all_100_tracks(database):
    reset_message_counter()
    service = MusicService(database=database, provider=None, max_file_mb=50, max_concurrent_downloads=2)
    context = make_context(
        music_service=service, database=database, spotify_provider=FakeSpotify(100)
    )
    message = SentMessage(text="🏆 Топ 100", bot=context.bot)
    await send_spotify_top(FakeUpdate(message=message, user=USER), context, limit=100)
    listing = message.replies[-1]

    seen_titles: set[str] = set()
    pages = 0
    while pages < 30:
        pages += 1
        for label in listing.button_labels:
            if label and label[0].isdigit():
                seen_titles.add(label)
        forward = [cb for cb in listing.callback_data if cb in {"tnext", "next", "snext"}]
        if not forward:
            break
        query = FakeCallbackQuery(forward[0], listing, USER, context.bot)
        await callback_query(FakeUpdate(callback_query=query, user=USER), context)

    print(f"\n--- walked {pages} pages, saw {len(seen_titles)} tracks ---")
    assert len(seen_titles) >= 100, f"only {len(seen_titles)} of 100 tracks reachable"
    assert pages <= 15, f"too many pages: {pages}"


@pytest.mark.asyncio
async def test_top_fallback_has_100_tracks(database):
    reset_message_counter()
    service = MusicService(database=database, provider=None, max_file_mb=50, max_concurrent_downloads=2)
    context = make_context(music_service=service, database=database, settings=_settings())  # no spotify provider
    message = SentMessage(text="🏆 Топ 100", bot=context.bot)

    await send_spotify_top(FakeUpdate(message=message, user=USER), context, limit=100)

    listing = message.replies[-1]
    assert listing.button_labels, "no fallback chart was shown"
    # walk the pages of the fallback list
    seen = set()
    for _ in range(30):
        for label in listing.button_labels:
            if label and label[0].isdigit():
                seen.add(label)
        forward = [cb for cb in listing.callback_data if cb in {"tnext", "next", "snext"}]
        if not forward:
            break
        await callback_query(
            FakeUpdate(
                callback_query=FakeCallbackQuery(forward[0], listing, USER, context.bot), user=USER
            ),
            context,
        )
    assert len(seen) >= 100, f"the offline chart only has {len(seen)} tracks"


# --------------------------------------------------------------------------
# 7) «Локальный топ»
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_searched_songs_land_in_the_local_top(database, tmp_path):
    reset_message_counter()
    provider = FakeProvider(tmp_path)
    service = MusicService(database=database, provider=provider, max_file_mb=50, max_concurrent_downloads=2)
    context = make_context(music_service=service, database=database, settings=_settings())

    # the user searches and receives a track
    typed = SentMessage(text="brand new song", bot=context.bot)
    song = await service.send_query(FakeUpdate(message=typed, user=USER), context, "brand new song")
    assert song is not None, "the download did not reach the catalogue"

    # ... and it must show up in the local top
    top_message = SentMessage(text="📈 Локальный топ", bot=context.bot)
    await send_local_top(FakeUpdate(message=top_message, user=USER), context)

    listing = top_message.replies[-1]
    print("\n--- LOCAL TOP ---", listing.text, listing.button_labels)
    assert listing.callback_data, "the local top has no buttons"
    assert "Downloaded Track" in " ".join(listing.button_labels), listing.button_labels
    assert any(cb.startswith("song:") for cb in listing.callback_data)


@pytest.mark.asyncio
async def test_local_top_track_button_plays_it(database, tmp_path):
    reset_message_counter()
    song = await database.save_song(
        title="Local Hit", artist="Local Artist", file_id="local-1", source_url=None
    )
    service = MusicService(database=database, provider=None, max_file_mb=50, max_concurrent_downloads=2)
    context = make_context(music_service=service, database=database, settings=_settings())
    panel = SentMessage(text="Локальный топ:", bot=context.bot)

    await callback_query(
        FakeUpdate(
            callback_query=FakeCallbackQuery(f"song:{song.id}", panel, USER, context.bot), user=USER
        ),
        context,
    )

    audios = [m for m in context.bot.sent_messages if m.audio is not None]
    assert audios, "pressing a local-top track sent no audio"
    labels = audios[-1].initial_button_labels
    assert "❤️" in labels or "💔" in labels, labels


@pytest.mark.asyncio
async def test_local_top_is_paginated(database):
    reset_message_counter()
    for i in range(40):
        await database.save_song(title=f"Track {i}", artist=f"Artist {i}", file_id=f"f{i}", source_url=None)
        await database.increment_play_count(i + 1)
    service = MusicService(database=database, provider=None, max_file_mb=50, max_concurrent_downloads=2)
    context = make_context(music_service=service, database=database, settings=_settings())
    message = SentMessage(text="📈 Локальный топ", bot=context.bot)

    await send_local_top(FakeUpdate(message=message, user=USER), context, limit=100)

    listing = message.replies[-1]
    print("\n--- LOCAL TOP PAGE 1 ---", len(listing.button_labels), "buttons")
    assert len(listing.button_labels) < 45, "the local top must be paginated"
    assert any(label in ("⏩", "▶️", "⏭") for label in listing.button_labels), listing.button_labels
