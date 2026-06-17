---
name: music-management
description: "Manage Apple Music & TIDAL via the `music` MCP tools: build mood/theme playlists (e.g. \"energy playlist\", \"chill mix\", today's-date mood) with real web discovery, plus sync playlists/favorites, search, and library tools. For ANY mood/theme/vibe playlist request use the Mood discovery recipe below — NOT music_recommend_tracks."
---

# Music management (Apple Music + TIDAL)

Apple Music = `apple_music`, TIDAL = `tidal`. Always start by confirming
connectivity with `mcp_music_music_get_accounts_status` and use a `connected`
service. Writes (`create_playlist`, `add_tracks`, `sync_*`) require
`confirmed=true` and must follow a preview + explicit user "yes".

> Source of truth for the installed skill at
> `%LOCALAPPDATA%\hermes\skills\media\music-management\SKILL.md`. Keep them in sync.
> (NOTE: Hermes loads skills from `%LOCALAPPDATA%\hermes\skills`, organized by
> category dirs — NOT from `~/.hermes/skills`.)

---

## Mood / theme playlist creation  ← use this for "energy/chill/workout/date" requests

**HARD RULES**
- **Do NOT use `music_recommend_tracks`** for a mood/theme/vibe. It is
  deterministic, seeds only from the user's profile artists, and collapses onto a
  single artist (off-theme). It is the wrong tool here.
- **Web discovery is mandatory.** The user's library is small and skews to their
  usual taste; moods like "energy" are mostly NOT in it — find NEW artists online.
- **Diversity:** many distinct artists (≤2 tracks per artist). Never fill from one
  artist's catalog.

**Recipe**
1. `mcp_music_music_get_accounts_status` → pick the `connected` target service.
2. `mcp_music_music_get_seed_artists(mood)`. If it returns **< ~5** artists, build
   the base first (step 3); else use them as seeds and go to step 4.
3. **Build the mood base (one-time, persists):**
   - `mcp_music_music_get_library_artists(service)` → the user's artists.
   - Decide each artist's moods and persist in ONE batch:
     `mcp_music_music_save_artist_moods({"Artist A": ["energy"], "Artist B": ["chill","focus"], ...})`.
   - Re-call `get_seed_artists(mood)`.
4. **Discover on the web (ALWAYS):** use `WebSearch` / `WebFetch` — editorial
   "best <mood>/<genre> songs" lists, Reddit (r/musicsuggestions, r/listentothis),
   "artists similar to <seed>". Blend a few seed-artist tracks with MANY new
   artists. Produce a de-duplicated list of `"Artist - Title"` strings, ~1.5–2× the
   requested size, diverse across artists.
5. **Resolve:** `mcp_music_music_resolve_tracks(items=[...], service="<service>",
   name="<playlist name>", exclude_existing=true)` → `draft_id` + `unmatched_tracks`.
6. **Preview:** report name, matched count, artist spread, a few unmatched. Don't
   create yet.
7. **Create on confirmation:** `mcp_music_music_create_playlist(draft_id,
   ["<service>"], confirmed=true)`.

Notes: resolver is version-safe (live/remix/remaster of a requested track → reported
unmatched, never swapped). Date-named playlist → use the real current date as
`name` (rename later only in the app; tools can't rename).

---

## Playlist sync (copy between services)
1. `mcp_music_music_get_playlists(service=...)` on each side; present counts.
2. Dry-run: `mcp_music_music_sync_playlist(source_service, playlist_id,
   destination_services=[...])` (omit `confirmed`) → review matched / ambiguous /
   substituted / unmatched.
3. Confirm with `confirmed=true`.
- Large playlists (hundreds of tracks) can exceed the 120s MCP timeout — for those
  prefer the CLI (`python -m music_service.cli sync-favorites …`) or smaller scopes.
  The mapping cache makes repeat syncs fast.

## Favorites / library sync (Apple Library Songs ↔ TIDAL Collection Tracks)
- `mcp_music_music_sync_favorites(direction=both|to_apple|to_tidal)` — dry-run
  first (omit `confirmed`), then `confirmed=true`. Union only; never deletes.

## Other operations
- Search: `mcp_music_music_search_tracks(service, query)`.
- Playlist tracks: `mcp_music_music_get_playlist_tracks(service, playlist_id)`.
- Feedback: `mcp_music_music_record_feedback(feedback=liked|disliked|...)`.
- `mcp_music_music_recommend_tracks` — ONLY for "more of my usual artists", never
  for a mood/theme (see hard rules above).

## Account setup notes
- Apple Music needs `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY_PATH` (.p8)
  and a Music User Token (`music_service.cli auth apple`). If status is
  `auth_required`/`unavailable`, the user must supply these; re-check after.
