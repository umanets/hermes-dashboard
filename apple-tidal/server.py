"""Hermes music MCP server (FastMCP, stdio transport).

Thin tool layer over ``music_service.app.MusicToolsService``. Every tool:
  * read-only tools run without confirmation;
  * write tools (create/add/sync) require ``confirmed=true`` and are preceded by
    a preview — see music_preview_playlist / music_sync_playlist (dry-run).

Run standalone:        python server.py
Register in Hermes (~/.hermes/config.yaml):
    mcp_servers:
      music:
        transport: stdio
        command: python
        args: ["D:/work/hermes-tools/apple-tidal/server.py"]
"""

from __future__ import annotations

import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

from music_service.app import MusicToolsService
from music_service.errors import MusicServiceError

logging.basicConfig(level=logging.INFO)

mcp = FastMCP("music")
APP = MusicToolsService()


def _error(exc: Exception) -> dict:
    if isinstance(exc, MusicServiceError):
        return {"error": exc.to_dict()}
    logging.exception("Unexpected error in music tool")
    return {"error": {"type": "UnexpectedError", "message": exc.__class__.__name__}}


async def _run(awaitable) -> dict:
    try:
        return await awaitable
    except Exception as exc:  # noqa: BLE001 - never crash the MCP transport
        return _error(exc)


def _run_sync(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# ---- read-only tools ------------------------------------------------------

@mcp.tool()
async def music_get_accounts_status() -> dict:
    """Report auth/connection status and capabilities for each music service."""
    return await _run(APP.accounts_status())


@mcp.tool()
async def music_get_playlists(service: str) -> dict:
    """List the user's playlists for a service ("apple_music" or "tidal")."""
    return await _run(APP.get_playlists(service))


@mcp.tool()
async def music_get_playlist_tracks(service: str, playlist_id: str) -> dict:
    """Get the tracks of a single playlist (read-only)."""
    return await _run(APP.get_playlist_tracks(service, playlist_id))


@mcp.tool()
async def music_search_tracks(service: str, query: str, limit: int = 20) -> dict:
    """Search a service's catalog for tracks."""
    return await _run(APP.search_tracks(service, query, limit=limit))


@mcp.tool()
async def music_recommend_tracks(
    prompt: str,
    services: Optional[list[str]] = None,
    limit: int = 25,
    exclude_existing: bool = True,
    reference_artists: Optional[list[str]] = None,
    allow_versions: bool = False,
) -> dict:
    """Recommend tracks from your profile + prompt and return a draft preview.

    Read-only: nothing is created. The response contains a ``draft_id`` to pass
    to music_create_playlist after you confirm.
    """
    return await _run(
        APP.recommend(
            prompt,
            services=tuple(services or ["tidal"]),
            limit=limit,
            exclude_existing=exclude_existing,
            reference_artists=reference_artists,
            allow_versions=allow_versions,
        )
    )


@mcp.tool()
async def music_get_library_artists(service: str, limit: int = 200) -> dict:
    """List the user's distinct library artists (by track count).

    Use this to build the mood base: read the artists, tag the relevant ones via
    music_save_artist_moods so music_get_seed_artists has real coverage.
    """
    return await _run(APP.get_library_artists(service, limit=limit))


@mcp.tool()
async def music_get_seed_artists(mood: str) -> dict:
    """Return the user's artists tagged with a mood (profile tags + cached tags).

    Use this to pick mood-appropriate seed artists before doing web/Reddit
    discovery. If coverage is sparse, tag more via music_save_artist_moods.
    """
    return _run_sync(APP.get_seed_artists, mood)


@mcp.tool()
async def music_save_artist_moods(tags: dict) -> dict:
    """Persist artist→moods tags into the local mood base (hybrid cache).

    tags example: {"Woodkid": ["energy", "cinematic"], "Rammstein": ["energy"]}.
    """
    return _run_sync(APP.save_artist_moods, tags)


@mcp.tool()
async def music_resolve_tracks(
    items: list[str],
    service: str,
    name: Optional[str] = None,
    exclude_existing: bool = True,
) -> dict:
    """Resolve a list of "Artist - Title" candidates into a provider playlist draft.

    This is how the agent turns its own web/Reddit/mood research into a concrete
    playlist: pass the discovered tracks; each is matched on `service` (search +
    matching, version-safe, dedup, optionally excluding already-saved tracks).
    Read-only — returns a draft_id + unmatched report; create with
    music_create_playlist(draft_id, [service], confirmed=true).
    """
    return await _run(
        APP.resolve_tracks(items, service, name=name, exclude_existing=exclude_existing)
    )


@mcp.tool()
async def music_preview_playlist(draft_id: str) -> dict:
    """Return a previously generated draft (read-only; never modifies services)."""
    return _run_sync(APP.preview_playlist, draft_id)


# ---- write tools (require confirmed=true) ---------------------------------

@mcp.tool()
async def music_create_playlist(
    draft_id: str, services: list[str], confirmed: bool = False
) -> dict:
    """Create a playlist from a draft on the given services. Requires confirmed=true."""
    return await _run(APP.create_playlist(draft_id, services=services, confirmed=confirmed))


@mcp.tool()
async def music_add_tracks(
    service: str, playlist_id: str, track_ids: list[str], confirmed: bool = False
) -> dict:
    """Add tracks to an existing playlist. Requires confirmed=true."""
    return await _run(
        APP.add_tracks(service, playlist_id, track_ids, confirmed=confirmed)
    )


@mcp.tool()
async def music_sync_playlist(
    source_service: str,
    playlist_id: str,
    destination_services: list[str],
    confirmed: bool = False,
    name: Optional[str] = None,
) -> dict:
    """Copy/sync a playlist to other services.

    Without confirmed=true this is a dry-run: it returns matched / ambiguous /
    substituted / unmatched tracks but creates nothing. With confirmed=true it
    also creates the destination playlist(s) and adds matched tracks.
    """
    return await _run(
        APP.sync_playlist(
            source_service, playlist_id, destination_services, confirmed=confirmed, name=name
        )
    )


@mcp.tool()
async def music_sync_favorites(
    direction: str = "both",
    confirmed: bool = False,
    max_per_direction: int = 50,
) -> dict:
    """Sync favorite/liked tracks between Apple Music and TIDAL.

    "Favorites" = TIDAL My Collection ↔ Apple Music library songs. Performs a
    union (adds what's missing on each side; never deletes). direction is "both"
    (default), "to_apple", or "to_tidal".

    Without confirmed=true this is a dry-run preview (matched / unmatched per
    direction, counts). With confirmed=true it adds the matched tracks. At most
    max_per_direction tracks are transferred per run (rate-limit safety).
    """
    return await _run(
        APP.sync_favorites(
            direction=direction, confirmed=confirmed, max_per_direction=max_per_direction
        )
    )


@mcp.tool()
async def music_record_feedback(
    feedback: str,
    canonical_id: Optional[str] = None,
    service: Optional[str] = None,
    track_id: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """Record feedback on a recommendation (liked, disliked, already_known, ...)."""
    return _run_sync(
        APP.record_feedback,
        feedback,
        canonical_id=canonical_id,
        service=service,
        track_id=track_id,
        note=note,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
