"""Minimal local debug CLI.

Examples:
    python -m music_service.cli auth apple          # run MusicKit authorize flow
    python -m music_service.cli playlists apple      # list library playlists
    python -m music_service.cli search apple "Adam Naas"

These are debugging aids; the real interface is the MCP server (server.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .errors import MusicServiceError
from .registry import get_adapter


async def _playlists(service: str) -> int:
    adapter = get_adapter(service)
    await adapter.authenticate()
    playlists = await adapter.get_playlists()
    print(json.dumps([p.model_dump() for p in playlists], indent=2, ensure_ascii=False))
    return 0


async def _search(service: str, query: str) -> int:
    adapter = get_adapter(service)
    await adapter.authenticate()
    tracks = await adapter.search_tracks(query)
    print(json.dumps([t.model_dump() for t in tracks], indent=2, ensure_ascii=False))
    return 0


def _auth(service: str) -> int:
    if service == "apple":
        from .auth_helper import run_authorize_server

        run_authorize_server()
        return 0
    if service == "tidal":
        from .tidal.login import run_login

        run_login()
        return 0
    print(f"auth not implemented for '{service}' yet.", file=sys.stderr)
    return 2


async def _create_test_playlist(service: str) -> int:
    """Create a clearly-named test playlist and add one searched track."""
    adapter = get_adapter(service)
    await adapter.authenticate()
    name = "Hermes Integration Test"
    playlist = await adapter.create_playlist(name, description="Created by Hermes music tools")
    print(f"Created playlist: {playlist.id} — {playlist.name}")
    results = await adapter.search_tracks("London Grammar Strong", limit=1)
    if results:
        await adapter.add_tracks(playlist.id, [results[0].id])
        print(f"Added track: {results[0].artist} — {results[0].title}")
    else:
        print("No track found to add.")
    return 0


async def _delete_playlist(service: str, playlist_id: str) -> int:
    adapter = get_adapter(service)
    await adapter.authenticate()
    if not hasattr(adapter, "delete_playlist"):
        print(f"delete not supported for {service}.", file=sys.stderr)
        return 2
    await adapter.delete_playlist(playlist_id)
    print(f"Deleted playlist {playlist_id}.")
    return 0


async def _cleanup_test_playlists(service: str) -> int:
    """Delete every playlist named exactly 'Hermes Integration Test'."""
    adapter = get_adapter(service)
    await adapter.authenticate()
    if not hasattr(adapter, "delete_playlist"):
        print(f"delete not supported for {service}.", file=sys.stderr)
        return 2
    playlists = await adapter.get_playlists()
    targets = [p for p in playlists if p.name == "Hermes Integration Test"]
    if not targets:
        print("No 'Hermes Integration Test' playlists found.")
        return 0
    for p in targets:
        await adapter.delete_playlist(p.id)
        print(f"Deleted {p.id} — {p.name}")
    return 0


async def _sync_favorites(direction: str, limit: int, confirmed: bool) -> int:
    """Run a full favorites sync with no MCP 2-minute timeout (for big libraries)."""
    from .app import MusicToolsService

    mode = "APPLYING" if confirmed else "DRY-RUN"
    print(f"[{mode}] favorites sync direction={direction} cap={limit}/dir … this can take several minutes.")
    app = MusicToolsService()
    res = await app.sync_favorites(direction=direction, confirmed=confirmed, max_per_direction=limit)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def _norm_service(s: str) -> str:
    return "apple_music" if s in ("apple", "apple_music") else s


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="music-tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_auth = sub.add_parser("auth", help="Authorize a service (interactive).")
    p_auth.add_argument("service", choices=["apple", "tidal"])

    p_pl = sub.add_parser("playlists", help="List playlists.")
    p_pl.add_argument("service")

    p_se = sub.add_parser("search", help="Search tracks.")
    p_se.add_argument("service")
    p_se.add_argument("query")

    p_ctp = sub.add_parser(
        "create-test-playlist",
        help="Create 'Hermes Integration Test' and add one track (WRITE).",
    )
    p_ctp.add_argument("service")

    p_del = sub.add_parser("delete-playlist", help="Delete a playlist by id (WRITE).")
    p_del.add_argument("service")
    p_del.add_argument("playlist_id")

    p_cl = sub.add_parser(
        "cleanup-test-playlists",
        help="Delete all 'Hermes Integration Test' playlists (WRITE).",
    )
    p_cl.add_argument("service")

    p_fav = sub.add_parser(
        "sync-favorites",
        help="Sync favorites Apple<->TIDAL with no 2-min timeout (for big libraries).",
    )
    p_fav.add_argument(
        "--direction", choices=["both", "to_apple", "to_tidal"], default="both"
    )
    p_fav.add_argument("--limit", type=int, default=100000, help="Max tracks per direction.")
    p_fav.add_argument("--apply", action="store_true", help="Actually write (default: dry-run).")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "auth":
            return _auth(args.service)
        if args.cmd == "playlists":
            return asyncio.run(_playlists(_norm_service(args.service)))
        if args.cmd == "search":
            return asyncio.run(_search(_norm_service(args.service), args.query))
        if args.cmd == "create-test-playlist":
            return asyncio.run(_create_test_playlist(_norm_service(args.service)))
        if args.cmd == "delete-playlist":
            return asyncio.run(_delete_playlist(_norm_service(args.service), args.playlist_id))
        if args.cmd == "cleanup-test-playlists":
            return asyncio.run(_cleanup_test_playlists(_norm_service(args.service)))
        if args.cmd == "sync-favorites":
            return asyncio.run(_sync_favorites(args.direction, args.limit, args.apply))
    except MusicServiceError as exc:
        print(f"Error: {exc.user_message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
