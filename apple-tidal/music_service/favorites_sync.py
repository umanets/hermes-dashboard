"""Favorites sync between Apple Music and TIDAL.

"Favorites" maps to each service's saved-tracks collection:
  * TIDAL  → My Collection (the heart/favorite tracks)
  * Apple  → My Library songs

By default this performs a **union** in both directions (add what's missing on
each side) and never deletes — safe and reversible. Matching uses
``TrackMatchingService`` (ISRC first, then normalized artist+title; version
variants like live/remaster are NOT treated as the same track).

To respect API rate limits, each direction transfers at most ``max_per_direction``
tracks per run (the rest are reported as ``deferred`` — run again to continue).
Writes happen only when ``confirmed=True``; otherwise it's a dry-run preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .adapter import MusicServiceAdapter
from .errors import MusicServiceError
from .mapping_cache import lookup_dest_id, store_mapping
from .matching import TrackMatchingService, core_title
from .models import Service, Track

DIRECTION_BOTH = "both"
DIRECTION_TO_APPLE = "to_apple"
DIRECTION_TO_TIDAL = "to_tidal"


def _brief(t: Track) -> dict:
    return {"id": t.id, "title": t.title, "artist": t.artist, "isrc": t.isrc}


def _index(tracks: list[Track]) -> tuple[dict, dict]:
    by_isrc: dict[str, list[Track]] = {}
    by_core: dict[str, list[Track]] = {}
    for t in tracks:
        if t.isrc:
            by_isrc.setdefault(t.isrc.upper(), []).append(t)
        by_core.setdefault(core_title(t.title), []).append(t)
    return by_isrc, by_core


def _pool(track: Track, by_isrc: dict, by_core: dict) -> list[Track]:
    pool: list[Track] = []
    seen: set[int] = set()
    groups = []
    if track.isrc and track.isrc.upper() in by_isrc:
        groups.append(by_isrc[track.isrc.upper()])
    groups.append(by_core.get(core_title(track.title), []))
    for group in groups:
        for cand in group:
            if id(cand) not in seen:
                seen.add(id(cand))
                pool.append(cand)
    return pool


@dataclass
class FavoritesSyncReport:
    direction: str
    confirmed: bool
    apple_total: int = 0
    tidal_total: int = 0
    already_in_sync: int = 0
    to_apple_matched: list[dict] = field(default_factory=list)   # resolved Apple catalog ids
    to_apple_unmatched: list[dict] = field(default_factory=list)
    to_tidal_matched: list[dict] = field(default_factory=list)
    to_tidal_unmatched: list[dict] = field(default_factory=list)
    added_to_apple: int = 0
    added_to_tidal: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "confirmed": self.confirmed,
            "apple_total": self.apple_total,
            "tidal_total": self.tidal_total,
            "already_in_sync": self.already_in_sync,
            "to_apple": {
                "to_add": len(self.to_apple_matched),
                "matched": self.to_apple_matched,
                "unmatched": self.to_apple_unmatched,
            },
            "to_tidal": {
                "to_add": len(self.to_tidal_matched),
                "matched": self.to_tidal_matched,
                "unmatched": self.to_tidal_unmatched,
            },
            "added_to_apple": self.added_to_apple,
            "added_to_tidal": self.added_to_tidal,
            "warnings": self.warnings,
        }


class FavoritesSyncService:
    def __init__(
        self,
        adapters: dict[str, MusicServiceAdapter],
        *,
        matcher: Optional[TrackMatchingService] = None,
        max_per_direction: int = 50,
        search_limit: int = 10,
        storage=None,
    ) -> None:
        self.adapters = adapters
        self.matcher = matcher or TrackMatchingService()
        self.max_per_direction = max_per_direction
        self.search_limit = search_limit
        self.storage = storage

    async def sync(
        self, *, direction: str = DIRECTION_BOTH, confirmed: bool = False
    ) -> FavoritesSyncReport:
        apple = self.adapters["apple_music"]
        tidal = self.adapters["tidal"]
        await apple.authenticate()
        await tidal.authenticate()

        apple_lib = await apple.get_library_tracks(limit=None)
        tidal_lib = await tidal.get_library_tracks(limit=None)
        report = FavoritesSyncReport(direction=direction, confirmed=confirmed)
        report.apple_total = len(apple_lib)
        report.tidal_total = len(tidal_lib)

        apple_isrc, apple_core = _index(apple_lib)
        tidal_isrc, tidal_core = _index(tidal_lib)

        in_sync = 0

        # TIDAL -> Apple: tidal favorites missing from the Apple library.
        if direction in (DIRECTION_BOTH, DIRECTION_TO_APPLE):
            missing = [t for t in tidal_lib if not self._present(t, apple_isrc, apple_core)]
            in_sync += len(tidal_lib) - len(missing)
            await self._resolve_into(
                missing, apple, report.to_apple_matched, report.to_apple_unmatched, report
            )

        # Apple -> TIDAL: apple library missing from tidal favorites.
        if direction in (DIRECTION_BOTH, DIRECTION_TO_TIDAL):
            missing = [t for t in apple_lib if not self._present(t, tidal_isrc, tidal_core)]
            await self._resolve_into(
                missing, tidal, report.to_tidal_matched, report.to_tidal_unmatched, report
            )

        report.already_in_sync = in_sync

        if not confirmed:
            report.warnings.append("Preview only — set confirmed=true to apply.")
            return report

        # Apply writes.
        apple_ids = [m["destination_id"] for m in report.to_apple_matched]
        tidal_ids = [m["destination_id"] for m in report.to_tidal_matched]
        if apple_ids:
            await apple.add_to_library(apple_ids)
            report.added_to_apple = len(apple_ids)
        if tidal_ids:
            await tidal.add_to_library(tidal_ids)
            report.added_to_tidal = len(tidal_ids)
        return report

    def _present(self, track: Track, by_isrc: dict, by_core: dict) -> bool:
        pool = _pool(track, by_isrc, by_core)
        if not pool:
            return False
        result = self.matcher.match_track(track, pool)
        return result.matched and not result.version_mismatch

    async def _resolve_into(
        self,
        missing: list[Track],
        dest_adapter: MusicServiceAdapter,
        matched_out: list[dict],
        unmatched_out: list[dict],
        report: FavoritesSyncReport,
    ) -> None:
        capped = missing[: self.max_per_direction]
        if len(missing) > len(capped):
            report.warnings.append(
                f"{len(missing) - len(capped)} track(s) toward {dest_adapter.service} "
                f"deferred (cap {self.max_per_direction}/run) — run again to continue."
            )
        dest_service = dest_adapter.service
        for track in capped:
            cached = lookup_dest_id(self.storage, track, dest_service)
            if cached:
                matched_out.append(
                    {"source": _brief(track), "destination_id": cached, "match_method": "cache", "confidence": 1.0}
                )
                continue
            try:
                candidates = await dest_adapter.search_tracks(
                    f"{track.artist} {track.title}", limit=self.search_limit
                )
            except MusicServiceError as exc:
                unmatched_out.append({**_brief(track), "reason": exc.user_message})
                continue
            result = self.matcher.match_track(track, candidates)
            if result.matched and result.track and not result.ambiguous:
                matched_out.append(
                    {
                        "source": _brief(track),
                        "destination_id": result.track.id,
                        "match_method": result.match_method,
                        "confidence": round(result.confidence, 3),
                    }
                )
                store_mapping(
                    self.storage, track, result.track,
                    method=result.match_method, confidence=result.confidence,
                )
            else:
                unmatched_out.append({**_brief(track), "reason": result.match_method})
