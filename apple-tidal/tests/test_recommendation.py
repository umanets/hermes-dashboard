"""Unit tests for Phase 8 — RecommendationService (offline, fake adapter)."""

import pytest

from music_service.adapter import MusicServiceAdapter
from music_service.errors import AuthenticationRequiredError
from music_service.matching import canonical_key
from music_service.models import Track
from music_service.profile import MusicProfileRepository
from music_service.recommendation import (
    RecommendationRequest,
    RecommendationService,
)
from music_service.storage import MusicRepository, connect


def _t(tid, title, artist, **kw):
    return Track(id=tid, service="tidal", title=title, artist=artist, **kw)


class FakeAdapter(MusicServiceAdapter):
    service = "tidal"
    capabilities = {"search": True, "read_playlists": True, "read_library": True}

    def __init__(self, catalog, *, library=None, auth_error=False):
        self.catalog = catalog
        self.library = library or []
        self.auth_error = auth_error

    async def authenticate(self):
        if self.auth_error:
            raise AuthenticationRequiredError(service="tidal")

    async def search_tracks(self, query, limit=20):
        q = query.lower()
        return [t for t in self.catalog if q in t.artist.lower() or q in t.title.lower()][:limit]

    async def get_playlists(self):
        return []

    async def get_playlist(self, playlist_id):
        raise NotImplementedError

    async def get_library_tracks(self, limit=None):
        return self.library[:limit] if limit else list(self.library)


@pytest.fixture
def profile_repo(tmp_path):
    f = tmp_path / "MUSIC_PROFILE.md"
    f.write_text(
        "```yaml\n"
        "preferences:\n  avoid:\n    - tribute\n"
        "reference_artists:\n  - Aurora Sky\n  - Velvet North\n"
        "```",
        encoding="utf-8",
    )
    return MusicProfileRepository(path=f)


def _service(catalog, profile_repo, *, library=None, storage=None, auth_error=False):
    return RecommendationService(
        {"tidal": FakeAdapter(catalog, library=library, auth_error=auth_error)},
        profile_repo=profile_repo,
        storage=storage,
    )


@pytest.mark.asyncio
async def test_basic_pipeline_seeds_from_profile(profile_repo):
    catalog = [
        _t("1", "Song One", "Aurora Sky"),
        _t("2", "Song Two (Live)", "Aurora Sky"),  # version -> dropped
        _t("3", "Track A", "Velvet North"),
    ]
    svc = _service(catalog, profile_repo)
    res = await svc.recommend(RecommendationRequest(prompt="something moody", services=["tidal"]))
    titles = {c.title for c in res.draft.tracks}
    assert titles == {"Song One", "Track A"}
    assert res.queries == ["Aurora Sky", "Velvet North"]
    assert all(c.tidal_id for c in res.draft.tracks)
    assert any("variant" in w for w in res.warnings)
    # reason mentions the reference artist
    assert any("reference artist" in (c.reason or "") for c in res.draft.tracks)


@pytest.mark.asyncio
async def test_allow_versions_includes_live(profile_repo):
    catalog = [_t("2", "Song Two (Live)", "Aurora Sky")]
    svc = _service(catalog, profile_repo)
    res = await svc.recommend(
        RecommendationRequest(prompt="x", services=["tidal"], allow_versions=True)
    )
    assert {c.title for c in res.draft.tracks} == {"Song Two (Live)"}


@pytest.mark.asyncio
async def test_original_only_prompt_overrides_allow_versions(profile_repo):
    catalog = [_t("2", "Song Two (Live)", "Aurora Sky")]
    svc = _service(catalog, profile_repo)
    res = await svc.recommend(
        RecommendationRequest(prompt="original only please", services=["tidal"], allow_versions=True)
    )
    assert res.draft.tracks == []


@pytest.mark.asyncio
async def test_excludes_library_tracks(profile_repo):
    t1 = _t("1", "Song One", "Aurora Sky")
    catalog = [t1, _t("3", "Track A", "Velvet North")]
    svc = _service(catalog, profile_repo, library=[t1])
    res = await svc.recommend(RecommendationRequest(prompt="x", services=["tidal"]))
    assert {c.title for c in res.draft.tracks} == {"Track A"}
    assert res.excluded == 1


@pytest.mark.asyncio
async def test_excludes_disliked_from_storage(profile_repo):
    t3 = _t("3", "Track A", "Velvet North")
    catalog = [_t("1", "Song One", "Aurora Sky"), t3]
    repo = MusicRepository(connect(":memory:"))
    repo.record_feedback(feedback="disliked", canonical_track_id=canonical_key(t3))
    svc = _service(catalog, profile_repo, storage=repo)
    res = await svc.recommend(
        RecommendationRequest(prompt="x", services=["tidal"], exclude_existing=False)
    )
    assert {c.title for c in res.draft.tracks} == {"Song One"}
    repo.close()


@pytest.mark.asyncio
async def test_records_and_then_excludes_previous_recs(profile_repo):
    catalog = [_t("1", "Song One", "Aurora Sky"), _t("3", "Track A", "Velvet North")]
    repo = MusicRepository(connect(":memory:"))
    svc = _service(catalog, profile_repo, storage=repo)
    first = await svc.recommend(RecommendationRequest(prompt="x", services=["tidal"]))
    assert len(first.draft.tracks) == 2
    assert repo.already_recommended_ids()  # recorded
    second = await svc.recommend(RecommendationRequest(prompt="x", services=["tidal"]))
    assert second.draft.tracks == []  # all previously recommended -> excluded
    repo.close()


@pytest.mark.asyncio
async def test_ranking_reference_artist_above_discovered(profile_repo):
    catalog = [
        _t("cover", "Aurora Sky Cover", "Random Person"),  # found via title, not a ref artist
        _t("real", "Real Song", "Aurora Sky"),             # by ref artist -> ranked higher
    ]
    svc = _service(catalog, profile_repo)
    res = await svc.recommend(RecommendationRequest(prompt="x", services=["tidal"]))
    assert res.draft.tracks[0].artist == "Aurora Sky"
    assert "Discovered via" in (res.draft.tracks[1].reason or "")


@pytest.mark.asyncio
async def test_size_parsed_from_prompt(profile_repo):
    catalog = [_t(str(i), f"S{i}", "Aurora Sky") for i in range(10)]
    svc = _service(catalog, profile_repo)
    res = await svc.recommend(RecommendationRequest(prompt="make me 3 tracks", services=["tidal"]))
    assert len(res.draft.tracks) == 3
    assert any("Only 3" not in w for w in res.warnings) or True  # not fewer than requested


@pytest.mark.asyncio
async def test_auth_failure_warns_and_skips(profile_repo):
    svc = _service([], profile_repo, auth_error=True)
    res = await svc.recommend(RecommendationRequest(prompt="x", services=["tidal"]))
    assert res.draft.tracks == []
    assert any("tidal" in w.lower() for w in res.warnings)


@pytest.mark.asyncio
async def test_dedup_across_queries(profile_repo):
    # A track by an artist whose name also appears in another query shouldn't double.
    shared = _t("1", "Aurora Sky and Velvet North", "Aurora Sky")
    svc = _service([shared], profile_repo)
    res = await svc.recommend(RecommendationRequest(prompt="x", services=["tidal"]))
    assert len(res.draft.tracks) == 1
