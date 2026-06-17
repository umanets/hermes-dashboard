# Apple Music — TODO (unblock after Developer Program approval)

Status (2026-06-14): membership **ACTIVE** — Team ID `4G28847YDE`
(exp 2027-06-14). Apple read **and write** code is now complete and wired
(create playlist + add tracks enabled and unit-tested). The only thing left is
the manual credential setup below — then it's live.

---

## 1. One-time account setup (manual — only you can do this)

1. Confirm Apple Developer Program membership is **approved**.
2. In *Certificates, Identifiers & Profiles*:
   - Create a **MusicKit** identifier.
   - Create a **private key** with MusicKit enabled → download the `.p8` (you can
     download it only once). Note the **Key ID**.
   - Note your **Team ID** (top-right of the developer portal).
3. Put the values in `.env` (project root) — never commit the `.p8`:
   ```env
   APPLE_TEAM_ID=4G28847YDE
   APPLE_KEY_ID=YYYYYYYYYY
   APPLE_PRIVATE_KEY_PATH=C:/secure/AuthKey_YYYYYYYYYY.p8
   APPLE_MUSIC_STOREFRONT=ua        # or your storefront
   ```
4. Get a **Music User Token** (needs an active Apple Music subscription):
   ```bash
   python -m music_service.cli auth apple
   ```
   Authorize in the browser; the token is saved to
   `~/.hermes/music_tools/apple_tokens.json`.
5. Smoke-test reads:
   ```bash
   python -m music_service.cli playlists apple
   python -m music_service.cli search apple "Adam Naas"
   ```

---

## 2. Code tasks to enable Apple writes — ✅ DONE

- [x] `music_service/apple/client.py`: `_post` helper + `create_library_playlist`
      (`POST /v1/me/library/playlists`) and `add_playlist_tracks`
      (`POST /v1/me/library/playlists/{id}/tracks`); `_handle` now accepts 201/204.
- [x] `music_service/apple/adapter.py`: capabilities `create_playlist`/`add_tracks`
      = True; `create_playlist` / `add_tracks` implemented (catalog `type: songs`).
- [x] Tests: `tests/test_apple_write.py` (mocked transport — POST URL + body, 201/204).
- [ ] After credentials are set: `python -m music_service.cli create-test-playlist apple`.

---

## 3. Known Apple limitations to handle / document

- **No public delete endpoint** for library playlists or for removing tracks
  (as of last check). So:
  - `cleanup-test-playlists apple` / `delete-playlist apple` cannot work via API —
    make them raise a clear `CapabilityNotSupportedError` ("delete the
    'Hermes Integration Test' playlist manually in the Apple Music app").
  - `music_sync_playlist` into Apple can create + add but not replace/remove.
- Created playlists may take a few seconds to appear; description is not always
  honored by the API.
- Library tracks don't expose **ISRC** (catalog/search do) — matching from the
  Apple *library* relies on normalized artist+title; catalog/search matches can
  use ISRC. (Already handled by `TrackMatchingService`.)
- Storefront matters for catalog/search results and availability.

---

## 4. Definition of done

- [ ] `music_get_accounts_status` shows `apple_music: connected`.
- [ ] Read tools return real Apple playlists/tracks/search.
- [ ] `create_playlist` + `add_tracks` work; `create-test-playlist apple` creates
      "Hermes Integration Test" and adds one track.
- [ ] Apple↔TIDAL `music_sync_playlist` produces a correct match report and (with
      `confirmed=true`) creates the destination playlist.
- [ ] Delete/cleanup paths fail loudly with a manual-cleanup message.
- [ ] Update `README.md` (remove the "Apple writes pending" caveats) and delete
      this file.
