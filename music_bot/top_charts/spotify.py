from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class SpotifyTrack:
    title: str
    artist: str

    @property
    def query(self) -> str:
        return f"{self.artist} {self.title}"


class SpotifyProvider:
    GLOBAL_TOP_50_PLAYLIST = "37i9dQZEVXbMDoHDwVN2tF"

    def __init__(self, client_id: str, client_secret: str, playlist_id: str | None = None) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.playlist_id = playlist_id or self.GLOBAL_TOP_50_PLAYLIST

    async def top_tracks(self, limit: int = 50) -> list[SpotifyTrack]:
        return await asyncio.to_thread(self._top_tracks_sync, limit)

    def _top_tracks_sync(self, limit: int) -> list[SpotifyTrack]:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials

        client = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
        )
        response = client.playlist_items(
            self.playlist_id,
            limit=min(limit, 100),
            fields="items(track(name,artists(name)))",
        )
        tracks: list[SpotifyTrack] = []
        for item in response.get("items", []):
            track = item.get("track") or {}
            artists = track.get("artists") or []
            if track.get("name") and artists:
                tracks.append(SpotifyTrack(track["name"], artists[0]["name"]))
        return tracks