"""Phase 9 — PlaylistSyncService: copy/sync a playlist between services.

Reads the source playlist, resolves each track on the destination service via
search + TrackMatchingService, and reports the outcome for EVERY source track:

  * matched      — a confident, same-version match was found
  * ambiguous    — multiple near-equal candidates; needs manual choice
  * substituted  — only a different version (live/acoustic/remix/remaster) exists
  * unmatched    — nothing suitable found

Nothing is ever silently omitted. Writes (create + add) happen only when the
caller passes ``confirmed=True``; otherwise it returns a dry-run preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .adapter import MusicServiceAdapter
from .errors import MusicServiceError
from .mapping_cache import lookup_dest_id, store_mapping
from .matching import TrackMatchingService
from .models import Service, Track


@dataclass
class SyncReport:
    source_service: Service
    source_playlist_id: str
    destination_service: Service
    destination_playlist_id: Optional[str] = None
    created: bool = False
    matched: list[dict] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)
    substituted: list[dict] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def total_source(self) -> int:
        return len(self.matched) + len(self.ambiguous) + len(self.substituted) + len(self.unmatched)

    def to_dict(self) -> dict:
        return {
            "source_service": self.source_service,
            "source_playlist_id": self.source_playlist_id,
            "destination_service": self.destination_service,
            "destination_playlist_id": self.destination_playlist_id,
            "created": self.created,
            "counts": {
                "source_total": self.total_source(),
                "matched": len(self.matched),
                "ambiguous": len(self.ambiguous),
                "substituted": len(self.substituted),
                "unmatched": len(self.unmatched),
            },
            "matched": self.matched,
            "ambiguous": self.ambiguous,
            "substituted": self.substituted,
            "unmatched": self.unmatched,
            "warnings": self.warnings,
        }


def _src(track: Track) -> dict:
    return {
        "id": track.id,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "isrc": track.isrc,
    }


class PlaylistSyncService:
    def __init__(
        self,
        adapters: dict[str, MusicServiceAdapter],
        *,
        matcher: Optional[TrackMatchingService] = None,
        search_limit: int = 10,
        storage=None,
    ) -> None:
        self.adapters = adapters
        self.matcher = matcher or TrackMatchingService()
        self.search_limit = search_limit
        self.storage = storage

    async def plan(
        self,
        source_service: Service,
        playlist_id: str,
        destination_service: Service,
    ) -> tuple[SyncReport, list[str]]:
        """Build the match report (no writes). Returns (report, matched_dest_ids)."""
        report = SyncReport(source_service, playlist_id, destination_service)
        src_adapter = self.adapters[source_service]
        dst_adapter = self.adapters[destination_service]
        await src_adapter.authenticate()
        await dst_adapter.authenticate()

        details = await src_adapter.get_playlist(playlist_id)
        matched_ids: list[str] = []
        for track in details.tracks:
            # Fast path: a previously resolved match from the cache.
            cached = lookup_dest_id(self.storage, track, destination_service)
            if cached:
                matched_ids.append(cached)
                report.matched.append(
                    {**_src(track), "destination_track_id": cached, "match_method": "cache", "confidence": 1.0}
                )
                continue
            query = f"{track.artist} {track.title}".strip()
            try:
                candidates = await dst_adapter.search_tracks(query, limit=self.search_limit)
            except MusicServiceError as exc:
                report.unmatched.append({**_src(track), "error": exc.user_message})
                continue
            result = self.matcher.match_track(track, candidates)
            entry = {
                **_src(track),
                "destination_track_id": result.track.id if result.track else None,
                "match_method": result.match_method,
                "confidence": round(result.confidence, 3),
            }
            if result.matched and not result.ambiguous:
                matched_ids.append(result.track.id)
                report.matched.append(entry)
                store_mapping(self.storage, track, result.track, method=result.match_method, confidence=result.confidence)
            elif result.version_mismatch and result.track is not None:
                entry["note"] = result.notes
                report.substituted.append(entry)
            elif result.ambiguous:
                report.ambiguous.append(entry)
            else:
                report.unmatched.append(entry)
        return report, matched_ids

    async def sync(
        self,
        source_service: Service,
        playlist_id: str,
        destination_service: Service,
        *,
        confirmed: bool = False,
        name: Optional[str] = None,
    ) -> SyncReport:
        report, matched_ids = await self.plan(source_service, playlist_id, destination_service)

        if not confirmed:
            report.warnings.append(
                "Preview only — no playlist was created. Re-run with confirmed=true to write."
            )
            return report

        dst_adapter = self.adapters[destination_service]
        src_details = await self.adapters[source_service].get_playlist(playlist_id)
        dest_name = name or f"{src_details.playlist.name} (from {source_service})"
        playlist = await dst_adapter.create_playlist(
            dest_name, description=f"Synced from {source_service} by Hermes"
        )
        report.destination_playlist_id = playlist.id
        if matched_ids:
            await dst_adapter.add_tracks(playlist.id, matched_ids)
        report.created = True
        return report
