"""Unit tests for Phase 5 — SQLite storage."""

import pytest

from music_service.matching import TrackMatchingService, canonical_key
from music_service.models import Playlist, PlaylistDetails, Track, TrackCandidate
from music_service.storage import MusicRepository, connect
from music_service.storage.db import SCHEMA_VERSION


@pytest.fixture
def repo():
    r = MusicRepository(connect(":memory:"))
    yield r
    r.close()


def _t(tid, service="tidal", title="Strong", artist="London Grammar", **kw):
    return Track(id=tid, service=service, title=title, artist=artist, **kw)


def test_schema_version_set(repo):
    v = repo.conn.execute("PRAGMA user_version").fetchone()[0]
    assert v == SCHEMA_VERSION


def test_service_accounts_have_no_secret_columns(repo):
    cols = {r[1] for r in repo.conn.execute("PRAGMA table_info(service_accounts)").fetchall()}
    # No token/secret/key columns anywhere in the accounts table.
    assert not (cols & {"access_token", "refresh_token", "client_secret", "private_key", "token"})
    assert "scopes" in cols and "user_id" in cols


def test_service_account_roundtrip(repo):
    repo.upsert_service_account("tidal", user_id="42", country="UA", scopes="playlists.read playlists.write")
    acc = repo.get_service_account("tidal")
    assert acc["user_id"] == "42"
    assert acc["country"] == "UA"
    assert "playlists.write" in acc["scopes"]
    # upsert updates in place
    repo.upsert_service_account("tidal", user_id="42", country="US", scopes="playlists.read")
    assert repo.get_service_account("tidal")["country"] == "US"


def test_track_upsert_stores_normalized_and_canonical(repo):
    t = _t("t1", title="Strong (Live)", artist="London Grammar", isrc="GB1")
    repo.upsert_track(t)
    got = repo.get_track("tidal", "t1")
    assert got.title == "Strong (Live)"
    row = repo.conn.execute("SELECT normalized_title, canonical_key FROM tracks WHERE service_track_id='t1'").fetchone()
    assert row["normalized_title"] == "strong live"  # normalized, version word kept in normalized_title
    assert row["canonical_key"] == canonical_key(t)


def test_track_upsert_is_idempotent(repo):
    repo.upsert_track(_t("t1", album="A"))
    repo.upsert_track(_t("t1", album="B"))  # same (service, id) -> update
    rows = repo.conn.execute("SELECT COUNT(*) FROM tracks WHERE service_track_id='t1'").fetchone()[0]
    assert rows == 1
    assert repo.get_track("tidal", "t1").album == "B"


def test_playlist_snapshot_roundtrip_preserves_order(repo):
    details = PlaylistDetails(
        playlist=Playlist(id="p1", service="tidal", name="Favorites", description=None),
        tracks=[_t("a", title="One"), _t("b", title="Two"), _t("c", title="Three")],
    )
    snap_id = repo.save_playlist_snapshot(details)
    assert isinstance(snap_id, int)
    loaded = repo.get_latest_snapshot("tidal", "p1")
    assert loaded.playlist.name == "Favorites"
    assert [t.title for t in loaded.tracks] == ["One", "Two", "Three"]
    # snapshot also populated the track cache
    assert repo.get_track("tidal", "a") is not None


def test_latest_snapshot_returns_most_recent(repo):
    pl = Playlist(id="p1", service="tidal", name="V1")
    repo.save_playlist_snapshot(PlaylistDetails(playlist=pl, tracks=[_t("a")]))
    pl2 = Playlist(id="p1", service="tidal", name="V2")
    repo.save_playlist_snapshot(PlaylistDetails(playlist=pl2, tracks=[_t("a"), _t("b")]))
    loaded = repo.get_latest_snapshot("tidal", "p1")
    assert loaded.playlist.name == "V2"
    assert len(loaded.tracks) == 2


def test_mapping_upsert_and_coalesce(repo):
    svc = TrackMatchingService()
    apple = _t("123", service="apple_music", isrc="GB1")
    tidal = _t("456", service="tidal", isrc="GB1")
    res = svc.match_track(apple, [tidal])
    mapping = svc.build_mapping(apple, res)
    ck = canonical_key(apple)
    repo.upsert_mapping(ck, mapping)

    got = repo.get_mapping(ck)
    assert got["apple_music_id"] == "123"
    assert got["tidal_id"] == "456"
    assert got["match_method"] == "isrc"

    # A later partial update (tidal only) must not wipe the apple id (COALESCE).
    repo.upsert_mapping(ck, {"tidal_id": "456b", "confidence": 0.9, "match_method": "normalized"})
    got2 = repo.get_mapping(ck)
    assert got2["apple_music_id"] == "123"  # preserved
    assert got2["tidal_id"] == "456b"

    assert repo.find_mapping_by_service_id("apple_music", "123")["canonical_track_id"] == ck


def test_recommendation_history_and_exclusion(repo):
    cands = [
        TrackCandidate(canonical_id="k1", title="A", artist="X", tidal_id="1"),
        TrackCandidate(canonical_id="k2", title="B", artist="Y", apple_music_id="2"),
    ]
    repo.add_recommendations("req-1", "emotional vocals", cands)
    assert repo.already_recommended_ids() == {"k1", "k2"}


def test_feedback_validation_and_negative_exclusion(repo):
    repo.record_feedback(feedback="liked", canonical_track_id="k1")
    repo.record_feedback(feedback="disliked", canonical_track_id="k2")
    repo.record_feedback(feedback="wrong_version", canonical_track_id="k3")
    assert repo.disliked_ids() == {"k2", "k3"}
    assert len(repo.get_feedback("k1")) == 1

    with pytest.raises(ValueError):
        repo.record_feedback(feedback="meh", canonical_track_id="k9")
