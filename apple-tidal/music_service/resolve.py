"""Resolve free-text track candidates into a provider playlist draft.

This is the deterministic half of the agent-driven recommendation flow: the LLM
agent (Hermes) does the "smart" discovery (mood reasoning + web/reddit research)
and produces a list of "Artist - Title" candidates; this module finds each one on
the target service (search + TrackMatchingService), drops duplicates and
already-saved tracks, and returns a ``PlaylistDraft`` ready for confirmation.

Version safety is preserved: if only a live/acoustic/remix/remaster of a
requested track exists, it is reported unmatched rather than silently swapped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

from .adapter import MusicServiceAdapter
from .errors import MusicServiceError
from .matching import TrackMatchingService, canonical_key
from .models import Service, Track, TrackCandidate

_SEPARATORS = (" - ", " – ", " — ", " — ", "\t", " | ", " by ")


def parse_item(item: Union[str, dict]) -> tuple[str, str, Optional[str], Optional[str]]:
    """Return (artist, title, isrc, reason) from a string or dict candidate."""
    if isinstance(item, dict):
        return (
            (item.get("artist") or "").strip(),
            (item.get("title") or "").strip(),
            item.get("isrc"),
            item.get("reason"),
        )
    text = str(item).strip()
    for sep in _SEPARATORS:
        if sep in text:
            left, right = text.split(sep, 1)
            return left.strip(), right.strip(), None, None
    # No separator: treat the whole thing as the title.
    return "", text, None, None


@dataclass
class ResolveReport:
    service: Service
    matched: list[dict] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)
    excluded: int = 0

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "counts": {
                "matched": len(self.matched),
                "unmatched": len(self.unmatched),
                "excluded": self.excluded,
            },
            "matched": self.matched,
            "unmatched": self.unmatched,
        }


class CandidateResolver:
    def __init__(
        self,
        adapters: dict[str, MusicServiceAdapter],
        *,
        matcher: Optional[TrackMatchingService] = None,
        search_limit: int = 10,
    ) -> None:
        self.adapters = adapters
        self.matcher = matcher or TrackMatchingService()
        self.search_limit = search_limit

    async def resolve(
        self,
        items: list[Union[str, dict]],
        service: Service,
        *,
        exclude_existing: bool = False,
        max_items: Optional[int] = None,
    ) -> tuple[list[TrackCandidate], ResolveReport]:
        adapter = self.adapters[service]
        await adapter.authenticate()

        known: set[str] = set()
        if exclude_existing:
            try:
                lib = await adapter.get_library_tracks(limit=None)
                known = {canonical_key(t) for t in lib}
            except MusicServiceError:
                pass

        report = ResolveReport(service=service)
        candidates: list[TrackCandidate] = []
        seen_keys: set[str] = set()
        for item in (items[:max_items] if max_items else items):
            artist, title, isrc, reason = parse_item(item)
            if not title:
                continue
            source = Track(id="", service=service, title=title, artist=artist, isrc=isrc)
            query = f"{artist} {title}".strip()
            try:
                results = await adapter.search_tracks(query, limit=self.search_limit)
            except MusicServiceError as exc:
                report.unmatched.append({"artist": artist, "title": title, "reason": exc.user_message})
                continue
            result = self.matcher.match_track(source, results)
            if not (result.matched and result.track and not result.ambiguous):
                report.unmatched.append({"artist": artist, "title": title, "reason": result.match_method})
                continue
            key = canonical_key(result.track)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if exclude_existing and key in known:
                report.excluded += 1
                continue
            track = result.track
            candidate = TrackCandidate(
                canonical_id=key,
                title=track.title,
                artist=track.artist,
                album=track.album,
                isrc=track.isrc,
                apple_music_id=track.id if service == "apple_music" else None,
                tidal_id=track.id if service == "tidal" else None,
                reason=reason or f"resolved via {result.match_method}",
            )
            candidates.append(candidate)
            report.matched.append(candidate.model_dump())
        return candidates, report
