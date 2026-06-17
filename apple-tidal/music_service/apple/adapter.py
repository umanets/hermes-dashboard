"""AppleMusicAdapter — implements the common interface (read-only, first pass).

Write operations (create_playlist / add_tracks) are intentionally left at the
base class' "not supported" behaviour for this deliverable; they will be enabled
in the Apple write phase after approval.
"""

from __future__ import annotations

from typing import Any, Optional

from ..adapter import MusicServiceAdapter
from ..config import AppleConfig, get_apple_config
from ..errors import AuthenticationRequiredError
from ..models import Playlist, PlaylistDetails, Track
from .auth import DeveloperTokenProvider, MusicUserTokenStore
from .client import AppleMusicClient


def _to_bool_explicit(content_rating: Optional[str]) -> Optional[bool]:
    if content_rating is None:
        return None
    return content_rating == "explicit"


class AppleMusicAdapter(MusicServiceAdapter):
    service = "apple_music"
    capabilities = {
        "search": True,
        "read_playlists": True,
        "read_library": True,
        "create_playlist": True,
        "add_tracks": True,
        "add_to_library": True,
    }

    def __init__(
        self,
        config: Optional[AppleConfig] = None,
        *,
        token_store: Optional[MusicUserTokenStore] = None,
    ) -> None:
        self._config = config or get_apple_config()
        self._token_store = token_store or MusicUserTokenStore()
        self._dev_provider = DeveloperTokenProvider(
            team_id=self._config.team_id,
            key_id=self._config.key_id,
            private_key=self._config.read_private_key(),
        )

    # ---- auth -------------------------------------------------------------

    def _music_user_token(self) -> Optional[str]:
        return self._token_store.resolve(self._config.music_user_token)

    async def authenticate(self) -> None:
        # Generating the developer token validates the key material. The MUT is
        # required for user-scoped reads; surface a clear error early if absent.
        self._dev_provider.get_token()
        if not self._music_user_token():
            raise AuthenticationRequiredError(
                user_message=(
                    "Apple Music user authorization is missing. Run the MusicKit "
                    "authorize page to obtain a Music User Token, then set "
                    "APPLE_MUSIC_USER_TOKEN (or save it to the token store)."
                ),
                service="apple_music",
            )

    def _client(self) -> AppleMusicClient:
        return AppleMusicClient(
            developer_token=self._dev_provider.get_token(),
            music_user_token=self._music_user_token(),
            storefront=self._config.storefront,
        )

    # ---- mapping ----------------------------------------------------------

    def _map_playlist(self, raw: dict[str, Any]) -> Playlist:
        attrs = raw.get("attributes", {})
        desc = attrs.get("description") or {}
        return Playlist(
            id=raw.get("id", ""),
            service="apple_music",
            name=attrs.get("name", "(untitled)"),
            description=desc.get("standard") if isinstance(desc, dict) else None,
            track_count=None,  # library playlists don't return a reliable count
        )

    def _map_track(self, raw: dict[str, Any]) -> Track:
        attrs = raw.get("attributes", {})
        return Track(
            id=raw.get("id", ""),
            service="apple_music",
            title=attrs.get("name", "(unknown)"),
            artist=attrs.get("artistName", "(unknown)"),
            album=attrs.get("albumName"),
            duration_ms=attrs.get("durationInMillis"),
            isrc=attrs.get("isrc"),
            explicit=_to_bool_explicit(attrs.get("contentRating")),
            url=attrs.get("url"),
        )

    # ---- read API ---------------------------------------------------------

    async def get_playlists(self) -> list[Playlist]:
        async with self._client() as client:
            raw = await client.get_library_playlists()
        return [self._map_playlist(p) for p in raw]

    async def get_playlist(self, playlist_id: str) -> PlaylistDetails:
        async with self._client() as client:
            playlists = await client.get_library_playlists()
            match = next((p for p in playlists if p.get("id") == playlist_id), None)
            tracks_raw = await client.get_library_playlist_tracks(playlist_id)
        playlist = (
            self._map_playlist(match)
            if match
            else Playlist(id=playlist_id, service="apple_music", name="(unknown)")
        )
        tracks = [self._map_track(t) for t in tracks_raw]
        playlist.track_count = len(tracks)
        return PlaylistDetails(playlist=playlist, tracks=tracks)

    async def get_library_tracks(self, limit: Optional[int] = None) -> list[Track]:
        async with self._client() as client:
            raw = await client.get_library_songs(limit=limit or 100)
        tracks = [self._map_track(t) for t in raw]
        return tracks[:limit] if limit else tracks

    async def search_tracks(self, query: str, limit: int = 20) -> list[Track]:
        async with self._client() as client:
            result = await client.search(query, types="songs", limit=limit)
        songs = (
            result.get("results", {}).get("songs", {}).get("data", [])
            if isinstance(result, dict)
            else []
        )
        return [self._map_track(s) for s in songs]

    # ---- write API --------------------------------------------------------

    async def create_playlist(
        self, name: str, description: Optional[str] = None
    ) -> Playlist:
        async with self._client() as client:
            result = await client.create_library_playlist(name, description=description)
        data = result.get("data") if isinstance(result, dict) else None
        raw = data[0] if isinstance(data, list) and data else {}
        return self._map_playlist(raw) if raw else Playlist(
            id="", service="apple_music", name=name, description=description
        )

    async def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        # Catalog song ids (from search) use type "songs".
        async with self._client() as client:
            await client.add_playlist_tracks(playlist_id, track_ids, track_type="songs")

    async def add_to_library(self, track_ids: list[str]) -> None:
        """Add catalog songs to the user's Apple Music library ("favorites")."""
        async with self._client() as client:
            await client.add_to_library(track_ids)
