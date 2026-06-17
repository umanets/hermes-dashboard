"""Phase 9 — application facade behind the MCP tools.

``MusicToolsService`` composes adapters + storage + recommendation + sync +
matching into high-level operations that the FastMCP tools call directly. Tools
stay thin: build args, call a method here, return its dict.

Dependencies are injectable (adapter_factory, storage, profile_repo) so the whole
surface is unit-testable offline without real credentials.
"""

from __future__ import annotations

import uuid
from typing import Callable, Optional

from .adapter import MusicServiceAdapter
from .errors import (
    ConfirmationRequiredError,
    MusicServiceError,
    TrackNotFoundError,
)
from .matching import TrackMatchingService, canonical_key
from .models import Service, Track, TrackCandidate
from .profile import MusicProfileRepository
from .favorites_sync import FavoritesSyncService
from .models import PlaylistDraft
from .recommendation import RecommendationRequest, RecommendationService
from .registry import get_adapter as _default_get_adapter
from .resolve import CandidateResolver
from .sync import PlaylistSyncService

ALL_SERVICES: tuple[Service, ...] = ("apple_music", "tidal")


class MusicToolsService:
    def __init__(
        self,
        *,
        adapter_factory: Callable[[str], MusicServiceAdapter] = _default_get_adapter,
        storage=None,
        profile_repo: Optional[MusicProfileRepository] = None,
        matcher: Optional[TrackMatchingService] = None,
    ) -> None:
        self._adapter_factory = adapter_factory
        self._storage = storage
        self._profile_repo = profile_repo or MusicProfileRepository()
        self._matcher = matcher or TrackMatchingService()
        self._adapter_cache: dict[str, MusicServiceAdapter] = {}

    # ---- shared helpers ---------------------------------------------------

    def storage(self):
        if self._storage is None:
            from .storage import MusicRepository

            self._storage = MusicRepository()
        return self._storage

    def adapter(self, service: str) -> MusicServiceAdapter:
        if service not in self._adapter_cache:
            self._adapter_cache[service] = self._adapter_factory(service)
        return self._adapter_cache[service]

    def _adapters(self, services) -> dict[str, MusicServiceAdapter]:
        return {s: self.adapter(s) for s in services}

    # ---- read operations --------------------------------------------------

    async def accounts_status(self, services=ALL_SERVICES) -> dict:
        out = {}
        for service in services:
            try:
                adapter = self.adapter(service)
            except MusicServiceError as exc:
                out[service] = {"status": "unavailable", "message": exc.user_message}
                continue
            entry = {"capabilities": adapter.capabilities}
            try:
                await adapter.authenticate()
                entry["status"] = "connected"
            except MusicServiceError as exc:
                entry["status"] = "auth_required"
                entry["message"] = exc.user_message
            out[service] = entry
        return {"accounts": out}

    async def get_playlists(self, service: str) -> dict:
        adapter = self.adapter(service)
        await adapter.authenticate()
        playlists = await adapter.get_playlists()
        return {"service": service, "count": len(playlists), "playlists": [p.model_dump() for p in playlists]}

    async def get_playlist_tracks(self, service: str, playlist_id: str) -> dict:
        adapter = self.adapter(service)
        await adapter.authenticate()
        details = await adapter.get_playlist(playlist_id)
        # cache the snapshot for later diffing/recommendation exclusion
        try:
            self.storage().save_playlist_snapshot(details)
        except Exception:
            pass
        return {
            "service": service,
            "playlist": details.playlist.model_dump(),
            "tracks": [t.model_dump() for t in details.tracks],
        }

    async def search_tracks(self, service: str, query: str, limit: int = 20) -> dict:
        adapter = self.adapter(service)
        await adapter.authenticate()
        tracks = await adapter.search_tracks(query, limit=limit)
        return {"service": service, "count": len(tracks), "tracks": [t.model_dump() for t in tracks]}

    async def get_library_artists(self, service: str, limit: int = 200) -> dict:
        """Distinct artists in the user's library, by track count.

        Used to build the mood base: the agent reads this list, tags artists with
        moods (LLM), and persists them via save_artist_moods so future mood
        requests have real seed coverage.
        """
        adapter = self.adapter(service)
        await adapter.authenticate()
        tracks = await adapter.get_library_tracks(limit=None)
        counts: dict[str, int] = {}
        display: dict[str, str] = {}
        for t in tracks:
            key = (t.artist or "").casefold().strip()
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            display.setdefault(key, t.artist)
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        artists = [{"artist": display[k], "tracks": c} for k, c in ordered[:limit]]
        return {"service": service, "distinct_artists": len(counts), "artists": artists}

    # ---- recommendation + preview ----------------------------------------

    async def recommend(
        self,
        prompt: str,
        *,
        services=("tidal",),
        limit: int = 25,
        exclude_existing: bool = True,
        reference_artists=None,
        allow_versions: bool = False,
    ) -> dict:
        rec = RecommendationService(
            self._adapters(services), profile_repo=self._profile_repo, storage=self.storage()
        )
        request = RecommendationRequest(
            prompt=prompt,
            services=list(services),
            limit=limit,
            exclude_existing=exclude_existing,
            reference_artists=list(reference_artists or []),
            allow_versions=allow_versions,
        )
        result = await rec.recommend(request)
        self.storage().save_draft(result.request_id, result.draft)
        data = result.to_dict()
        data["draft_id"] = result.request_id
        return data

    async def resolve_tracks(
        self,
        items: list,
        service: str,
        *,
        name: Optional[str] = None,
        exclude_existing: bool = True,
    ) -> dict:
        """Resolve free-text 'Artist - Title' candidates into a provider draft.

        This is the deterministic landing point for the agent's web/mood research:
        it finds each candidate on ``service`` and returns a draft_id + report.
        """
        resolver = CandidateResolver(self._adapters((service,)), matcher=self._matcher)
        candidates, report = await resolver.resolve(
            items, service, exclude_existing=exclude_existing
        )
        draft_id = uuid.uuid4().hex
        draft = PlaylistDraft(
            name=name or "Hermes Resolved Playlist",
            description=f"Resolved {len(candidates)} of {len(items)} candidates on {service}",
            tracks=candidates,
        )
        self.storage().save_draft(draft_id, draft)
        return {
            "draft_id": draft_id,
            "name": draft.name,
            "service": service,
            "tracks": [c.model_dump() for c in candidates],
            "unmatched_tracks": report.unmatched,
            "excluded": report.excluded,
        }

    def get_seed_artists(self, mood: str) -> dict:
        """Union of profile-tagged + cached artists matching a mood (hybrid base)."""
        profile = self._profile_repo.load()
        from_profile = profile.artists_for_mood(mood)
        from_cache = self.storage().get_artists_for_mood(mood)
        seen, artists = set(), []
        for a in [*from_profile, *from_cache]:
            if a.casefold() not in seen:
                seen.add(a.casefold())
                artists.append(a)
        return {"mood": mood, "artists": artists, "from_profile": from_profile, "from_cache": from_cache}

    def save_artist_moods(self, tags: dict, *, source: str = "agent") -> dict:
        """Persist agent/LLM-derived artist→moods into the SQLite mood cache."""
        saved = 0
        for artist, moods in (tags or {}).items():
            if isinstance(moods, str):
                moods = [moods]
            self.storage().upsert_artist_moods(artist, list(moods), source=source)
            saved += 1
        return {"saved": saved}

    def preview_playlist(self, draft_id: str) -> dict:
        draft = self.storage().get_draft(draft_id)
        if draft is None:
            raise TrackNotFoundError(user_message=f"No draft found with id {draft_id}.")
        return {
            "draft_id": draft_id,
            "name": draft.name,
            "description": draft.description,
            "tracks": [c.model_dump() for c in draft.tracks],
            "warnings": [],
            "unmatched_tracks": [],
        }

    # ---- writes (confirmation required) ----------------------------------

    async def create_playlist(self, draft_id: str, *, services, confirmed: bool = False) -> dict:
        if confirmed is not True:
            raise ConfirmationRequiredError(
                user_message="Refusing to create a playlist without confirmed=true."
            )
        draft = self.storage().get_draft(draft_id)
        if draft is None:
            raise TrackNotFoundError(user_message=f"No draft found with id {draft_id}.")

        results = {}
        for service in services:
            adapter = self.adapter(service)
            await adapter.authenticate()
            playlist = await adapter.create_playlist(draft.name, description=draft.description)
            ids, unmatched = await self._resolve_candidates(service, adapter, draft.tracks)
            if ids:
                await adapter.add_tracks(playlist.id, ids)
            results[service] = {
                "playlist_id": playlist.id,
                "added": len(ids),
                "unmatched": unmatched,
            }
        return {"draft_id": draft_id, "created": results}

    async def add_tracks(
        self, service: str, playlist_id: str, track_ids: list[str], *, confirmed: bool = False
    ) -> dict:
        if confirmed is not True:
            raise ConfirmationRequiredError(
                user_message="Refusing to add tracks without confirmed=true."
            )
        adapter = self.adapter(service)
        await adapter.authenticate()
        await adapter.add_tracks(playlist_id, track_ids)
        return {"service": service, "playlist_id": playlist_id, "added": len(track_ids)}

    async def sync_playlist(
        self,
        source_service: str,
        playlist_id: str,
        destination_services,
        *,
        confirmed: bool = False,
        name: Optional[str] = None,
    ) -> dict:
        services = {source_service, *destination_services}
        syncer = PlaylistSyncService(
            self._adapters(services), matcher=self._matcher, storage=self.storage()
        )
        reports = []
        for dest in destination_services:
            report = await syncer.sync(
                source_service, playlist_id, dest, confirmed=confirmed, name=name
            )
            reports.append(report.to_dict())
        return {"confirmed": confirmed, "syncs": reports}

    async def sync_favorites(
        self,
        *,
        direction: str = "both",
        confirmed: bool = False,
        max_per_direction: int = 50,
    ) -> dict:
        syncer = FavoritesSyncService(
            self._adapters(("apple_music", "tidal")),
            max_per_direction=max_per_direction,
            storage=self.storage(),
        )
        report = await syncer.sync(direction=direction, confirmed=confirmed)
        return report.to_dict()

    # ---- feedback ---------------------------------------------------------

    def record_feedback(
        self,
        feedback: str,
        *,
        canonical_id: Optional[str] = None,
        service: Optional[str] = None,
        track_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> dict:
        try:
            self.storage().record_feedback(
                feedback=feedback,
                canonical_track_id=canonical_id,
                service=service,
                service_track_id=track_id,
                note=note,
            )
        except ValueError as exc:
            raise MusicServiceError(user_message=str(exc)) from exc
        return {"recorded": True, "feedback": feedback}

    # ---- internals --------------------------------------------------------

    async def _resolve_candidates(
        self, service: str, adapter: MusicServiceAdapter, candidates: list[TrackCandidate]
    ) -> tuple[list[str], list[dict]]:
        """Resolve each draft candidate to a concrete track id on ``service``."""
        ids: list[str] = []
        unmatched: list[dict] = []
        for cand in candidates:
            direct = cand.apple_music_id if service == "apple_music" else cand.tidal_id
            if direct:
                ids.append(direct)
                continue
            # Need to find it on this service via search + matching.
            source = Track(
                id="",
                service=service,  # type: ignore[arg-type]
                title=cand.title,
                artist=cand.artist,
                album=cand.album,
                isrc=cand.isrc,
            )
            try:
                results = await adapter.search_tracks(f"{cand.artist} {cand.title}", limit=10)
            except MusicServiceError:
                results = []
            match = self._matcher.match_track(source, results)
            if match.matched and match.track and not match.ambiguous:
                ids.append(match.track.id)
            else:
                unmatched.append(
                    {"title": cand.title, "artist": cand.artist, "reason": match.match_method}
                )
        return ids, unmatched
