"""Unit tests for the common domain models (Phase 1)."""

import pytest
from pydantic import ValidationError

from music_service.models import (
    Playlist,
    PlaylistDetails,
    PlaylistDraft,
    Track,
    TrackCandidate,
)


def test_track_minimal_and_full():
    t = Track(id="1", service="apple_music", title="Holy", artist="Adam Naas")
    assert t.album is None and t.isrc is None and t.explicit is None

    full = Track(
        id="2",
        service="tidal",
        title="Strong",
        artist="London Grammar",
        album="If You Wait",
        duration_ms=295000,
        isrc="GBARL1300150",
        explicit=False,
        url="https://example/track",
    )
    assert full.duration_ms == 295000
    assert full.isrc == "GBARL1300150"


def test_track_rejects_unknown_service():
    with pytest.raises(ValidationError):
        Track(id="1", service="spotify", title="x", artist="y")


def test_playlist_details_holds_tracks():
    pl = Playlist(id="p1", service="apple_music", name="Test")
    details = PlaylistDetails(
        playlist=pl,
        tracks=[Track(id="1", service="apple_music", title="a", artist="b")],
    )
    assert details.playlist.name == "Test"
    assert len(details.tracks) == 1


def test_playlist_draft_defaults_to_empty_tracks():
    draft = PlaylistDraft(name="Dark Vocal Discovery")
    assert draft.tracks == []
    assert draft.description is None


def test_track_candidate_cross_service_ids():
    c = TrackCandidate(
        canonical_id="isrc:GBARL1300150",
        title="Strong",
        artist="London Grammar",
        isrc="GBARL1300150",
        apple_music_id="123",
        tidal_id="456",
        reason="matches reference artist",
    )
    assert c.apple_music_id == "123"
    assert c.tidal_id == "456"


def test_models_round_trip_json():
    t = Track(id="1", service="apple_music", title="a", artist="b")
    assert Track.model_validate(t.model_dump()) == t
