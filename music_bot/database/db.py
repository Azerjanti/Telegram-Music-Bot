from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from music_bot.search_engine import normalize, similarity


@dataclass(frozen=True)
class Song:
    id: int
    title: str
    artist: str
    file_id: str
    source_url: str | None
    play_count: int


@dataclass(frozen=True)
class Favorite:
    id: int
    file_id: str
    title: str
    artist: str


class Database:
    def __init__(self, database_url: str | None, sqlite_path: Path) -> None:
        self.database_url = database_url
        self.sqlite_path = sqlite_path
        self._pool: Any = None
        self._sqlite: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def is_postgres(self) -> bool:
        return bool(self.database_url and self.database_url.startswith(("postgres://", "postgresql://")))

    async def connect(self) -> None:
        if self.is_postgres:
            import asyncpg

            self._pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
        else:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self._sqlite = sqlite3.connect(self.sqlite_path, check_same_thread=False)
            self._sqlite.row_factory = sqlite3.Row
        await self.init()

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
        if self._sqlite:
            self._sqlite.close()

    async def init(self) -> None:
        sql = """
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER {serial} PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                normalized_artist TEXT NOT NULL,
                file_id TEXT NOT NULL UNIQUE,
                source_url TEXT,
                play_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """
        favorites_sql = """
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, file_id)
            )
        """
        if self.is_postgres:
            sql = sql.format(serial="GENERATED ALWAYS AS IDENTITY")
            async with self._pool.acquire() as connection:
                await connection.execute(sql)
                await connection.execute(
                    favorites_sql.replace(
                        "id INTEGER PRIMARY KEY AUTOINCREMENT",
                        "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY",
                    ).replace("user_id INTEGER", "user_id BIGINT")
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS songs_artist_idx ON songs (normalized_artist)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS songs_plays_idx ON songs (play_count DESC)"
                )
        else:
            sql = sql.format(serial="")
            async with self._lock:
                self._sqlite.execute(sql)
                self._sqlite.execute(favorites_sql)
                self._sqlite.execute("CREATE INDEX IF NOT EXISTS songs_artist_idx ON songs (normalized_artist)")
                self._sqlite.execute("CREATE INDEX IF NOT EXISTS songs_plays_idx ON songs (play_count DESC)")
                self._sqlite.execute(
                    "CREATE INDEX IF NOT EXISTS favorites_user_idx ON favorites (user_id, created_at DESC)"
                )
                self._sqlite.commit()

    @staticmethod
    def _row_to_song(row: Any) -> Song:
        values = dict(row) if not isinstance(row, sqlite3.Row) else dict(row)
        return Song(
            id=int(values["id"]),
            title=values["title"],
            artist=values["artist"],
            file_id=values["file_id"],
            source_url=values.get("source_url"),
            play_count=int(values["play_count"]),
        )

    async def search_song(self, query: str, threshold: float = 70) -> Song | None:
        tokens = [token for token in normalize(query).split() if len(token) > 1]
        if not tokens:
            return None
        if self.is_postgres:
            clauses = [
                f"(normalized_title ILIKE ${index} OR normalized_artist ILIKE ${index})"
                for index in range(1, len(tokens) + 1)
            ]
            async with self._pool.acquire() as connection:
                rows = await connection.fetch(
                    f"SELECT * FROM songs WHERE {' OR '.join(clauses)} ORDER BY play_count DESC LIMIT 100",
                    *[f"%{token}%" for token in tokens],
                )
        else:
            clauses = [
                "(normalized_title LIKE ? OR normalized_artist LIKE ?)"
                for _ in tokens
            ]
            params = [value for token in tokens for value in (f"%{token}%", f"%{token}%")]
            async with self._lock:
                rows = self._sqlite.execute(
                    f"SELECT * FROM songs WHERE {' OR '.join(clauses)} ORDER BY play_count DESC LIMIT 100",
                    params,
                ).fetchall()

        candidates = [self._row_to_song(row) for row in rows]
        best = max(candidates, key=lambda song: similarity(query, song.title, song.artist), default=None)
        return best if best and similarity(query, best.title, best.artist) >= threshold else None

    async def save_song(self, title: str, artist: str, file_id: str, source_url: str | None) -> Song:
        values = (title, artist, normalize(title), normalize(artist), file_id, source_url)
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                row = await connection.fetchrow(
                    """
                    INSERT INTO songs (title, artist, normalized_title, normalized_artist, file_id, source_url)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (file_id) DO UPDATE SET title = EXCLUDED.title, artist = EXCLUDED.artist
                    RETURNING *
                    """,
                    *values,
                )
        else:
            async with self._lock:
                self._sqlite.execute(
                    """
                    INSERT INTO songs (title, artist, normalized_title, normalized_artist, file_id, source_url)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET title = excluded.title, artist = excluded.artist
                    """,
                    values,
                )
                self._sqlite.commit()
                row = self._sqlite.execute("SELECT * FROM songs WHERE file_id = ?", (file_id,)).fetchone()
        return self._row_to_song(row)

    async def get_song(self, song_id: int) -> Song | None:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                row = await connection.fetchrow("SELECT * FROM songs WHERE id = $1", song_id)
        else:
            async with self._lock:
                row = self._sqlite.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        return self._row_to_song(row) if row else None

    async def get_song_by_file_id(self, file_id: str) -> Song | None:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                row = await connection.fetchrow("SELECT * FROM songs WHERE file_id = $1", file_id)
        else:
            async with self._lock:
                row = self._sqlite.execute("SELECT * FROM songs WHERE file_id = ?", (file_id,)).fetchone()
        return self._row_to_song(row) if row else None

    async def increment_play_count(self, song_id: int) -> None:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                await connection.execute("UPDATE songs SET play_count = play_count + 1, updated_at = CURRENT_TIMESTAMP WHERE id = $1", song_id)
        else:
            async with self._lock:
                self._sqlite.execute("UPDATE songs SET play_count = play_count + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (song_id,))
                self._sqlite.commit()

    async def artist_songs(self, artist: str, limit: int = 50) -> list[Song]:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                rows = await connection.fetch(
                    "SELECT * FROM songs WHERE normalized_artist ILIKE $1 ORDER BY play_count DESC, title LIMIT $2",
                    f"%{normalize(artist)}%",
                    limit,
                )
        else:
            async with self._lock:
                rows = self._sqlite.execute(
                    "SELECT * FROM songs WHERE normalized_artist LIKE ? ORDER BY play_count DESC, title LIMIT ?",
                    (f"%{normalize(artist)}%", limit),
                ).fetchall()
        return [self._row_to_song(row) for row in rows]

    async def top_songs(self, limit: int = 50) -> list[Song]:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                rows = await connection.fetch("SELECT * FROM songs ORDER BY play_count DESC, title LIMIT $1", limit)
        else:
            async with self._lock:
                rows = self._sqlite.execute("SELECT * FROM songs ORDER BY play_count DESC, title LIMIT ?", (limit,)).fetchall()
        return [self._row_to_song(row) for row in rows]

    @staticmethod
    def _row_to_favorite(row: Any) -> Favorite:
        values = dict(row)
        return Favorite(
            id=int(values["id"]),
            file_id=values["file_id"],
            title=values["title"],
            artist=values["artist"],
        )

    async def is_favorite(self, user_id: int, file_id: str) -> bool:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                row = await connection.fetchrow(
                    "SELECT id FROM favorites WHERE user_id = $1 AND file_id = $2",
                    user_id,
                    file_id,
                )
        else:
            async with self._lock:
                row = self._sqlite.execute(
                    "SELECT id FROM favorites WHERE user_id = ? AND file_id = ?",
                    (user_id, file_id),
                ).fetchone()
        return bool(row)

    async def add_favorite(self, user_id: int, song: Song) -> Favorite:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                row = await connection.fetchrow(
                    """
                    INSERT INTO favorites (user_id, file_id, title, artist)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id, file_id) DO UPDATE SET title = EXCLUDED.title, artist = EXCLUDED.artist
                    RETURNING *
                    """,
                    user_id,
                    song.file_id,
                    song.title,
                    song.artist,
                )
        else:
            async with self._lock:
                self._sqlite.execute(
                    """
                    INSERT INTO favorites (user_id, file_id, title, artist)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, file_id) DO UPDATE SET title = excluded.title, artist = excluded.artist
                    """,
                    (user_id, song.file_id, song.title, song.artist),
                )
                self._sqlite.commit()
                row = self._sqlite.execute(
                    "SELECT * FROM favorites WHERE user_id = ? AND file_id = ?",
                    (user_id, song.file_id),
                ).fetchone()
        return self._row_to_favorite(row)

    async def remove_favorite(self, user_id: int, file_id: str) -> None:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                await connection.execute(
                    "DELETE FROM favorites WHERE user_id = $1 AND file_id = $2",
                    user_id,
                    file_id,
                )
        else:
            async with self._lock:
                self._sqlite.execute(
                    "DELETE FROM favorites WHERE user_id = ? AND file_id = ?",
                    (user_id, file_id),
                )
                self._sqlite.commit()

    async def list_favorites(self, user_id: int, limit: int = 50) -> list[Favorite]:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                rows = await connection.fetch(
                    "SELECT * FROM favorites WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
                    user_id,
                    limit,
                )
        else:
            async with self._lock:
                rows = self._sqlite.execute(
                    "SELECT * FROM favorites WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
        return [self._row_to_favorite(row) for row in rows]

    async def get_favorite(self, user_id: int, favorite_id: int) -> Favorite | None:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                row = await connection.fetchrow(
                    "SELECT * FROM favorites WHERE user_id = $1 AND id = $2",
                    user_id,
                    favorite_id,
                )
        else:
            async with self._lock:
                row = self._sqlite.execute(
                    "SELECT * FROM favorites WHERE user_id = ? AND id = ?",
                    (user_id, favorite_id),
                ).fetchone()
        return self._row_to_favorite(row) if row else None

    async def delete_favorite(self, user_id: int, favorite_id: int) -> None:
        if self.is_postgres:
            async with self._pool.acquire() as connection:
                await connection.execute(
                    "DELETE FROM favorites WHERE user_id = $1 AND id = $2",
                    user_id,
                    favorite_id,
                )
        else:
            async with self._lock:
                self._sqlite.execute(
                    "DELETE FROM favorites WHERE user_id = ? AND id = ?",
                    (user_id, favorite_id),
                )
                self._sqlite.commit()