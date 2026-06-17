"""Unit tests for Phase 9 — app facade, sync, drafts, tool registration."""

import asyncio

import pytest

from music_service.adapter import MusicServiceAdapter
from music_service.app import MusicToolsService
from music_service.errors import ConfirmationRequiredError, MusicServiceError, TrackNotFoundError
from music_service.models import Playlist, PlaylistDetails, PlaylistDraft, Track, TrackCandidate
from music_service.profile import MusicProfileRepository
from music_service.storage import MusicRepository, connect


def _t(tid, title, artist, service="tidal", **kw):
    return Track(id=tid, service=service, title=title, artist=artist, **kw)


class FakeAdapter(MusicServiceAdapter):
    capabilities = {
        "search": True, "read_playlists": True, "read_library": True,
        "create_playlist": True, "add_tracks": True,
    }

    def __init__(self, service, *, catalog=None, playlists=None, library=None, auth_error=False):
        self.service = service
        self.catalog = catalog or []
        self._playlists = {d.playlist.id: d for d in (playlists or [])}
        self.library = library or []
        self.auth_error = auth_error
        self.added: dict[str, list[str]] = {}
        self._counter = 0

    async def authenticate(self):
        if self.auth_error:
            raise MusicServiceError(user_message="auth needed", service=self.service)

    async def search_tracks(self, query, limit=20):
        q = query.lower()
        return [t for t in self.catalog if q in (t.artist + " " + t.title).lower()][:limit]

    async def get_playlists(self):
        return [d.playlist for d in self._playlists.values()]

    async def get_playlist(self, playlist_id):
        if playlist_id not in self._playlists:
            raise TrackNotFoundError(service=self.service)
        return self._playlists[playlist_id]

    async def get_library_tracks(self, limit=None):
        return list(self.library)

    async def create_playlist(self, name, description=None):
        self._counter += 1
        pid = f"new{self._counter}"
        pl = Playlist(id=pid, service=self.service, name=name, description=description)
        self._playlists[pid] = PlaylistDetails(playlist=pl, tracks=[])
        return pl

    async def add_tracks(self, playlist_id, track_ids):
        self.added.setdefault(playlist_id, []).extend(track_ids)


@pytest.fixture
def storage():
    r = MusicRepository(connect(":memory:"))
    yield r
    r.close()


@pytest.fixture
def profile_repo(tmp_path):
    f = tmp_path / "MUSIC_PROFILE.md"
    f.write_text("```yaml\nreference_artists:\n  - Aurora Sky\n```", encoding="utf-8")
    return MusicProfileRepository(path=f)


def _svc(adapters: dict, storage, profile_repo):
    return MusicToolsService(
        adapter_factory=lambda s: adapters[s], storage=storage, profile_repo=profile_repo
    )


# ---- drafts ---------------------------------------------------------------

def test_draft_persistence_roundtrip(storage):
    draft = PlaylistDraft(name="X", tracks=[TrackCandidate(canonical_id="k", title="A", artist="B")])
    storage.save_draft("d1", draft)
    got = storage.get_draft("d1")
    assert got.name == "X"
    assert got.tracks[0].title == "A"
    assert storage.get_draft("missing") is None


# ---- accounts status ------------------------------------------------------

@pytest.mark.asyncio
async def test_accounts_status(storage, profile_repo):
    adapters = {
        "tidal": FakeAdapter("tidal"),
        "apple_music": FakeAdapter("apple_music", auth_error=True),
    }
    svc = _svc(adapters, storage, profile_repo)
    res = await svc.accounts_status(("tidal", "apple_music"))
    assert res["accounts"]["tidal"]["status"] == "connected"
    assert res["accounts"]["apple_music"]["status"] == "auth_required"


# ---- recommend -> preview -------------------------------------------------

@pytest.mark.asyncio
async def test_recommend_persists_draft_and_preview(storage, profile_repo):
    catalog = [_t("1", "Song", "Aurora Sky")]
    svc = _svc({"tidal": FakeAdapter("tidal", catalog=catalog)}, storage, profile_repo)
    rec = await svc.recommend("moody", services=("tidal",))
    draft_id = rec["draft_id"]
    assert rec["tracks"]
    preview = svc.preview_playlist(draft_id)
    assert preview["draft_id"] == draft_id
    assert preview["tracks"][0]["title"] == "Song"


def test_preview_missing_draft_raises(storage, profile_repo):
    svc = _svc({}, storage, profile_repo)
    with pytest.raises(TrackNotFoundError):
        svc.preview_playlist("nope")


# ---- create playlist (confirmation + candidate resolution) ----------------

@pytest.mark.asyncio
async def test_create_requires_confirmation(storage, profile_repo):
    draft = PlaylistDraft(name="D", tracks=[TrackCandidate(canonical_id="k", title="A", artist="B", tidal_id="x")])
    storage.save_draft("d1", draft)
    svc = _svc({"tidal": FakeAdapter("tidal")}, storage, profile_repo)
    with pytest.raises(ConfirmationRequiredError):
        await svc.create_playlist("d1", services=["tidal"], confirmed=False)


@pytest.mark.asyncio
async def test_create_resolves_direct_and_searched(storage, profile_repo):
    draft = PlaylistDraft(
        name="D",
        tracks=[
            TrackCandidate(canonical_id="k1", title="Direct", artist="A", tidal_id="x"),  # direct id
            TrackCandidate(canonical_id="k2", title="Findable", artist="B"),               # via search
            TrackCandidate(canonical_id="k3", title="Ghost", artist="Nobody"),             # unmatched
        ],
    )
    storage.save_draft("d1", draft)
    catalog = [_t("found1", "Findable", "B", duration_ms=200000)]
    adapter = FakeAdapter("tidal", catalog=catalog)
    svc = _svc({"tidal": adapter}, storage, profile_repo)
    res = await svc.create_playlist("d1", services=["tidal"], confirmed=True)
    tidal = res["created"]["tidal"]
    assert tidal["added"] == 2  # direct x + found1
    assert len(tidal["unmatched"]) == 1
    assert adapter.added[tidal["playlist_id"]] == ["x", "found1"]


@pytest.mark.asyncio
async def test_add_tracks_requires_confirmation(storage, profile_repo):
    adapter = FakeAdapter("tidal")
    svc = _svc({"tidal": adapter}, storage, profile_repo)
    with pytest.raises(ConfirmationRequiredError):
        await svc.add_tracks("tidal", "p1", ["a"], confirmed=False)
    res = await svc.add_tracks("tidal", "p1", ["a", "b"], confirmed=True)
    assert res["added"] == 2


# ---- sync -----------------------------------------------------------------

def _sync_setup():
    src_tracks = [
        _t("m", "Match Me", "Artist One", service="apple_music", isrc="ISRC1", duration_ms=200000),
        _t("l", "Live Lover", "Artist Two", service="apple_music", duration_ms=210000),
        _t("g", "Ghost Song", "Nobody", service="apple_music"),
        _t("am", "Ambi Tune", "Artist Three", service="apple_music", duration_ms=180000),
    ]
    source = FakeAdapter(
        "apple_music",
        playlists=[PlaylistDetails(playlist=Playlist(id="p1", service="apple_music", name="Src"), tracks=src_tracks)],
    )
    dest_catalog = [
        _t("d_m", "Match Me", "Artist One", isrc="ISRC1", duration_ms=200500),       # clean match
        _t("d_live", "Live Lover (Live)", "Artist Two", duration_ms=240000),         # version mismatch
        _t("d_a1", "Ambi Tune", "Artist Three", duration_ms=180000),                 # ambiguous pair
        _t("d_a2", "Ambi Tune", "Artist Three", duration_ms=180200),
    ]
    dest = FakeAdapter("tidal", catalog=dest_catalog)
    return source, dest


@pytest.mark.asyncio
async def test_sync_dry_run_does_not_create(storage, profile_repo):
    source, dest = _sync_setup()
    svc = _svc({"apple_music": source, "tidal": dest}, storage, profile_repo)
    res = await svc.sync_playlist("apple_music", "p1", ["tidal"], confirmed=False)
    report = res["syncs"][0]
    assert report["destination_playlist_id"] is None
    assert report["created"] is False
    assert report["counts"]["matched"] == 1
    assert report["counts"]["substituted"] == 1
    assert report["counts"]["ambiguous"] == 1
    assert report["counts"]["unmatched"] == 1
    assert report["counts"]["source_total"] == 4
    assert dest.added == {}  # nothing written


@pytest.mark.asyncio
async def test_sync_confirmed_creates_and_adds(storage, profile_repo):
    source, dest = _sync_setup()
    svc = _svc({"apple_music": source, "tidal": dest}, storage, profile_repo)
    res = await svc.sync_playlist("apple_music", "p1", ["tidal"], confirmed=True)
    report = res["syncs"][0]
    assert report["created"] is True
    pid = report["destination_playlist_id"]
    assert pid is not None
    assert dest.added[pid] == ["d_m"]  # only the confident match is added


# ---- feedback -------------------------------------------------------------

def test_record_feedback_valid_and_invalid(storage, profile_repo):
    svc = _svc({}, storage, profile_repo)
    assert svc.record_feedback("liked", canonical_id="k1")["recorded"] is True
    with pytest.raises(MusicServiceError):
        svc.record_feedback("nonsense", canonical_id="k1")


# ---- server tool registration --------------------------------------------

def test_server_registers_all_tools():
    import server

    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    expected = {
        "music_get_accounts_status",
        "music_get_playlists",
        "music_get_playlist_tracks",
        "music_search_tracks",
        "music_recommend_tracks",
        "music_preview_playlist",
        "music_create_playlist",
        "music_add_tracks",
        "music_sync_playlist",
        "music_sync_favorites",
        "music_record_feedback",
        "music_resolve_tracks",
        "music_get_seed_artists",
        "music_save_artist_moods",
        "music_get_library_artists",
    }
    assert expected <= names
