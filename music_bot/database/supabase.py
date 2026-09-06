from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from music_bot.database.db import Favorite, Song
from music_bot.search_engine import normalize, similarity


@dataclass
class SupabaseDatabase:
    """Small PostgREST repository for the songs cache table."""

    url: str
    key: str
    table: str = "songs"

    def __post_init__(self) -> None:
        self.url = self.url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"{self.url}/rest/v1",
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        await self._request("GET", self.table, params={"select": "id", "limit": "1"})

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self._client:
            raise RuntimeError("Supabase database is not connected")
        response = await self._client.request(method, path, **kwargs)
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    @staticmethod
    def _row_to_song(row: dict[str, Any]) -> Song:
        return Song(
            id=int(row["id"]),
            title=row["title"],
            artist=row["artist"],
            file_id=row["file_id"],
            source_url=row.get("source_url"),
            play_count=int(row.get("play_count", 0)),
        )

    async def _all_songs(self) -> list[Song]:
        rows = await self._request(
            "GET",
            self.table,
            params={"select": "*", "order": "play_count.desc", "limit": "500"},
        )
        return [self._row_to_song(row) for row in rows or []]

    async def search_song(self, query: str, threshold: float = 70) -> Song | None:
        songs = await self._all_songs()
        best = max(songs, key=lambda song: similarity(query, song.title, song.artist), default=None)
        return best if best and similarity(query, best.title, best.artist) >= threshold else None

    async def get_song(self, song_id: int) -> Song | None:
        rows = await self._request(
            "GET",
            self.table,
            params={"select": "*", "id": f"eq.{song_id}", "limit": "1"},
        )
        return self._row_to_song(rows[0]) if rows else None

    async def get_song_by_file_id(self, file_id: str) -> Song | None:
        rows = await self._request(
            "GET",
            self.table,
            params={"select": "*", "file_id": f"eq.{file_id}", "limit": "1"},
        )
        return self._row_to_song(rows[0]) if rows else None

    async def save_song(self, title: str, artist: str, file_id: str, source_url: str | None) -> Song:
        payload = {
            "title": title,
            "artist": artist,
            "normalized_title": normalize(title),
            "normalized_artist": normalize(artist),
            "file_id": file_id,
            "source_url": source_url,
        }
        rows = await self._request(
            "POST",
            self.table,
            params={"on_conflict": "file_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json=payload,
        )
        if not rows:
            raise RuntimeError("Supabase did not return the saved song")
        return self._row_to_song(rows[0])

    async def increment_play_count(self, song_id: int) -> None:
        song = await self.get_song(song_id)
        if not song:
            return
        await self._request(
            "PATCH",
            self.table,
            params={"id": f"eq.{song_id}"},
            json={"play_count": song.play_count + 1},
            headers={"Prefer": "return=minimal"},
        )

    async def artist_songs(self, artist: str, limit: int = 50) -> list[Song]:
        rows = await self._request(
            "GET",
            self.table,
            params={
                "select": "*",
                "normalized_artist": f"ilike.*{normalize(artist)}*",
                "order": "play_count.desc,title.asc",
                "limit": str(limit),
            },
        )
        return [self._row_to_song(row) for row in rows or []]

    async def top_songs(self, limit: int = 50) -> list[Song]:
        rows = await self._request(
            "GET",
            self.table,
            params={"select": "*", "order": "play_count.desc,title.asc", "limit": str(limit)},
        )
        return [self._row_to_song(row) for row in rows or []]

    @staticmethod
    def _row_to_favorite(row: dict[str, Any]) -> Favorite:
        return Favorite(
            id=int(row["id"]),
            file_id=row["file_id"],
            title=row["title"],
            artist=row["artist"],
        )

    async def is_favorite(self, user_id: int, file_id: str) -> bool:
        rows = await self._request(
            "GET",
            "favorites",
            params={
                "select": "id",
                "user_id": f"eq.{user_id}",
                "file_id": f"eq.{file_id}",
                "limit": "1",
            },
        )
        return bool(rows)

    async def add_favorite(self, user_id: int, song: Song) -> Favorite:
        rows = await self._request(
            "POST",
            "favorites",
            params={"on_conflict": "user_id,file_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "user_id": user_id,
                "file_id": song.file_id,
                "title": song.title,
                "artist": song.artist,
            },
        )
        if not rows:
            raise RuntimeError("Supabase did not return the saved favorite")
        return self._row_to_favorite(rows[0])

    async def remove_favorite(self, user_id: int, file_id: str) -> None:
        await self._request(
            "DELETE",
            "favorites",
            params={"user_id": f"eq.{user_id}", "file_id": f"eq.{file_id}"},
        )

    async def list_favorites(self, user_id: int, limit: int = 50) -> list[Favorite]:
        rows = await self._request(
            "GET",
            "favorites",
            params={
                "select": "*",
                "user_id": f"eq.{user_id}",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        return [self._row_to_favorite(row) for row in rows or []]

    async def get_favorite(self, user_id: int, favorite_id: int) -> Favorite | None:
        rows = await self._request(
            "GET",
            "favorites",
            params={
                "select": "*",
                "user_id": f"eq.{user_id}",
                "id": f"eq.{favorite_id}",
                "limit": "1",
            },
        )
        return self._row_to_favorite(rows[0]) if rows else None

    async def delete_favorite(self, user_id: int, favorite_id: int) -> None:
        await self._request(
            "DELETE",
            "favorites",
            params={"user_id": f"eq.{user_id}", "id": f"eq.{favorite_id}"},
        )