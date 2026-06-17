"""Thin async HTTP client for the Apple Music API.

Knows nothing about the domain model — it returns raw JSON dicts. The adapter
maps those into ``Track`` / ``Playlist``. This separation keeps HTTP concerns
(auth headers, pagination, error mapping, redaction) in one place.

Read-only endpoints only, for the first deliverable:
  * GET /v1/me/library/playlists
  * GET /v1/me/library/playlists/{id}/tracks
  * GET /v1/catalog/{storefront}/search
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from ..errors import (
    AuthenticationRequiredError,
    MusicServiceError,
    RateLimitError,
    TrackNotFoundError,
)

logger = logging.getLogger("hermes.music.apple")

BASE_URL = "https://api.music.apple.com"


class AppleMusicClient:
    def __init__(
        self,
        *,
        developer_token: str,
        music_user_token: Optional[str],
        storefront: str = "us",
        http: Optional[httpx.AsyncClient] = None,
        timeout: float = 30.0,
    ) -> None:
        self._developer_token = developer_token
        self._music_user_token = music_user_token
        self.storefront = storefront
        self._http = http
        self._owns_http = http is None
        self._timeout = timeout

    async def __aenter__(self) -> "AppleMusicClient":
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=self._timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()

    # ---- internals --------------------------------------------------------

    def _headers(self, *, user_scoped: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._developer_token}"}
        if user_scoped:
            if not self._music_user_token:
                raise AuthenticationRequiredError(
                    user_message=(
                        "Apple Music user authorization is missing. Obtain a "
                        "Music User Token via the MusicKit authorize page."
                    ),
                    service="apple_music",
                )
            headers["Music-User-Token"] = self._music_user_token
        return headers

    async def _get(
        self, path: str, *, user_scoped: bool, params: Optional[dict] = None
    ) -> dict[str, Any]:
        assert self._http is not None, "Use 'async with AppleMusicClient(...)'"
        # Log metadata only — never the headers (they contain tokens).
        logger.debug("Apple GET %s params=%s user_scoped=%s", path, params, user_scoped)
        resp = await self._http.get(
            path, headers=self._headers(user_scoped=user_scoped), params=params
        )
        return self._handle(resp, path)

    async def _post(
        self,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        user_scoped: bool = True,
    ) -> dict[str, Any]:
        assert self._http is not None, "Use 'async with AppleMusicClient(...)'"
        logger.debug("Apple POST %s user_scoped=%s", path, user_scoped)
        headers = self._headers(user_scoped=user_scoped)
        kwargs: dict[str, Any] = {"headers": headers, "params": params}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            kwargs["json"] = json_body
        resp = await self._http.post(path, **kwargs)
        return self._handle(resp, path)

    def _handle(self, resp: httpx.Response, path: str) -> dict[str, Any]:
        status = resp.status_code
        logger.debug("Apple %s %s -> %s", resp.request.method, path, status)
        if status in (200, 201):
            return resp.json() if resp.content else {}
        if status in (202, 204):
            return {}
        if status in (401, 403):
            raise AuthenticationRequiredError(
                user_message=(
                    "Apple Music rejected the credentials (developer token or "
                    "Music User Token invalid/expired)."
                ),
                service="apple_music",
            )
        if status == 404:
            raise TrackNotFoundError(service="apple_music")
        if status == 429:
            retry_after = resp.headers.get("Retry-After")
            raise RateLimitError(
                user_message="The Apple Music API rate limit was reached.",
                service="apple_music",
                retry_after=float(retry_after) if retry_after else None,
            )
        raise MusicServiceError(
            user_message=f"Apple Music API error (HTTP {status}).",
            service="apple_music",
        )

    async def _paginate(
        self, path: str, *, user_scoped: bool, params: Optional[dict] = None
    ) -> list[dict]:
        items: list[dict] = []
        next_path: Optional[str] = path
        next_params = dict(params or {})
        while next_path:
            page = await self._get(next_path, user_scoped=user_scoped, params=next_params)
            items.extend(page.get("data", []))
            # Apple returns 'next' as a path already containing offset params.
            next_path = page.get("next")
            next_params = None  # subsequent 'next' already encodes the query
        return items

    # ---- public, read-only API -------------------------------------------

    async def get_library_playlists(self, limit: int = 100) -> list[dict]:
        return await self._paginate(
            "/v1/me/library/playlists", user_scoped=True, params={"limit": limit}
        )

    async def get_library_playlist_tracks(
        self, playlist_id: str, limit: int = 100
    ) -> list[dict]:
        return await self._paginate(
            f"/v1/me/library/playlists/{playlist_id}/tracks",
            user_scoped=True,
            params={"limit": limit},
        )

    async def get_library_songs(self, limit: int = 100) -> list[dict]:
        return await self._paginate(
            "/v1/me/library/songs", user_scoped=True, params={"limit": limit}
        )

    async def search(
        self, term: str, *, types: str = "songs", limit: int = 20
    ) -> dict[str, Any]:
        return await self._get(
            f"/v1/catalog/{self.storefront}/search",
            user_scoped=False,
            params={"term": term, "types": types, "limit": limit},
        )

    # ---- write API (library playlists) -----------------------------------

    async def create_library_playlist(
        self,
        name: str,
        *,
        description: Optional[str] = None,
        track_ids: Optional[list[str]] = None,
        track_type: str = "songs",
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {"name": name}
        if description:
            attributes["description"] = description
        body: dict[str, Any] = {"attributes": attributes}
        if track_ids:
            body["relationships"] = {
                "tracks": {"data": [{"id": tid, "type": track_type} for tid in track_ids]}
            }
        return await self._post("/v1/me/library/playlists", json_body=body, user_scoped=True)

    async def add_playlist_tracks(
        self, playlist_id: str, track_ids: list[str], *, track_type: str = "songs"
    ) -> None:
        body = {"data": [{"id": tid, "type": track_type} for tid in track_ids]}
        await self._post(
            f"/v1/me/library/playlists/{playlist_id}/tracks",
            json_body=body,
            user_scoped=True,
        )

    async def add_to_library(self, catalog_song_ids: list[str]) -> None:
        # Add catalog songs to "My Library": POST /v1/me/library?ids[songs]=a,b
        if not catalog_song_ids:
            return
        await self._post(
            "/v1/me/library",
            params={"ids[songs]": ",".join(catalog_song_ids)},
            user_scoped=True,
        )
