"""Integration test: the admin «Статистика» queries against a real PostgreSQL.

Uses an embedded PostgreSQL server (pgserver) so the asyncpg branch of
music_bot.database.db.Database is really executed, not mocked.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

import pytest

ROOT = Path = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_bot.database import Database  # noqa: E402


@pytest.fixture(scope="module")
def postgres_url():
    pgserver = pytest.importorskip("pgserver")
    data_dir = pathlib.Path(tempfile.mkdtemp(prefix="pgtest")) / "pg"
    server = pgserver.get_server(str(data_dir), cleanup_mode=None)
    yield server.get_uri()
    try:
        server.cleanup()
    except Exception:
        pass


@pytest.fixture
async def pg_database(postgres_url, tmp_path):
    db = Database(postgres_url, tmp_path / "unused.sqlite3")
    await db.connect()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_postgres_user_stats(pg_database):
    db = pg_database
    await db.register_user(user_id=1, username="one", first_name="One")
    await db.register_user(user_id=2, username="two", first_name="Two")
    await db.register_user(user_id=3, username="three", first_name="Three")
    await db.set_banned(3, True, "spam")
    await db.mark_user_blocked(2)
    await db.set_db_admin(1, True)

    stats = await db.user_stats()
    print("POSTGRES STATS:", stats)
    assert stats["total"] == 3
    assert stats["active"] == 3
    assert stats["banned"] == 1
    assert stats["blocked"] == 1


@pytest.mark.asyncio
async def test_postgres_list_and_search_users(pg_database):
    db = pg_database
    users = await db.list_users(offset=0, limit=10)
    assert {u.user_id for u in users} >= {1, 2, 3}
    found = await db.search_users("two")
    assert any(u.user_id == 2 for u in found), found
    admin = await db.get_user(1)
    assert admin and admin.is_admin


@pytest.mark.asyncio
async def test_postgres_song_and_channel_roundtrip(pg_database):
    db = pg_database
    song = await db.save_song(title="Track", artist="Artist", file_id="f-1", source_url=None)
    assert song.id
    await db.increment_play_count(song.id)
    again = await db.get_song(song.id)
    assert again and again.play_count == 1
    channel = await db.add_required_channel(chat_id=-1001, username="chan", title="Chan")
    assert channel.id
    channels = await db.list_required_channels()
    assert any(c.chat_id == -1001 for c in channels)
