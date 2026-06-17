"""Tests for free-text track resolution + the hybrid mood base."""

import pytest

from music_service.adapter import MusicServiceAdapter
from music_service.app import MusicToolsService
from music_service.models import Track
from music_service.profile import MusicProfile, MusicProfileRepository
from music_service.resolve import CandidateResolver, parse_item
from music_service.storage import MusicRepository, connect


def _t(tid, title, artist, service="apple_music", **kw):
    return Track(id=tid, service=service, title=title, artist=artist, **kw)


class FakeAdapter(MusicServiceAdapter):
    capabilities = {"search": True, "read_library": True, "create_playlist": True, "add_tracks": True}

    def __init__(self, service, *, catalog=None, library=None):
        self.service = service
        self.catalog = catalog or []
        self.library = library or []

    async def authenticate(self): pass
    async def search_tracks(self, query, limit=20):
        q = query.lower()
        return [t for t in self.catalog if q in (t.artist + " " + t.title).lower()][:limit]
    async def get_library_tracks(self, limit=None): return list(self.library)
    async def get_playlists(self): return []
    async def get_playlist(self, pid): raise NotImplementedError


# ---- parse_item -----------------------------------------------------------

@pytest.mark.parametrize(
    "item,expected",
    [
        ("Woodkid - Run Boy Run", ("Woodkid", "Run Boy Run")),
        ("The Prodigy – Breathe", ("The Prodigy", "Breathe")),
        ("Justice by Genesis", ("Justice", "Genesis")),
        ("Just A Title", ("", "Just A Title")),
    ],
)
def test_parse_item_strings(item, expected):
    artist, title, _, _ = parse_item(item)
    assert (artist, title) == expected


def test_parse_item_dict():
    a, t, isrc, reason = parse_item({"artist": "M83", "title": "Outro", "isrc": "X1", "reason": "epic"})
    assert (a, t, isrc, reason) == ("M83", "Outro", "X1", "epic")


# ---- resolver -------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolver_matches_and_reports_unmatched():
    catalog = [
        _t("a1", "Run Boy Run", "Woodkid"),
        _t("a2", "Breathe", "The Prodigy"),
    ]
    resolver = CandidateResolver({"apple_music": FakeAdapter("apple_music", catalog=catalog)})
    items = ["Woodkid - Run Boy Run", "The Prodigy - Breathe", "Nonexistent - Ghost"]
    candidates, report = await resolver.resolve(items, "apple_music")
    assert {c.title for c in candidates} == {"Run Boy Run", "Breathe"}
    assert all(c.apple_music_id for c in candidates)
    assert len(report.unmatched) == 1 and report.unmatched[0]["title"] == "Ghost"


@pytest.mark.asyncio
async def test_resolver_excludes_existing_library():
    existing = _t("a1", "Run Boy Run", "Woodkid")
    resolver = CandidateResolver(
        {"apple_music": FakeAdapter("apple_music", catalog=[existing], library=[existing])}
    )
    candidates, report = await resolver.resolve(
        ["Woodkid - Run Boy Run"], "apple_music", exclude_existing=True
    )
    assert candidates == []
    assert report.excluded == 1


@pytest.mark.asyncio
async def test_resolver_does_not_substitute_versions():
    catalog = [_t("a1", "Run Boy Run (Live)", "Woodkid")]
    resolver = CandidateResolver({"apple_music": FakeAdapter("apple_music", catalog=catalog)})
    candidates, report = await resolver.resolve(["Woodkid - Run Boy Run"], "apple_music")
    assert candidates == []
    assert len(report.unmatched) == 1


# ---- profile mood tags ----------------------------------------------------

def test_profile_artists_for_mood():
    p = MusicProfile(
        reference_artists=["Woodkid", "Adam Naas", "M83"],
        artist_moods={"Woodkid": ["energy", "cinematic"], "M83": ["Energy"], "Adam Naas": ["soulful"]},
    )
    assert p.artists_for_mood("energy") == ["Woodkid", "M83"]
    assert p.artists_for_mood("soulful") == ["Adam Naas"]


def test_profile_mood_merge_unions():
    base = MusicProfile(artist_moods={"Woodkid": ["energy"]})
    merged = base.merge({"artist_moods": {"Woodkid": ["cinematic"], "Justice": ["energy"]}})
    assert set(merged.artist_moods["Woodkid"]) == {"energy", "cinematic"}
    assert merged.artist_moods["Justice"] == ["energy"]


def test_profile_mood_tags_roundtrip_markdown(tmp_path):
    f = tmp_path / "MUSIC_PROFILE.md"
    repo = MusicProfileRepository(path=f)
    repo.save(MusicProfile(reference_artists=["Woodkid"], artist_moods={"Woodkid": ["energy"]}))
    assert repo.load().artists_for_mood("energy") == ["Woodkid"]


# ---- storage mood cache ---------------------------------------------------

def test_storage_artist_moods_roundtrip():
    repo = MusicRepository(connect(":memory:"))
    repo.upsert_artist_moods("Rammstein", ["Energy", "heavy"], source="agent")
    assert set(repo.get_artist_moods("rammstein")) == {"energy", "heavy"}  # normalized lookup
    assert "Rammstein" in repo.get_artists_for_mood("energy")
    assert repo.get_artists_for_mood("calm") == []
    repo.close()


# ---- app facade: hybrid seed + resolve + save -----------------------------

def _app(adapters, storage, profile_repo):
    return MusicToolsService(adapter_factory=lambda s: adapters[s], storage=storage, profile_repo=profile_repo)


def test_app_get_seed_artists_unions_profile_and_cache(tmp_path):
    f = tmp_path / "MUSIC_PROFILE.md"
    f.write_text(
        "```yaml\nreference_artists:\n  - Woodkid\nartist_moods:\n  Woodkid:\n    - energy\n```",
        encoding="utf-8",
    )
    repo = MusicRepository(connect(":memory:"))
    repo.upsert_artist_moods("The Prodigy", ["energy"])
    app = _app({}, repo, MusicProfileRepository(path=f))
    res = app.get_seed_artists("energy")
    assert "Woodkid" in res["artists"] and "The Prodigy" in res["artists"]
    repo.close()


def test_app_save_artist_moods_then_seed(tmp_path):
    repo = MusicRepository(connect(":memory:"))
    app = _app({}, repo, MusicProfileRepository(path=tmp_path / "p.md"))
    app.save_artist_moods({"Justice": ["energy", "electronic"], "M83": "energy"})
    assert set(app.get_seed_artists("energy")["artists"]) >= {"Justice", "M83"}
    repo.close()


@pytest.mark.asyncio
async def test_app_get_library_artists_counts_and_orders(tmp_path):
    lib = [
        _t("1", "A", "Black Pumas"), _t("2", "B", "Black Pumas"),
        _t("3", "C", "London Grammar"), _t("4", "D", "black pumas"),  # case-insensitive merge
    ]
    repo = MusicRepository(connect(":memory:"))
    app = _app({"apple_music": FakeAdapter("apple_music", library=lib)}, repo, MusicProfileRepository(path=tmp_path / "p.md"))
    res = await app.get_library_artists("apple_music")
    assert res["distinct_artists"] == 2
    assert res["artists"][0] == {"artist": "Black Pumas", "tracks": 3}  # most tracks first
    repo.close()


@pytest.mark.asyncio
async def test_app_resolve_tracks_persists_draft(tmp_path):
    catalog = [_t("a1", "Run Boy Run", "Woodkid")]
    repo = MusicRepository(connect(":memory:"))
    app = _app({"apple_music": FakeAdapter("apple_music", catalog=catalog)}, repo, MusicProfileRepository(path=tmp_path / "p.md"))
    res = await app.resolve_tracks(["Woodkid - Run Boy Run", "X - Y"], "apple_music", name="Energy")
    assert res["name"] == "Energy"
    assert len(res["tracks"]) == 1
    assert len(res["unmatched_tracks"]) == 1
    # draft persisted and previewable
    assert app.preview_playlist(res["draft_id"])["tracks"][0]["title"] == "Run Boy Run"
    repo.close()
