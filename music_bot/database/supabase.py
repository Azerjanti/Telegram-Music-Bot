from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

from music_bot.database.db import Favorite, RequiredChannel, Song, UserRecord
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

    async def _count(self, table: str, filters: dict[str, str]) -> int:
        """Return the exact row count for the given PostgREST filters."""
        if not self._client:
            raise RuntimeError("Supabase database is not connected")
        response = await self._client.request(
            "GET",
            table,
            params={"select": "id", **filters},
            headers={"Prefer": "count=exact", "Range": "0-0"},
        )
        response.raise_for_status()
        content_range = response.headers.get("content-range", "")
        try:
            return int(content_range.split("/")[1])
        except (IndexError, ValueError):
            return 0

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
    # ------------------------------------------------------------------ users

    @staticmethod
    def _row_to_user(row: dict[str, Any]) -> UserRecord:
        return UserRecord(
            user_id=int(row["user_id"]),
            username=row.get("username"),
            first_name=row.get("first_name"),
            is_admin=bool(row.get("is_admin", False)),
            is_banned=bool(row.get("is_banned", False)),
            banned_reason=row.get("banned_reason"),
            started_at=row.get("started_at"),
            last_seen_at=row.get("last_seen_at"),
            blocked_at=row.get("blocked_at"),
        )

    async def register_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> UserRecord:
        payload: dict[str, Any] = {"user_id": user_id}
        if username is not None:
            payload["username"] = username
        if first_name is not None:
            payload["first_name"] = first_name
        if last_name is not None:
            payload["last_name"] = last_name
        await self._request(
            "POST",
            "users",
            params={"on_conflict": "user_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload,
        )
        # Always touch last_seen_at so the "active users" counter stays fresh.
        await self._request(
            "PATCH",
            "users",
            params={"user_id": f"eq.{user_id}"},
            headers={"Prefer": "return=minimal"},
            json={"last_seen_at": _now_iso()},
        )
        return (await self.get_user(user_id)) or UserRecord(
            user_id=user_id,
            username=username,
            first_name=first_name,
            is_admin=False,
            is_banned=False,
            banned_reason=None,
            started_at=None,
            last_seen_at=None,
            blocked_at=None,
        )

    async def get_user(self, user_id: int) -> UserRecord | None:
        rows = await self._request(
            "GET",
            "users",
            params={"select": "*", "user_id": f"eq.{user_id}", "limit": "1"},
        )
        return self._row_to_user(rows[0]) if rows else None

    async def is_banned(self, user_id: int) -> bool:
        record = await self.get_user(user_id)
        return bool(record and record.is_banned)

    async def set_banned(self, user_id: int, banned: bool, reason: str | None = None) -> None:
        await self._request(
            "POST",
            "users",
            params={"on_conflict": "user_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "user_id": user_id,
                "is_banned": banned,
                "banned_reason": reason,
            },
        )

    async def mark_user_blocked(self, user_id: int) -> None:
        record = await self.get_user(user_id)
        if not record or record.blocked_at:
            return
        await self._request(
            "PATCH",
            "users",
            params={"user_id": f"eq.{user_id}"},
            headers={"Prefer": "return=minimal"},
            json={"blocked_at": _now_iso()},
        )

    async def clear_user_blocked(self, user_id: int) -> None:
        await self._request(
            "PATCH",
            "users",
            params={"user_id": f"eq.{user_id}"},
            headers={"Prefer": "return=minimal"},
            json={"blocked_at": None},
        )

    async def set_db_admin(self, user_id: int, is_admin: bool) -> None:
        await self._request(
            "POST",
            "users",
            params={"on_conflict": "user_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"user_id": user_id, "is_admin": is_admin},
        )

    async def user_stats(self, active_days: int = 7) -> dict[str, int]:
        total = await self._count("users", {})
        active = await self._count(
            "users", {"last_seen_at": f"gte.{_ago_iso(int(active_days))}"}
        )
        blocked = await self._count(
            "users", {"blocked_at": "not.is.null", "is_banned": "eq.false"}
        )
        banned = await self._count("users", {"is_banned": "eq.true"})
        return {
            "total": int(total),
            "active": int(active),
            "blocked": int(blocked),
            "banned": int(banned),
        }

    async def list_users(self, offset: int = 0, limit: int = 20) -> list[UserRecord]:
        rows = await self._request(
            "GET",
            "users",
            params={
                "select": "*",
                "order": "last_seen_at.desc",
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        return [self._row_to_user(row) for row in rows or []]

    async def search_users(self, value: str, limit: int = 20) -> list[UserRecord]:
        try:
            user_id_match = int(value)
        except (TypeError, ValueError):
            user_id_match = None
        like = f"*{value}*"
        or_clause = f"(username.ilike.{like},first_name.ilike.{like})"
        if user_id_match is not None:
            or_clause = (
                f"(user_id.eq.{user_id_match},username.ilike.{like},first_name.ilike.{like})"
            )
        rows = await self._request(
            "GET",
            "users",
            params={"select": "*", "or": or_clause, "limit": str(limit)},
        )
        return [self._row_to_user(row) for row in rows or []]

    # ------------------------------------------------------ required channels

    @staticmethod
    def _row_to_channel(row: dict[str, Any]) -> RequiredChannel:
        return RequiredChannel(
            id=int(row["id"]),
            chat_id=int(row["chat_id"]),
            username=row.get("username"),
            title=row.get("title"),
        )

    async def add_required_channel(
        self, chat_id: int, username: str | None, title: str | None
    ) -> RequiredChannel:
        rows = await self._request(
            "POST",
            "required_channels",
            params={"on_conflict": "chat_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "chat_id": chat_id,
                "username": username,
                "title": title,
            },
        )
        if not rows:
            raise RuntimeError("Supabase did not return the saved channel")
        return self._row_to_channel(rows[0])

    async def remove_required_channel(self, channel_id: int) -> None:
        await self._request(
            "DELETE",
            "required_channels",
            params={"id": f"eq.{channel_id}"},
        )

    async def list_required_channels(self) -> list[RequiredChannel]:
        rows = await self._request(
            "GET",
            "required_channels",
            params={"select": "*", "order": "id.asc"},
        )
        return [self._row_to_channel(row) for row in rows or []]
