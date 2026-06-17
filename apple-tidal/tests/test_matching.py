"""Unit tests for Phase 6 — normalization, matching, versioning, dedup."""

import pytest

from music_service.matching import (
    MatchResult,
    TrackMatchingService,
    artist_tokens,
    canonical_key,
    core_title,
    deduplicate,
    detect_versions,
    durations_close,
    normalize_text,
    strip_featuring,
    title_similarity,
)
from music_service.models import Track


def _t(tid, service, title, artist, **kw):
    return Track(id=tid, service=service, title=title, artist=artist, **kw)


# ---- normalization --------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Holé!", "hole"),
        ("Café  del  Mar", "cafe del mar"),
        ("AC/DC", "ac dc"),
        ("Sigur Rós", "sigur ros"),
        ("Beyoncé & JAY-Z", "beyonce and jay z"),
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


def test_strip_featuring():
    assert strip_featuring("Holy (feat. Adam Naas)").strip().lower().startswith("holy")
    assert "feat" not in strip_featuring("Song ft. Someone").lower()
    assert "featuring" not in strip_featuring("Song featuring X").lower()


def test_core_title_removes_versions_and_feat():
    assert core_title("Strong (Live)") == "strong"
    assert core_title("Strong - 2011 Remaster") == "strong"
    assert core_title("Holy (feat. Adam Naas) [Acoustic]") == "holy"
    assert core_title("Wow (Deluxe Edition)") == "wow"


def test_artist_tokens_splits_collaborators():
    assert artist_tokens("Jay-Z & Beyoncé") == {"jay z", "beyonce"}
    assert artist_tokens("London Grammar feat. Someone") == {"london grammar", "someone"}


# ---- version detection ----------------------------------------------------

@pytest.mark.parametrize(
    "title,tags",
    [
        ("Strong", set()),
        ("Strong (Live)", {"live"}),
        ("Strong - Acoustic", {"acoustic"}),
        ("Strong (2011 Remaster)", {"remaster"}),
        ("Strong (Tiësto Remix)", {"remix"}),
        ("Song (Instrumental)", {"instrumental"}),
        ("Song (Taylor's Version)", {"rerecorded"}),
    ],
)
def test_detect_versions(title, tags):
    assert detect_versions(title) == tags


# ---- duration tolerance ---------------------------------------------------

def test_durations_close():
    assert durations_close(200000, 201500) is True   # within 3s
    assert durations_close(200000, 210000) is False  # 10s off
    assert durations_close(200000, None) is None
    assert durations_close(1_000_000, 1_015_000) is True  # within 2% rel


# ---- ISRC matching (priority 1) -------------------------------------------

def test_isrc_match_is_authoritative():
    svc = TrackMatchingService()
    src = _t("a1", "apple_music", "Whatever", "Someone", isrc="gbarl1300150")
    cands = [
        _t("t9", "tidal", "Totally Different Title", "Other", isrc="GBARL1300150"),
        _t("t8", "tidal", "Whatever", "Someone"),
    ]
    res = svc.match_track(src, cands)
    assert res.matched is True
    assert res.match_method == "isrc"
    assert res.confidence == 1.0
    assert res.track.id == "t9"  # ISRC wins over title similarity


# ---- normalized matching (priority 2/3) -----------------------------------

def test_normalized_match_without_isrc():
    svc = TrackMatchingService()
    src = _t("a1", "apple_music", "Strong", "London Grammar", album="If You Wait", duration_ms=295000)
    cands = [
        _t("t1", "tidal", "Strong", "London Grammar", album="If You Wait", duration_ms=296000),
    ]
    res = svc.match_track(src, cands)
    assert res.matched is True
    assert res.match_method in ("exact", "normalized_album", "normalized")
    assert res.confidence >= 0.85
    assert res.version_mismatch is False


def test_feat_difference_still_matches():
    svc = TrackMatchingService()
    src = _t("a1", "apple_music", "Holy", "Adam Naas")
    cands = [_t("t1", "tidal", "Holy (feat. Someone)", "Adam Naas")]
    res = svc.match_track(src, cands)
    assert res.matched is True


# ---- version substitution must NOT happen automatically -------------------

def test_does_not_substitute_live_for_original():
    svc = TrackMatchingService()
    src = _t("a1", "apple_music", "Strong", "London Grammar")
    cands = [_t("t1", "tidal", "Strong (Live)", "London Grammar")]
    res = svc.match_track(src, cands)
    assert res.matched is False
    assert res.version_mismatch is True
    assert res.match_method == "version_mismatch"
    assert res.confidence <= 0.5
    assert res.track.id == "t1"  # still surfaced for the caller to decide


def test_prefers_original_over_live_when_both_present():
    svc = TrackMatchingService()
    src = _t("a1", "apple_music", "Strong", "London Grammar", duration_ms=295000)
    cands = [
        _t("live", "tidal", "Strong (Live)", "London Grammar", duration_ms=320000),
        _t("orig", "tidal", "Strong", "London Grammar", duration_ms=295000),
    ]
    res = svc.match_track(src, cands)
    assert res.matched is True
    assert res.track.id == "orig"
    assert res.version_mismatch is False


def test_remaster_not_substituted_for_original():
    svc = TrackMatchingService()
    src = _t("a1", "apple_music", "Bohemian Rhapsody", "Queen")
    cands = [_t("t1", "tidal", "Bohemian Rhapsody (2011 Remaster)", "Queen")]
    res = svc.match_track(src, cands)
    assert res.version_mismatch is True
    assert res.matched is False


# ---- fuzzy + no-match ------------------------------------------------------

def test_no_match_below_threshold():
    svc = TrackMatchingService()
    src = _t("a1", "apple_music", "Strong", "London Grammar")
    cands = [_t("t1", "tidal", "Completely Unrelated", "Another Band")]
    res = svc.match_track(src, cands)
    assert res.matched is False
    assert res.match_method == "none"


def test_empty_candidates():
    svc = TrackMatchingService()
    res = svc.match_track(_t("a1", "apple_music", "x", "y"), [])
    assert res.matched is False
    assert res.track is None


# ---- ambiguity ------------------------------------------------------------

def test_ambiguous_when_two_close_candidates():
    svc = TrackMatchingService()
    src = _t("a1", "apple_music", "Strong", "London Grammar", duration_ms=295000)
    cands = [
        _t("t1", "tidal", "Strong", "London Grammar", duration_ms=295000),
        _t("t2", "tidal", "Strong", "London Grammar", duration_ms=295500),
    ]
    res = svc.match_track(src, cands)
    assert res.ambiguous is True


# ---- mapping output shape -------------------------------------------------

def test_build_mapping_shape():
    svc = TrackMatchingService()
    src = _t("123", "apple_music", "Strong", "London Grammar", isrc="GB123")
    cand = _t("456", "tidal", "Strong", "London Grammar", isrc="GB123")
    res = svc.match_track(src, [cand])
    mapping = svc.build_mapping(src, res)
    assert mapping["apple_music_id"] == "123"
    assert mapping["tidal_id"] == "456"
    assert mapping["match_method"] == "isrc"
    assert mapping["confidence"] == 1.0


# ---- dedup ----------------------------------------------------------------

def test_deduplicate_by_isrc_and_normalized_key():
    tracks = [
        _t("1", "tidal", "Strong", "London Grammar", isrc="GB1"),
        _t("2", "apple_music", "STRONG", "london grammar", isrc="GB1"),  # same ISRC
        _t("3", "tidal", "Strong (feat. X)", "London Grammar"),         # same core/no isrc
        _t("4", "tidal", "Strong", "London Grammar"),                   # dup of 3
        _t("5", "tidal", "Strong (Live)", "London Grammar"),            # different version -> kept
    ]
    out = deduplicate(tracks)
    ids = [t.id for t in out]
    assert ids == ["1", "3", "5"]


def test_canonical_key_distinguishes_versions():
    assert canonical_key(_t("1", "tidal", "Strong", "LG")) != canonical_key(
        _t("2", "tidal", "Strong (Live)", "LG")
    )
