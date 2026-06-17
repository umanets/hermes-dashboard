"""Unit tests for favorites sync (Apple library ↔ TIDAL collection)."""

import pytest

from music_service.adapter import MusicServiceAdapter
from music_service.favorites_sync import FavoritesSyncService
from music_service.models import Track
from music_service.storage import MusicRepository, connect


def _t(tid, title, artist, service, **kw):
    return Track(id=tid, service=service, title=title, artist=artist, **kw)


class FakeAdapter(MusicServiceAdapter):
    capabilities = {"read_library": True, "search": True, "add_to_library": True}

    def __init__(self, service, *, library=None, catalog=None):
        self.service = service
        self.library = library or []
        self.catalog = catalog or []
        self.added: list[str] = []
        self.search_calls = 0

    async def authenticate(self):
        pass

    async def get_library_tracks(self, limit=None):
        return list(self.library)

    async def search_tracks(self, query, limit=20):
        self.search_calls += 1
        q = query.lower()
        return [t for t in self.catalog if q in (t.artist + " " + t.title).lower()][:limit]

    async def add_to_library(self, track_ids):
        self.added.extend(track_ids)

    # unused abstract methods
    async def get_playlists(self): return []
    async def get_playlist(self, pid): raise NotImplementedError


def _setup():
    # Song A is in both (apple lib has no ISRC, tidal has ISRC -> normalized match).
    apple = FakeAdapter(
        "apple_music",
        library=[
            _t("a_A", "Song A", "Artist X", "apple_music"),
            _t("a_B", "Song B", "Artist Y", "apple_music"),  # missing on tidal
        ],
        catalog=[_t("cat_C", "Song C", "Artist Z", "apple_music", isrc="I3")],  # for resolving from tidal
    )
    tidal = FakeAdapter(
        "tidal",
        library=[
            _t("t_A", "Song A", "Artist X", "tidal", isrc="I1"),
            _t("t_C", "Song C", "Artist Z", "tidal", isrc="I3"),  # missing on apple
        ],
        catalog=[_t("cat_B", "Song B", "Artist Y", "tidal")],  # for resolving from apple
    )
    return apple, tidal


@pytest.mark.asyncio
async def test_dry_run_reports_both_directions_no_writes():
    apple, tidal = _setup()
    svc = FavoritesSyncService({"apple_music": apple, "tidal": tidal})
    report = await svc.sync(direction="both", confirmed=False)
    d = report.to_dict()
    assert d["already_in_sync"] == 1            # Song A on both sides
    assert d["to_apple"]["to_add"] == 1         # Song C -> Apple
    assert d["to_apple"]["matched"][0]["destination_id"] == "cat_C"
    assert d["to_tidal"]["to_add"] == 1         # Song B -> TIDAL
    assert d["to_tidal"]["matched"][0]["destination_id"] == "cat_B"
    assert any("Preview only" in w for w in d["warnings"])
    assert apple.added == [] and tidal.added == []


@pytest.mark.asyncio
async def test_confirmed_writes_both_sides():
    apple, tidal = _setup()
    svc = FavoritesSyncService({"apple_music": apple, "tidal": tidal})
    report = await svc.sync(direction="both", confirmed=True)
    assert apple.added == ["cat_C"]
    assert tidal.added == ["cat_B"]
    assert report.added_to_apple == 1
    assert report.added_to_tidal == 1


@pytest.mark.asyncio
async def test_direction_to_apple_only():
    apple, tidal = _setup()
    svc = FavoritesSyncService({"apple_music": apple, "tidal": tidal})
    report = await svc.sync(direction="to_apple", confirmed=True)
    assert apple.added == ["cat_C"]
    assert tidal.added == []  # not touched


@pytest.mark.asyncio
async def test_version_variant_not_synced():
    apple = FakeAdapter("apple_music", library=[], catalog=[_t("cat_orig", "Song D", "Artist Q", "apple_music")])
    tidal = FakeAdapter("tidal", library=[_t("t_live", "Song D (Live)", "Artist Q", "tidal")], catalog=[])
    svc = FavoritesSyncService({"apple_music": apple, "tidal": tidal})
    report = await svc.sync(direction="to_apple", confirmed=True)
    # Live track must not be matched to the studio original.
    assert apple.added == []
    assert len(report.to_apple_unmatched) == 1


@pytest.mark.asyncio
async def test_cache_makes_second_run_search_free():
    apple, tidal = _setup()
    repo = MusicRepository(connect(":memory:"))
    svc = FavoritesSyncService({"apple_music": apple, "tidal": tidal}, storage=repo)

    await svc.sync(direction="both", confirmed=True)
    first_searches = apple.search_calls + tidal.search_calls
    assert first_searches > 0  # had to resolve via search the first time

    # Reset state but keep the (now-populated) mapping cache + the just-added items.
    apple2, tidal2 = _setup()
    svc2 = FavoritesSyncService({"apple_music": apple2, "tidal": tidal2}, storage=repo)
    await svc2.sync(direction="both", confirmed=False)
    assert apple2.search_calls == 0 and tidal2.search_calls == 0  # all from cache
    repo.close()


@pytest.mark.asyncio
async def test_cap_defers_excess():
    apple = FakeAdapter("apple_music", library=[], catalog=[
        _t("c1", "S1", "A", "apple_music"), _t("c2", "S2", "B", "apple_music"),
    ])
    tidal = FakeAdapter("tidal", library=[
        _t("t1", "S1", "A", "tidal"), _t("t2", "S2", "B", "tidal"),
    ], catalog=[])
    svc = FavoritesSyncService({"apple_music": apple, "tidal": tidal}, max_per_direction=1)
    report = await svc.sync(direction="to_apple", confirmed=True)
    assert len(apple.added) == 1
    assert any("deferred" in w for w in report.warnings)
