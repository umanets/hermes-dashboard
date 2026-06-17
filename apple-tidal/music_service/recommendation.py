"""Phase 8 — deterministic RecommendationService.

No ML and no LLM: a transparent, repeatable pipeline that turns a request +
the user's profile into a previewed playlist draft.

Pipeline (per the brief):
  1. read the request
  2. extract reference artists, size, exclusions, instructions
  3. load profile + library (+ optionally playlist tracks) + prior recs/feedback
  4. generate candidate search queries (seeded by reference artists)
  5. search the selected services
  6. deduplicate candidates
  7. exclude known / disliked / already-recommended tracks (when requested)
  8. rank candidates
  9. attach a short reason
 10. return a preview (PlaylistDraft) — creation happens later, after confirmation

This module never writes to external services. It only reads (search/library)
and may record the proposed recommendations to local storage so they are not
suggested again.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .adapter import MusicServiceAdapter
from .errors import MusicServiceError
from .matching import (
    canonical_key,
    deduplicate,
    detect_versions,
    normalize_text,
)
from .models import PlaylistDraft, Service, Track, TrackCandidate
from .profile import MusicProfile, MusicProfileRepository

_SIZE_RE = re.compile(r"(\d+)\s*(?:tracks?|songs?|треков|песен)", re.IGNORECASE)
_NO_VERSION_RE = re.compile(
    r"\bno\s+(?:live|acoustic|remix|remaster)|\boriginal(?:\s+version)?s?\s+only\b|"
    r"\bтолько\s+оригинал", re.IGNORECASE
)
_MAX_QUERIES = 12
_MAX_PER_QUERY = 25


@dataclass
class RecommendationRequest:
    prompt: str
    services: list[Service] = field(default_factory=lambda: ["tidal"])
    limit: int = 25
    exclude_existing: bool = True
    reference_artists: list[str] = field(default_factory=list)
    allow_versions: bool = False  # include live/acoustic/remix/remaster variants
    include_playlist_tracks: bool = False  # off by default to respect rate limits
    name: Optional[str] = None

    def resolved_limit(self) -> int:
        m = _SIZE_RE.search(self.prompt)
        return int(m.group(1)) if m else self.limit

    def resolved_allow_versions(self) -> bool:
        return self.allow_versions and not _NO_VERSION_RE.search(self.prompt)


@dataclass
class RecommendationResult:
    draft: PlaylistDraft
    warnings: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    request_id: str = ""
    considered: int = 0
    excluded: int = 0

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "name": self.draft.name,
            "tracks": [c.model_dump() for c in self.draft.tracks],
            "warnings": self.warnings,
            "queries": self.queries,
            "considered": self.considered,
            "excluded": self.excluded,
        }


class RecommendationService:
    def __init__(
        self,
        adapters: dict[str, MusicServiceAdapter],
        *,
        profile_repo: Optional[MusicProfileRepository] = None,
        storage=None,  # optional MusicRepository
    ) -> None:
        self.adapters = adapters
        self.profile_repo = profile_repo or MusicProfileRepository()
        self.storage = storage

    async def recommend(
        self, request: RecommendationRequest, *, record: bool = True
    ) -> RecommendationResult:
        warnings: list[str] = []
        limit = request.resolved_limit()
        allow_versions = request.resolved_allow_versions()

        # 2-3) Effective profile = baseline merged with this request's steering.
        profile = self.profile_repo.load_effective(
            {"reference_artists": request.reference_artists, "prompt": request.prompt}
        )

        # 4) Build seed queries from reference artists.
        queries = _build_queries(profile)
        if not queries:
            warnings.append("No reference artists available to seed search.")

        # 5) Search selected, authenticated services.
        found: list[tuple[Track, str]] = []  # (track, reason-seed)
        per_query = _per_query_limit(limit, len(queries))
        for service in request.services:
            adapter = self.adapters.get(service)
            if adapter is None:
                warnings.append(f"Service '{service}' is not available.")
                continue
            try:
                await adapter.authenticate()
            except MusicServiceError as exc:
                warnings.append(f"{service}: {exc.user_message}")
                continue
            for q in queries:
                try:
                    results = await adapter.search_tracks(q, limit=per_query)
                except MusicServiceError as exc:
                    warnings.append(f"{service} search '{q}': {exc.user_message}")
                    continue
                for t in results:
                    found.append((t, q))

        # 6) Deduplicate (keep the first/best occurrence + its seed).
        seed_by_key: dict[str, str] = {}
        for t, seed in found:
            seed_by_key.setdefault(canonical_key(t), seed)
        unique = deduplicate([t for t, _ in found])
        considered = len(unique)

        # 7) Exclusions.
        exclude_keys = await self._excluded_keys(request, profile) if request.exclude_existing else set()
        exclude_keys |= self._disliked_keys()

        kept: list[Track] = []
        dropped_versions = 0
        excluded = 0
        for t in unique:
            if canonical_key(t) in exclude_keys:
                excluded += 1
                continue
            if not allow_versions and detect_versions(t.title):
                dropped_versions += 1
                continue
            kept.append(t)
        if dropped_versions:
            warnings.append(
                f"{dropped_versions} live/acoustic/remix/remaster variant(s) were "
                "excluded (pass allow_versions to include them)."
            )

        # 8) Rank.
        ref_set = {normalize_text(a) for a in profile.reference_artists}
        avoid_terms = [normalize_text(a) for a in profile.preferences.avoid]
        kept.sort(key=lambda t: _rank_key(t, ref_set, avoid_terms))

        # 9-10) Build the preview with reasons.
        selected = kept[:limit]
        candidates = [
            _to_candidate(t, seed_by_key.get(canonical_key(t)), ref_set) for t in selected
        ]
        if len(selected) < limit:
            warnings.append(f"Only {len(selected)} of {limit} requested tracks found.")

        request_id = uuid.uuid4().hex
        draft = PlaylistDraft(
            name=request.name or _derive_name(request.prompt),
            description=f"Generated by Hermes from: {request.prompt}",
            tracks=candidates,
        )

        # Record so we don't re-recommend these next time.
        if record and self.storage is not None and candidates:
            self.storage.add_recommendations(request_id, request.prompt, candidates)

        return RecommendationResult(
            draft=draft,
            warnings=warnings,
            queries=queries,
            request_id=request_id,
            considered=considered,
            excluded=excluded,
        )

    # ---- exclusion sources ----

    async def _excluded_keys(self, request: RecommendationRequest, profile: MusicProfile) -> set[str]:
        keys: set[str] = set()
        for service in request.services:
            adapter = self.adapters.get(service)
            if adapter is None:
                continue
            try:
                lib = await adapter.get_library_tracks(limit=None)
                keys |= {canonical_key(t) for t in lib}
            except MusicServiceError:
                pass
            if request.include_playlist_tracks:
                try:
                    for pl in await adapter.get_playlists():
                        details = await adapter.get_playlist(pl.id)
                        keys |= {canonical_key(t) for t in details.tracks}
                except MusicServiceError:
                    pass
        # Already-recommended canonical ids from storage.
        if self.storage is not None:
            keys |= self.storage.already_recommended_ids()
        return keys

    def _disliked_keys(self) -> set[str]:
        return self.storage.disliked_ids() if self.storage is not None else set()


# --- helpers ----------------------------------------------------------------

def _build_queries(profile: MusicProfile) -> list[str]:
    seen: set[str] = set()
    queries: list[str] = []
    for artist in profile.reference_artists:
        key = artist.casefold().strip()
        if key and key not in seen:
            seen.add(key)
            queries.append(artist)
    return queries[:_MAX_QUERIES]


def _per_query_limit(limit: int, num_queries: int) -> int:
    if num_queries <= 0:
        return _MAX_PER_QUERY
    return max(5, min(_MAX_PER_QUERY, math.ceil(limit * 2 / num_queries)))


def _rank_key(track: Track, ref_set: set[str], avoid_terms: list[str]) -> tuple:
    """Sort key (ascending) — lower tuple sorts first, so negate 'good' signals."""
    artist_norm = normalize_text(track.artist)
    title_norm = normalize_text(track.title)
    ref_hit = any(r and (r in artist_norm or artist_norm in r) for r in ref_set)
    avoided = any(term and term in (artist_norm + " " + title_norm) for term in avoid_terms)
    score = 0.0
    if ref_hit:
        score += 2.0
    if avoided:
        score -= 1.0
    # Higher score first; stable, deterministic tiebreak.
    return (-score, artist_norm, title_norm)


def _to_candidate(track: Track, seed: Optional[str], ref_set: set[str]) -> TrackCandidate:
    reason = _reason_for(track, seed, ref_set)
    return TrackCandidate(
        canonical_id=canonical_key(track),
        title=track.title,
        artist=track.artist,
        album=track.album,
        isrc=track.isrc,
        apple_music_id=track.id if track.service == "apple_music" else None,
        tidal_id=track.id if track.service == "tidal" else None,
        reason=reason,
    )


def _reason_for(track: Track, seed: Optional[str], ref_set: set[str]) -> str:
    artist_norm = normalize_text(track.artist)
    if any(r and (r in artist_norm or artist_norm in r) for r in ref_set):
        return f"By your reference artist {track.artist}"
    if seed:
        return f"Discovered via reference artist {seed}"
    return "Aligns with your stated preferences"


def _derive_name(prompt: str) -> str:
    words = re.findall(r"[\w']+", prompt)
    if not words:
        return "Hermes Discovery"
    snippet = " ".join(words[:5]).title()
    return snippet[:60]
