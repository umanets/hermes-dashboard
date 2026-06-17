"""Async HTTP client for the official TIDAL API v2 (JSON:API).

Base: https://openapi.tidal.com/v2 — responses use the JSON:API envelope
({data, included, links}). This client returns raw dicts; the adapter maps them
into the common models. Auth headers are never logged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

import httpx

from ..errors import (
    AuthenticationRequiredError,
    CapabilityNotSupportedError,
    MusicServiceError,
    RateLimitError,
    TrackNotFoundError,
)

logger = logging.getLogger("hermes.music.tidal")

BASE_URL = "https://openapi.tidal.com/v2"
JSON_API = "application/vnd.api+json"
MAX_ITEMS_PER_ADD = 50  # TIDAL hard limit on POST .../relationships/items

# TIDAL's dev API has tight rate limits, so retry 429s with bounded backoff.
DEFAULT_MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 10.0
# Small courtesy pause between cursor pages to avoid bursting into a 429.
PAGE_PAUSE_SECONDS = 0.2
# Default ceiling on items pulled when paginating reads (search/library).
DEFAULT_MAX_ITEMS = 100


class TidalClient:
    def __init__(
        self,
        *,
        access_token: str,
        country: str = "US",
        http: Optional[httpx.AsyncClient] = None,
        timeout: float = 30.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleeper: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        self._access_token = access_token
        self.country = country
        self._http = http
        self._owns_http = http is None
        self._timeout = timeout
        self._max_retries = max_retries
        # Injectable for tests so backoff doesn't actually sleep.
        self._sleep = sleeper or asyncio.sleep

    async def __aenter__(self) -> "TidalClient":
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=self._timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()

    def _headers(self, *, write: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": JSON_API,
        }
        if write:
            headers["Content-Type"] = JSON_API
        return headers

    def _handle(self, resp: httpx.Response, path: str) -> dict[str, Any]:
        status = resp.status_code
        logger.debug("TIDAL %s %s -> %s", resp.request.method, path, status)
        if status in (200, 201):
            return resp.json() if resp.content else {}
        if status == 204:
            return {}
        if status == 401:
            raise AuthenticationRequiredError(
                user_message="The TIDAL access token is invalid or expired.",
                service="tidal",
            )
        if status == 403:
            # Typically an insufficient-scope error for the registered app.
            raise CapabilityNotSupportedError(
                user_message=(
                    "TIDAL refused the operation (HTTP 403). The registered "
                    "application likely lacks the required scope (e.g. "
                    "playlists.write)."
                ),
                service="tidal",
            )
        if status == 404:
            raise TrackNotFoundError(service="tidal")
        if status == 429:
            # Reached only after retries are exhausted (see _send).
            retry_after = resp.headers.get("Retry-After")
            raise RateLimitError(
                user_message="The TIDAL API rate limit was reached.",
                service="tidal",
                retry_after=float(retry_after) if retry_after else None,
            )
        raise MusicServiceError(
            user_message=f"TIDAL API error (HTTP {status}).", service="tidal"
        )

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        write: bool = False,
    ) -> dict[str, Any]:
        assert self._http is not None, "Use 'async with TidalClient(...)'"
        merged = {"countryCode": self.country, **(params or {})}
        logger.debug("TIDAL %s %s params=%s", method, path, merged)
        delay = BACKOFF_BASE_SECONDS
        for attempt in range(self._max_retries + 1):
            resp = await self._http.request(
                method, path, headers=self._headers(write=write), params=merged, json=json_body
            )
            if resp.status_code != 429 or attempt == self._max_retries:
                return self._handle(resp, path)
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else delay
            logger.warning("TIDAL 429 on %s; backing off %.1fs (attempt %d)", path, wait, attempt + 1)
            await self._sleep(min(wait, BACKOFF_CAP_SECONDS))
            delay *= 2
        # Unreachable, but keeps type checkers happy.
        return self._handle(resp, path)

    async def _get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        return await self._send("GET", path, params=params)

    async def _post(self, path: str, json_body: dict, params: Optional[dict] = None) -> dict[str, Any]:
        return await self._send("POST", path, params=params, json_body=json_body, write=True)

    async def _paginate(
        self, path: str, params: Optional[dict] = None, *, max_items: Optional[int] = None
    ) -> tuple[list[dict], list[dict]]:
        """Return (data, included) across cursor pages, stopping at ``max_items``.

        ``max_items`` bounds how many primary ``data`` rows we collect so we don't
        burn the rate limit pulling a huge result set when only a few are needed.
        """
        data: list[dict] = []
        included: list[dict] = []
        page = await self._get(path, params)
        while True:
            rows = page.get("data") if isinstance(page.get("data"), list) else []
            data.extend(rows)
            included.extend(page.get("included", []))
            if max_items is not None and len(data) >= max_items:
                data = data[:max_items]
                break
            cursor = (page.get("links") or {}).get("next")
            if not cursor:
                break
            next_cursor = _extract_cursor(cursor)
            if not next_cursor:
                break
            await self._sleep(PAGE_PAUSE_SECONDS)
            page = await self._get(path, {**(params or {}), "page[cursor]": next_cursor})
        return data, included

    # ---- public API -------------------------------------------------------

    async def get_user_playlists(self, user_id: str) -> tuple[list[dict], list[dict]]:
        return await self._paginate("/playlists", {"filter[owners.id]": user_id})

    async def get_playlist(self, playlist_id: str) -> dict[str, Any]:
        return await self._get(f"/playlists/{playlist_id}")

    async def get_playlist_items(
        self, playlist_id: str, *, max_items: Optional[int] = None
    ) -> tuple[list[dict], list[dict]]:
        return await self._paginate(
            f"/playlists/{playlist_id}/relationships/items",
            {"include": "items,items.artists,items.albums"},
            max_items=max_items,
        )

    async def get_collection_tracks(
        self, user_id: str, *, max_items: Optional[int] = None
    ) -> tuple[list[dict], list[dict]]:
        # The user's "My Collection" saved tracks. Collection id == user id.
        return await self._paginate(
            f"/userCollections/{user_id}/relationships/tracks",
            {"include": "tracks,tracks.artists,tracks.albums"},
            max_items=max_items,
        )

    async def search_tracks(
        self, query: str, *, max_items: int = DEFAULT_MAX_ITEMS
    ) -> tuple[list[dict], list[dict]]:
        # The search resource id IS the query string (path-encoded by httpx).
        # Bounded by max_items: search returns many small pages, and fetching all
        # of them reliably trips the rate limiter.
        return await self._paginate(
            f"/searchResults/{query}/relationships/tracks",
            {"include": "tracks,tracks.artists,tracks.albums"},
            max_items=max_items,
        )

    async def create_playlist(self, name: str, *, access_type: str = "UNLISTED") -> dict[str, Any]:
        body = {
            "data": {
                "type": "playlists",
                "attributes": {"name": name, "accessType": access_type},
            }
        }
        return await self._post("/playlists", body)

    async def delete_playlist(self, playlist_id: str) -> None:
        await self._send("DELETE", f"/playlists/{playlist_id}", write=True)

    async def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        # Chunk to TIDAL's 50-item limit.
        for start in range(0, len(track_ids), MAX_ITEMS_PER_ADD):
            chunk = track_ids[start : start + MAX_ITEMS_PER_ADD]
            body = {"data": [{"id": tid, "type": "tracks"} for tid in chunk]}
            await self._post(f"/playlists/{playlist_id}/relationships/items", body)

    async def add_to_collection(self, user_id: str, track_ids: list[str]) -> None:
        # Add favorite tracks to "My Collection": POST /userCollections/{id}/relationships/tracks
        for start in range(0, len(track_ids), MAX_ITEMS_PER_ADD):
            chunk = track_ids[start : start + MAX_ITEMS_PER_ADD]
            body = {"data": [{"id": tid, "type": "tracks"} for tid in chunk]}
            await self._post(f"/userCollections/{user_id}/relationships/tracks", body)


def _extract_cursor(next_link: str) -> Optional[str]:
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(next_link).query)
    for key in ("page[cursor]", "page%5Bcursor%5D"):
        if key in qs:
            return qs[key][0]
    return None
