# Hermes Music Tools — Apple Music + TIDAL

A modular music integration for the **Hermes Agent**, exposed as an **MCP
(FastMCP) stdio server**. Hermes only ever sees thin MCP tools; all OAuth, HTTP,
matching, and (later) recommendation logic lives in a reusable `music_service`
package underneath.

> **Status.** Phase 1 (models) + Phase 2 (interface) + Apple Music (read **and
> write** — create playlist / add tracks; needs your MusicKit key set up) +
> **Phase 4 TIDAL** (official API v2: OAuth PKCE login, token persistence/refresh,
> read playlists/tracks, search, and write — create playlist / add tracks — gated
> by granted scopes). The `music_get_playlists` Hermes tool works for both
> services. Apple writes, SQLite, matching, and recommendations are later phases.

## Architecture

```
Hermes Agent
  └── MCP (stdio)  ── server.py  (FastMCP tools — thin wrappers)
        └── music_service/        (reusable, Hermes-independent library)
              ├── models.py        Track, Playlist, PlaylistDetails, ...
              ├── adapter.py       MusicServiceAdapter (common interface)
              ├── errors.py        typed, redaction-safe errors
              ├── registry.py      service id -> adapter
              ├── config.py        env/.env loading (no hardcoded secrets)
              ├── matching.py      TrackMatchingService: normalize, ISRC/fuzzy match, dedup
              ├── profile.py       MusicProfile + repository (MUSIC_PROFILE.md, per-request merge)
              ├── recommendation.py RecommendationService: deterministic recommend pipeline
              ├── sync.py          PlaylistSyncService: cross-service copy + match report
              ├── app.py           MusicToolsService: facade the MCP tools call
              ├── storage/         SQLite: tracks, snapshots, mappings, drafts, rec history/feedback
              ├── apple/           AppleMusicAdapter: auth, client, mapping
              ├── tidal/           TidalAdapter: OAuth PKCE, client, mapping
              └── auth_helper/     MusicKit JS authorize page + local server
```

Why a standalone MCP server (not code inside Hermes): it matches Hermes'
`optional-mcps` pattern (see the `n8n` manifest), keeps service logic out of the
agent, and is independently unit-testable.

## Required developer accounts

| Service | Needed | Notes |
|---|---|---|
| Apple Music | **Apple Developer Program membership** ($99/yr) **+** active Apple Music subscription | The Developer Program issues the MusicKit `.p8` key, Team ID, and Key ID used to sign the *developer token* required on every API call. The subscription is needed to mint the *Music User Token* via MusicKit JS. |
| TIDAL | Registered TIDAL developer app (client id/secret) | OAuth2; wired in Phase 4. |

> ⚠️ With an Apple Music **subscription but no Developer Program membership**,
> the API cannot be called at all — the developer token (and therefore MusicKit
> JS) cannot be created. All code below is ready; it goes live the moment a valid
> `.p8` key + Team ID + Key ID are supplied.

## Setup

```bash
cd D:/work/hermes-tools/apple-tidal
python -m venv .venv
.venv/Scripts/activate        # Windows;  source .venv/bin/activate on POSIX
pip install -e ".[dev]"
cp .env.example .env          # then fill in APPLE_* values
```

## Apple Music authorization flow

1. **Developer token** — generated automatically from `APPLE_TEAM_ID`,
   `APPLE_KEY_ID`, and the `.p8` at `APPLE_PRIVATE_KEY_PATH` (ES256 JWT, cached
   and auto-refreshed). Nothing to do manually.
2. **Music User Token** — must be obtained in a browser:
   ```bash
   python -m music_service.cli auth apple
   ```
   This serves a local MusicKit JS page, signs you into Apple Music, and saves
   the token to `~/.hermes/music_tools/apple_tokens.json`. (Or paste a token into
   `APPLE_MUSIC_USER_TOKEN`.)

## TIDAL authorization flow (official API v2)

Uses OAuth2 **Authorization Code + PKCE** against the official endpoints
(`login.tidal.com` / `auth.tidal.com`), API base `https://openapi.tidal.com/v2`.
No unofficial/private APIs are used.

1. Register an app at the TIDAL developer portal and note the **client id**,
   **client secret**, and a **redirect URI**. Grant it the scopes you need —
   for writes you must enable `playlists.write` (and `collection.write`).
2. Put `TIDAL_CLIENT_ID`, `TIDAL_CLIENT_SECRET`, `TIDAL_REDIRECT_URI`,
   `TIDAL_COUNTRY` in `.env`. **`TIDAL_REDIRECT_URI` must exactly match** a
   redirect registered for the app (default `http://127.0.0.1:8765/callback`).
3. Log in:
   ```bash
   python -m music_service.cli auth tidal
   ```
   A local callback server captures the redirect, exchanges the code for tokens,
   and saves them to `~/.hermes/music_tools/tidal_tokens.json`. It prints the
   **granted scopes** and warns if `playlists.write` was not granted. Access
   tokens auto-refresh using the stored refresh token.

### TIDAL capabilities & limitations
- Writes are **runtime-gated**: if the granted token lacks `playlists.write`,
  create/add raise `CapabilityNotSupportedError` ("Playlist creation is not
  available for this TIDAL application") instead of failing opaquely.
- The v2 playlist resource has **no description field** — descriptions can't be
  set or read on TIDAL playlists.
- Adding tracks is chunked to TIDAL's **50-items-per-request** limit.
- New playlists are created as `UNLISTED` (effectively private) for safety.
- **Rate limits:** TIDAL's dev API is throttled aggressively. The client retries
  `429`s with bounded backoff (honoring `Retry-After`) and **caps pagination**
  (search/library stop at the requested `limit`) so reads don't burst into a
  429. If a 429 still surfaces, it's a clean `RateLimitError` — retry shortly.

## Running locally (debug CLI)

```bash
# Apple
python -m music_service.cli playlists apple
python -m music_service.cli search apple "Adam Naas"
# TIDAL
python -m music_service.cli auth tidal
python -m music_service.cli playlists tidal
python -m music_service.cli search tidal "London Grammar"
# Write POC (creates "Hermes Integration Test" + adds one track):
python -m music_service.cli create-test-playlist tidal
```

## Registering the tool in Hermes

Add to `~/.hermes/config.yaml` (on Windows: `%LOCALAPPDATA%\hermes\config.yaml`):

```yaml
mcp_servers:
  music:
    transport: stdio
    command: python
    args: ["D:/work/hermes-tools/apple-tidal/server.py"]
```

Then restart Hermes. A full catalog manifest is in `hermes-manifest.yaml`.

### Available MCP tools

Read-only (no confirmation):
- `music_get_accounts_status` — auth/capabilities per service
- `music_get_playlists` `{service}`
- `music_get_playlist_tracks` `{service, playlist_id}`
- `music_search_tracks` `{service, query, limit}`
- `music_recommend_tracks` `{prompt, services, limit, exclude_existing, reference_artists, allow_versions}` → returns a `draft_id` (deterministic, profile-seeded)
- `music_get_seed_artists` `{mood}` → your artists tagged with that mood (profile tags + SQLite cache)
- `music_save_artist_moods` `{tags}` → persist `{"Woodkid": ["energy"], …}` into the mood cache
- `music_resolve_tracks` `{items, service, name, exclude_existing}` → resolve a list of `"Artist - Title"` candidates into a `draft_id` (search + version-safe matching + dedup + exclude-known)
- `music_preview_playlist` `{draft_id}` — re-show a draft; never modifies services
- `music_record_feedback` `{feedback, canonical_id|service+track_id, note}`

#### Smart, agent-driven recommendations (web/Reddit research)

The built-in `music_recommend_tracks` is deterministic and only seeds from your
profile artists — it does **not** understand moods like "energy" semantically. For
genuinely smart, on-theme discovery, let the **agent** do the reasoning and use the
tools for the deterministic part:

1. `music_get_seed_artists("energy")` — get your mood-matching artists (tag more via
   `music_save_artist_moods` if sparse).
2. The agent researches the web/Reddit/specialist lists (its own WebSearch/WebFetch)
   for on-theme tracks, producing a list of `"Artist - Title"`.
3. `music_resolve_tracks(items=[…], service="apple_music", exclude_existing=true)` →
   finds them on the service, returns a `draft_id` + unmatched report.
4. `music_create_playlist(draft_id, ["apple_music"], confirmed=true)`.

This keeps the "intelligence" in the LLM (no scraping/keys in the server) and the
deterministic resolution/matching/creation in the tools.

A Hermes **skill** encodes this recipe so the agent follows it automatically for
mood/theme requests: source in `hermes-skill/music-discovery/SKILL.md`, installed
to `~/.hermes/skills/music-discovery/SKILL.md`. Restart Hermes to load it.

Writes — **require `confirmed: true`** and must be preceded by a preview:
- `music_create_playlist` `{draft_id, services, confirmed}` — rejected unless `confirmed=true`
- `music_add_tracks` `{service, playlist_id, track_ids, confirmed}`
- `music_sync_playlist` `{source_service, playlist_id, destination_services, confirmed, name}` — without `confirmed` it returns a dry-run report (matched / ambiguous / substituted / unmatched, nothing omitted); with `confirmed=true` it also creates the destination playlist and adds matched tracks.
- `music_sync_favorites` `{direction, confirmed, max_per_direction}` — keep favorites in sync across services (TIDAL **My Collection** ↔ Apple Music **library songs**). Union only (adds what's missing on each side, never deletes). `direction` = `both` (default) / `to_apple` / `to_tidal`. Dry-run unless `confirmed=true`; caps transfers per run for rate-limit safety.

Typical flow: `music_recommend_tracks` → review `draft_id` via `music_preview_playlist` → `music_create_playlist` with `confirmed=true`.

**Performance / caching.** Sync cost is ~one destination search per source track,
sequential and rate-limit-bound (TIDAL is the bottleneck). Resolved Apple↔TIDAL
matches are cached in the SQLite `track_service_mapping` table, so **repeat syncs
skip the search for already-resolved tracks and run nearly instantly** — only new
tracks are searched. `music_sync_favorites` also caps transfers per run for
rate-limit safety (run again to continue).

## Testing

```bash
pytest            # all unit tests are offline (no network, no real credentials)
```

Phase 6 matching (`music_service/matching.py`): ISRC > normalized(+album/version)
> fuzzy priority, with **version safety** — a live/acoustic/remix/remaster
candidate is never auto-substituted for the original (returned flagged
`version_mismatch`, `matched=False`); the original is preferred when both exist.
Also provides `deduplicate()` / `canonical_key()`.

Phase 5 storage (`music_service/storage/`): SQLite schema (service_accounts,
tracks, playlist_snapshots, playlist_tracks, track_service_mapping,
recommendation_history, recommendation_feedback) + `MusicRepository`. No tokens
or secrets are stored in the DB — only non-secret account metadata.

Phase 7 profile (`MUSIC_PROFILE.md` + `music_service/profile.py`): user-editable
preferences (vocals/sound/avoid + reference_artists) parsed from a fenced yaml
block; `MusicProfileRepository` loads/saves/bootstraps the user copy and merges
per-request instructions over the baseline (`load_effective`).

Phase 8 recommendations (`music_service/recommendation.py`): a deterministic
(no-ML, no-LLM) pipeline — parse request → load effective profile → seed search
queries from reference artists → search → dedup → exclude library/disliked/
already-recommended → rank (reference artists first, avoid-terms down) → attach
reasons → return a `PlaylistDraft` preview. It only reads; nothing is created
until the (Phase 9) confirmation step. Recommendations are recorded to storage so
they aren't repeated.

Phase 9 (`app.py`, `sync.py`, `server.py`): all MCP tools as thin facade wrappers;
draft persistence (preview→create); `PlaylistSyncService` with a full match report
(matched/ambiguous/substituted/unmatched — nothing omitted); confirmation enforced
on every write (`ConfirmationRequiredError` unless `confirmed=true`); sync dry-run.

Covered (115 tests): model validation, error hierarchy & redaction, capability
enforcement (unsupported writes raise, never return empty), Apple developer-token
JWT shape + caching/refresh, Music-User-Token store round-trip, Apple JSON→model
mapping; TIDAL PKCE (S256), authorize-URL building, token parsing/refresh-token
retention/expiry, token store round-trip, ISO-8601 duration parsing, JSON:API
track/playlist mapping, write-scope gating, add-tracks 50-item chunking, 429
retry/backoff & bounded pagination (mocked transport); and Phase 6 normalization,
version detection, duration tolerance, ISRC/normalized/fuzzy matching, version
non-substitution, ambiguity, mapping output, and dedup. Integration tests against
real accounts are deferred and will be opt-in via env vars. Phase 5 adds
schema-version, no-secret-columns, track/snapshot/mapping round-trips, mapping
COALESCE merge, recommendation history exclusion, feedback validation, draft
persistence, accounts status, recommend→preview, create-with-confirmation +
candidate resolution, add-tracks confirmation, sync dry-run vs confirmed, and
tool registration.

## Safety

- Read-only in this deliverable; write methods raise `CapabilityNotSupportedError`.
- When writes are enabled, tests/POCs use a clearly named **`Hermes Integration
  Test`** playlist and never modify existing playlists.

## Credential security

- Secrets come from env / `.env` / `~/.hermes/.env` only — never hardcoded.
- The `.p8` private key is read from a path and **never logged**; HTTP logging
  records method/path/status only, never the `Authorization` / `Music-User-Token`
  headers.
- The Music User Token is stored in a local JSON file (chmod 600 on POSIX), not
  in source control (`.gitignore`d).

## Known limitations (so far)

- TIDAL playlists have no description (v2 API); 50 tracks max per add call;
  writes require the app to hold `playlists.write`.
- Apple library tracks don't expose ISRC (only catalog/search results do) — this
  matters for the Phase 6 matching service.
- Apple library playlists don't return a reliable track count (left `null` until
  the playlist is opened).

## How to remove test playlists

TIDAL (write support is live):
```bash
python -m music_service.cli cleanup-test-playlists tidal   # deletes all "Hermes Integration Test"
python -m music_service.cli delete-playlist tidal <id>     # delete a specific playlist
```
Apple Music: once Apple write support lands, the same commands will work for
`apple`; until then, delete `Hermes Integration Test` from the Apple Music app.
```
