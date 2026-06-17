"""Phase 6 — TrackMatchingService and the normalization it relies on.

Goal: decide whether a track from one service is the *same recording* as a track
from another, with a confidence score and an explicit match method. Matching
priority (highest first):

  1. exact ISRC                                  -> confidence 1.0
  2. normalized artist + title (+ close duration)
  3. normalized artist + title + album + version
  4. fuzzy fallback (only above a threshold, lower confidence)

Hard rule (from the brief): never silently substitute a live / acoustic / remix
/ remastered (etc.) version when the requested *original* version exists. We
detect a track's "version" and treat a version difference as a near-blocker:
such a candidate is returned but flagged ``version_mismatch`` with capped
confidence and ``matched=False`` — the caller decides what to do with it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from .models import Service, Track, TrackCandidate

# --- version vocabulary -----------------------------------------------------

# Tags that represent a genuinely different recording — substituting across them
# is NOT allowed automatically.
_VERSION_PATTERNS: list[tuple[str, str]] = [
    ("live", r"\blive\b"),
    ("acoustic", r"\bacoustic\b"),
    ("remix", r"\b(?:remix|re-?mix|\w+ mix)\b"),
    ("remaster", r"\b(?:remaster(?:ed)?|\d{4}\s+remaster)\b"),
    ("instrumental", r"\binstrumental\b"),
    ("demo", r"\bdemo\b"),
    ("rerecorded", r"\b(?:re-?recorded|re-?recording|taylor'?s version)\b"),
    ("radio_edit", r"\bradio edit\b"),
    ("karaoke", r"\bkaraoke\b"),
]

# Tags that are noise (album packaging / content rating) — stripped from the
# core title but do NOT count as a version difference.
_NOISE_PATTERNS: list[str] = [
    r"\bdeluxe(?:\s+edition)?\b",
    r"\bexpanded(?:\s+edition)?\b",
    r"\bspecial edition\b",
    r"\banniversary edition\b",
    r"\bbonus track\b",
    r"\bsingle version\b",
    r"\balbum version\b",
    r"\bexplicit\b",
    r"\bclean\b",
    r"\bmono\b",
    r"\bstereo\b",
    r"\bedit\b",
    r"\bversion\b",
]

_FEAT_RE = re.compile(
    r"\s*[\(\[]?\s*(?:feat\.?|featuring|ft\.?|with)\s+[^)\]]*[\)\]]?",
    re.IGNORECASE,
)
# Bracketed/parenthetical or trailing " - ..." segments that often carry version info.
_SEGMENT_RE = re.compile(r"[\(\[][^)\]]*[\)\]]|\s-\s.*$")


# --- normalization ----------------------------------------------------------

def strip_diacritics(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def normalize_text(text: Optional[str]) -> str:
    """Casefold, drop diacritics & punctuation, collapse whitespace."""
    if not text:
        return ""
    text = strip_diacritics(text).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^0-9a-z\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_featuring(title: str) -> str:
    """Remove 'feat./ft./featuring/with ...' clauses from a title."""
    return _FEAT_RE.sub("", title).strip()


def _version_segments(title: str) -> str:
    """Text of the version-bearing parts only: (...) [...] and a trailing ' - ...'.

    Restricting detection to these segments avoids false positives from ordinary
    title words (e.g. "Live Lover" is NOT a live version; "Song (Live)" is).
    """
    segs = [m.group(1) for m in re.finditer(r"[\(\[]([^)\]]*)[\)\]]", title)]
    dash = re.search(r"\s-\s(.*)$", title)
    if dash:
        segs.append(dash.group(1))
    return strip_diacritics(" ".join(segs)).casefold()


def detect_versions(title: str) -> set[str]:
    """Return the set of *blocking* version tags found in a title's segments."""
    blob = _version_segments(title)
    found = set()
    for tag, pat in _VERSION_PATTERNS:
        if re.search(pat, blob):
            found.add(tag)
    return found


def core_title(title: Optional[str]) -> str:
    """Normalized title with feat/version/noise segments removed.

    Used as the comparison key so "Strong (Live)" and "Strong" share a core but
    differ in detected versions.
    """
    if not title:
        return ""
    t = strip_featuring(title)
    # Remove bracketed and trailing-dash segments entirely from the core.
    t = _SEGMENT_RE.sub(" ", t)
    t = normalize_text(t)
    # Defensive: strip any leftover noise/version words appearing inline.
    for pat in _NOISE_PATTERNS:
        t = re.sub(pat, " ", t)
    for _, pat in _VERSION_PATTERNS:
        t = re.sub(pat, " ", t)
    return re.sub(r"\s+", " ", t).strip()


def artist_tokens(artist: Optional[str]) -> set[str]:
    """Split a (possibly multi-)artist string into a set of normalized names."""
    if not artist:
        return set()
    parts = re.split(r",|&|\band\b|\bx\b|\bfeat\.?\b|\bft\.?\b|\bwith\b|/|;", artist, flags=re.IGNORECASE)
    return {normalize_text(p) for p in parts if normalize_text(p)}


# --- similarity helpers -----------------------------------------------------

def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def title_similarity(a: str, b: str) -> float:
    na, nb = core_title(a), core_title(b)
    return max(_ratio(na, nb), _jaccard(set(na.split()), set(nb.split())))


def artist_similarity(a: str, b: str) -> float:
    ta, tb = artist_tokens(a), artist_tokens(b)
    # Strong if the primary-artist token sets overlap, with a string-ratio fallback.
    overlap = _jaccard(ta, tb)
    string_ratio = _ratio(normalize_text(a), normalize_text(b))
    primary = 1.0 if (ta and tb and (ta & tb)) else 0.0
    return max(overlap, string_ratio, 0.9 * primary if primary else 0.0)


def durations_close(
    a: Optional[int], b: Optional[int], *, abs_tol_ms: int = 3000, rel_tol: float = 0.02
) -> Optional[bool]:
    """True/False if both durations known; None if either is missing."""
    if a is None or b is None:
        return None
    diff = abs(a - b)
    return diff <= max(abs_tol_ms, int(rel_tol * max(a, b)))


# --- match result -----------------------------------------------------------

@dataclass
class MatchResult:
    matched: bool
    track: Optional[Track]
    match_method: str  # isrc | exact | normalized | normalized_album | fuzzy | version_mismatch | none
    confidence: float
    version_mismatch: bool = False
    ambiguous: bool = False
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "match_method": self.match_method,
            "confidence": round(self.confidence, 3),
            "version_mismatch": self.version_mismatch,
            "ambiguous": self.ambiguous,
            "track_id": self.track.id if self.track else None,
            "notes": self.notes,
        }


# --- the service ------------------------------------------------------------

class TrackMatchingService:
    def __init__(
        self,
        *,
        accept_threshold: float = 0.7,
        version_mismatch_cap: float = 0.5,
        ambiguity_delta: float = 0.05,
    ) -> None:
        self.accept_threshold = accept_threshold
        self.version_mismatch_cap = version_mismatch_cap
        self.ambiguity_delta = ambiguity_delta

    # ---- scoring a single pair ----

    def _score(self, source: Track, candidate: Track) -> tuple[float, str, bool]:
        """Return (confidence, method, version_mismatch) for one candidate."""
        # 1) ISRC is authoritative.
        if source.isrc and candidate.isrc and source.isrc.upper() == candidate.isrc.upper():
            return 1.0, "isrc", False

        a_sim = artist_similarity(source.artist, candidate.artist)
        t_sim = title_similarity(source.title, candidate.title)
        base = 0.45 * a_sim + 0.55 * t_sim

        # Album bonus.
        album_match = False
        if source.album and candidate.album:
            if title_similarity(source.album, candidate.album) >= 0.8:
                album_match = True
                base = min(1.0, base + 0.05)

        # Duration.
        close = durations_close(source.duration_ms, candidate.duration_ms)
        if close is True:
            base = min(1.0, base + 0.05)
        elif close is False:
            base -= 0.2

        # Version safety.
        sv, cv = detect_versions(source.title), detect_versions(candidate.title)
        version_mismatch = sv != cv
        if version_mismatch:
            base -= 0.4

        confidence = max(0.0, min(1.0, base))

        if version_mismatch:
            return min(confidence, self.version_mismatch_cap), "version_mismatch", True
        if a_sim >= 0.97 and t_sim >= 0.97:
            return confidence, "exact", False
        if album_match and confidence >= 0.85:
            return confidence, "normalized_album", False
        if confidence >= 0.85:
            return confidence, "normalized", False
        return confidence, "fuzzy", False

    # ---- public API ----

    def match_track(self, source: Track, candidates: list[Track]) -> MatchResult:
        """Find the best match for ``source`` among ``candidates``."""
        if not candidates:
            return MatchResult(False, None, "none", 0.0, notes="no candidates")

        scored = [
            (conf, method, vm, cand)
            for cand in candidates
            for (conf, method, vm) in [self._score(source, cand)]
        ]
        # Prefer non-version-mismatch, then higher confidence.
        scored.sort(key=lambda x: (not x[2], x[0]), reverse=True)
        best_conf, best_method, best_vm, best_track = scored[0]

        # Ambiguity: two acceptable, version-OK candidates within delta.
        ok = [s for s in scored if not s[2] and s[0] >= self.accept_threshold]
        ambiguous = len(ok) >= 2 and (ok[0][0] - ok[1][0]) <= self.ambiguity_delta

        matched = (best_conf >= self.accept_threshold) and not best_vm
        if best_method == "isrc":
            matched = True

        notes = None
        if best_vm:
            sv = detect_versions(source.title) ^ detect_versions(best_track.title)
            notes = f"version difference: {sorted(sv)}"
        return MatchResult(
            matched=matched,
            track=best_track,
            match_method=best_method if (matched or best_vm) else "none",
            confidence=best_conf,
            version_mismatch=best_vm,
            ambiguous=ambiguous,
            notes=notes,
        )

    def build_mapping(self, source: Track, result: MatchResult) -> dict:
        """Produce the cross-service mapping dict (per the brief's example)."""
        ids: dict[str, Optional[str]] = {"apple_music_id": None, "tidal_id": None}
        _set_service_id(ids, source.service, source.id)
        if result.track:
            _set_service_id(ids, result.track.service, result.track.id)
        return {
            **ids,
            "isrc": source.isrc or (result.track.isrc if result.track else None),
            "match_method": result.match_method,
            "confidence": round(result.confidence, 3),
            "version_mismatch": result.version_mismatch,
        }


def _set_service_id(ids: dict, service: Service, value: str) -> None:
    if service == "apple_music":
        ids["apple_music_id"] = value
    elif service == "tidal":
        ids["tidal_id"] = value


# --- dedup / canonical id ---------------------------------------------------

def canonical_key(track: Track) -> str:
    """Stable identity for dedup: ISRC if present, else artist+core-title+versions."""
    if track.isrc:
        return f"isrc:{track.isrc.upper()}"
    artists = "+".join(sorted(artist_tokens(track.artist)))
    versions = "+".join(sorted(detect_versions(track.title)))
    return f"k:{artists}|{core_title(track.title)}|{versions}"


def deduplicate(tracks: list[Track]) -> list[Track]:
    """Remove duplicates by canonical key, preserving first-seen order."""
    seen: set[str] = set()
    out: list[Track] = []
    for t in tracks:
        key = canonical_key(t)
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out
