"""TidalAdapter — official TIDAL API v2, implementing the common interface.

Reads: playlists, playlist tracks, library (collection) tracks, search.
Writes: create_playlist / add_tracks — enabled, but gated by the scopes the
registered app actually holds. If the app lacks playlists.write, TIDAL returns
403 and we surface a clear CapabilityNotSupportedError (per the brief).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..adapter import MusicServiceAdapter
from ..config import TidalConfig, get_tidal_config
from ..errors import AuthenticationRequiredError, CapabilityNotSupportedError
from ..models import Playlist, PlaylistDetails, Track
from .auth import TidalTokens, TidalTokenStore, refresh_tokens
from .client import TidalClient

_ISO_DURATION = re.compile(
    r"P(?:(?P<d>\d+)D)?T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?"
)


def parse_iso8601_duration_ms(value: Optional[str]) -> Optional[int]:
    """Convert an ISO-8601 duration like 'PT3M20S' to milliseconds."""
    if not value:
        return None
    match = _ISO_DURATION.fullmatch(value)
    if not match:
        return None
    parts = {k: int(v) if v else 0 for k, v in match.groupdict().items()}
    total = parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"]
    return total * 1000


def _index(included: list[dict]) -> dict[tuple[str, str], dict]:
    return {(r.get("type"), r.get("id")): r for r in included if r.get("type") and r.get("id")}


class TidalAdapter(MusicServiceAdapter):
    service = "tidal"
    capabilities = {
        "search": True,
        "read_playlists": True,
        "read_library": True,
        # Write capability is "declared" here but verified against granted scopes
        # at runtime (see ensure_write_capability / 403 handling).
        "create_playlist": True,
        "add_tracks": True,
        "add_to_library": True,
    }

    def __init__(
        self,
        config: Optional[TidalConfig] = None,
        *,
        token_store: Optional[TidalTokenStore] = None,
    ) -> None:
        self._config = config or get_tidal_config()
        self._token_store = token_store or TidalTokenStore()
        self._tokens: Optional[TidalTokens] = None

    # ---- auth -------------------------------------------------------------

    async def authenticate(self) -> None:
        tokens = self._token_store.load()
        if tokens is None:
            raise AuthenticationRequiredError(
                user_message=(
                    "Not logged in to TIDAL. Run 'python -m music_service.cli auth "
                    "tidal' to complete the OAuth login."
                ),
                service="tidal",
            )
        if tokens.is_expired():
            tokens = refresh_tokens(
                tokens=tokens,
                client_id=self._config.client_id,
                client_secret=self._config.client_secret,
            )
            self._token_store.save(tokens)
        if not tokens.user_id:
            raise AuthenticationRequiredError(
                user_message="TIDAL session is missing the user id; please log in again.",
                service="tidal",
            )
        self._tokens = tokens

    def _granted_scopes(self) -> set[str]:
        return set((self._tokens.scope or "").split()) if self._tokens else set()

    def has_write_scope(self) -> bool:
        scopes = self._granted_scopes()
        return "playlists.write" in scopes or "w_usr" in scopes

    def has_collection_write_scope(self) -> bool:
        scopes = self._granted_scopes()
        return "collection.write" in scopes or "w_usr" in scopes

    def _client(self) -> TidalClient:
        assert self._tokens is not None, "call authenticate() first"
        return TidalClient(
            access_token=self._tokens.access_token, country=self._config.country
        )

    def _user_id(self) -> str:
        assert self._tokens is not None and self._tokens.user_id
        return self._tokens.user_id

    # ---- mapping ----------------------------------------------------------

    def _map_playlist(self, raw: dict[str, Any]) -> Playlist:
        attrs = raw.get("attributes", {})
        return Playlist(
            id=raw.get("id", ""),
            service="tidal",
            name=attrs.get("name", "(untitled)"),
            description=None,  # v2 playlist attributes expose no description
            track_count=attrs.get("numberOfItems"),
        )

    def _map_track(self, raw: dict[str, Any], idx: dict[tuple[str, str], dict]) -> Track:
        attrs = raw.get("attributes", {})
        rels = raw.get("relationships", {})

        def _names(rel_key: str) -> list[str]:
            out = []
            for ref in (rels.get(rel_key, {}).get("data") or []):
                res = idx.get((ref.get("type"), ref.get("id")))
                if res:
                    name = res.get("attributes", {}).get("name") or res.get("attributes", {}).get("title")
                    if name:
                        out.append(name)
            return out

        artists = _names("artists")
        albums = _names("albums")
        ext = attrs.get("externalLinks") or []
        url = ext[0].get("href") if ext and isinstance(ext[0], dict) else None
        return Track(
            id=raw.get("id", ""),
            service="tidal",
            title=attrs.get("title", "(unknown)"),
            artist=", ".join(artists) if artists else "(unknown)",
            album=albums[0] if albums else None,
            duration_ms=parse_iso8601_duration_ms(attrs.get("duration")),
            isrc=attrs.get("isrc"),
            explicit=attrs.get("explicit"),
            url=url,
        )

    def _map_tracks_from(self, data: list[dict], included: list[dict]) -> list[Track]:
        idx = _index(included)
        tracks: list[Track] = []
        for ref in data:
            # relationship endpoints return identifier objects; resolve to full resource
            full = idx.get((ref.get("type"), ref.get("id")), ref)
            if full.get("type") == "tracks":
                tracks.append(self._map_track(full, idx))
        return tracks

    # ---- read API ---------------------------------------------------------

    async def get_playlists(self) -> list[Playlist]:
        async with self._client() as client:
            data, _ = await client.get_user_playlists(self._user_id())
        return [self._map_playlist(p) for p in data]

    async def get_playlist(self, playlist_id: str) -> PlaylistDetails:
        async with self._client() as client:
            single = await client.get_playlist(playlist_id)
            data, included = await client.get_playlist_items(playlist_id)
        pl_raw = single.get("data", {}) if isinstance(single, dict) else {}
        playlist = (
            self._map_playlist(pl_raw)
            if pl_raw
            else Playlist(id=playlist_id, service="tidal", name="(unknown)")
        )
        tracks = self._map_tracks_from(data, included)
        playlist.track_count = len(tracks)
        return PlaylistDetails(playlist=playlist, tracks=tracks)

    async def get_library_tracks(self, limit: Optional[int] = None) -> list[Track]:
        async with self._client() as client:
            data, included = await client.get_collection_tracks(self._user_id(), max_items=limit)
        tracks = self._map_tracks_from(data, included)
        return tracks[:limit] if limit else tracks

    async def search_tracks(self, query: str, limit: int = 20) -> list[Track]:
        async with self._client() as client:
            data, included = await client.search_tracks(query, max_items=limit)
        tracks = self._map_tracks_from(data, included)
        return tracks[:limit]

    # ---- write API --------------------------------------------------------

    def _require_write(self) -> None:
        if not self.has_write_scope():
            raise CapabilityNotSupportedError(
                user_message=(
                    "Playlist writing is not available for this TIDAL application "
                    "(the granted token lacks the playlists.write scope). Re-register "
                    "the app with write scope and re-authorize."
                ),
                service="tidal",
            )

    async def create_playlist(
        self, name: str, description: Optional[str] = None
    ) -> Playlist:
        self._require_write()
        # Note: the v2 API cannot set a description on create; it is ignored.
        async with self._client() as client:
            result = await client.create_playlist(name, access_type="UNLISTED")
        raw = result.get("data", {}) if isinstance(result, dict) else {}
        return self._map_playlist(raw) if raw else Playlist(
            id="", service="tidal", name=name
        )

    async def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        self._require_write()
        async with self._client() as client:
            await client.add_tracks(playlist_id, track_ids)

    async def delete_playlist(self, playlist_id: str) -> None:
        self._require_write()
        async with self._client() as client:
            await client.delete_playlist(playlist_id)

    async def add_to_library(self, track_ids: list[str]) -> None:
        """Add tracks to the user's TIDAL favorites (My Collection)."""
        if not self.has_collection_write_scope():
            raise CapabilityNotSupportedError(
                user_message=(
                    "Adding favorites is not available for this TIDAL application "
                    "(token lacks collection.write). Re-authorize with that scope."
                ),
                service="tidal",
            )
        async with self._client() as client:
            await client.add_to_collection(self._user_id(), track_ids)
