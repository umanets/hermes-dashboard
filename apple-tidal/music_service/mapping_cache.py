"""Cross-service id cache helpers (shared by the sync services).

Resolved Apple↔TIDAL matches are persisted in ``track_service_mapping`` so a
repeat sync can look up the destination id by the source's native id instead of
re-searching. ``storage`` is an optional ``MusicRepository`` — when None, these
are no-ops and sync simply runs uncached.
"""

from __future__ import annotations

from typing import Optional

from .matching import canonical_key, normalize_text
from .models import Service, Track


def _id_column(service: Service) -> str:
    return "apple_music_id" if service == "apple_music" else "tidal_id"


def lookup_dest_id(storage, source: Track, dest_service: Service) -> Optional[str]:
    """Return a cached destination id for ``source`` on ``dest_service``, if any."""
    if storage is None or not source.id:
        return None
    row = storage.find_mapping_by_service_id(source.service, source.id)
    if not row:
        return None
    return row.get(_id_column(dest_service))


def store_mapping(storage, source: Track, dest: Track, *, method: str, confidence: float) -> None:
    """Persist a confident source↔dest match for future fast lookups."""
    if storage is None or dest is None:
        return
    ids = {"apple_music_id": None, "tidal_id": None}
    for track in (source, dest):
        ids[_id_column(track.service)] = track.id
    mapping = {
        **ids,
        "isrc": source.isrc or dest.isrc,
        "normalized_artist": normalize_text(source.artist),
        "normalized_title": normalize_text(source.title),
        "duration_ms": source.duration_ms or dest.duration_ms,
        "match_method": method,
        "confidence": confidence,
    }
    storage.upsert_mapping(canonical_key(source), mapping)
