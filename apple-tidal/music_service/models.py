"""Phase 1 — common, service-independent domain model.

These types are deliberately free of any Apple/TIDAL specifics. Adapters map
provider JSON into these models so the rest of the system (matching,
recommendation, tools) never sees a raw API payload.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Canonical service identifiers used everywhere (tool inputs, DB rows, models).
Service = Literal["apple_music", "tidal"]


class Track(BaseModel):
    """A single track as exposed by one concrete service."""

    id: str = Field(..., description="Provider-native track id (library or catalog).")
    service: Service
    title: str
    artist: str
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    isrc: Optional[str] = Field(
        default=None,
        description="International Standard Recording Code, when the service exposes it.",
    )
    explicit: Optional[bool] = None
    url: Optional[str] = None


class Playlist(BaseModel):
    """Playlist metadata without its tracks."""

    id: str
    service: Service
    name: str
    description: Optional[str] = None
    track_count: Optional[int] = None


class PlaylistDetails(BaseModel):
    """A playlist together with its (ordered) tracks."""

    playlist: Playlist
    tracks: list[Track] = Field(default_factory=list)


class TrackCandidate(BaseModel):
    """A recommendation/sync candidate that may resolve to several services.

    ``canonical_id`` is a stable, service-independent identity (typically derived
    from ISRC or from normalized artist+title). The per-service ids are filled in
    by the TrackMatchingService once a concrete match is found.
    """

    canonical_id: str
    title: str
    artist: str
    album: Optional[str] = None
    isrc: Optional[str] = None
    apple_music_id: Optional[str] = None
    tidal_id: Optional[str] = None
    reason: Optional[str] = Field(
        default=None, description="Short human-facing rationale for the recommendation."
    )


class PlaylistDraft(BaseModel):
    """A previewed, not-yet-created playlist (output of music_preview_playlist)."""

    name: str
    description: Optional[str] = None
    tracks: list[TrackCandidate] = Field(default_factory=list)
