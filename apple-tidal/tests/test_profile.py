"""Unit tests for Phase 7 — music profile + repository."""

import pytest

from music_service.profile import (
    BUNDLED_PROFILE,
    DEFAULT_PROFILE_DATA,
    MusicProfile,
    MusicProfileRepository,
    parse_profile_text,
    render_profile_markdown,
)


def test_bundled_profile_parses_and_has_reference_artists():
    assert BUNDLED_PROFILE.exists()
    profile = parse_profile_text(BUNDLED_PROFILE.read_text(encoding="utf-8"))
    assert "London Grammar" in profile.reference_artists
    assert "soulful" in profile.preferences.vocals
    assert "punchy bass" in profile.preferences.sound
    assert "generic pop vocals" in profile.preferences.avoid


def test_parse_plain_yaml_without_fence():
    text = "reference_artists:\n  - A\n  - B\npreferences:\n  vocals:\n    - soft\n"
    p = parse_profile_text(text)
    assert p.reference_artists == ["A", "B"]
    assert p.preferences.vocals == ["soft"]


def test_parse_empty_and_garbage_is_safe():
    assert parse_profile_text("").reference_artists == []
    assert parse_profile_text("not: [valid").reference_artists == []  # yaml error -> empty


def test_merge_unions_case_insensitively():
    base = MusicProfile.model_validate(DEFAULT_PROFILE_DATA)
    merged = base.merge(
        {
            "reference_artists": ["london grammar", "Radiohead"],  # dup (diff case) + new
            "vocals": ["soulful", "haunting"],
            "avoid": ["autotune"],
            "instructions": "darker, slower",
        }
    )
    # No case-insensitive duplicate of London Grammar
    lg = [a for a in merged.reference_artists if a.casefold() == "london grammar"]
    assert len(lg) == 1
    assert "Radiohead" in merged.reference_artists
    assert "haunting" in merged.preferences.vocals
    assert "autotune" in merged.preferences.avoid
    assert merged.instructions == "darker, slower"
    # baseline untouched
    assert base.instructions is None


def test_merge_accepts_nested_profile_shape():
    base = MusicProfile(reference_artists=["A"])
    merged = base.merge({"preferences": {"sound": ["warm"]}, "reference_artists": ["B"]})
    assert merged.reference_artists == ["A", "B"]
    assert merged.preferences.sound == ["warm"]


def test_merge_none_returns_copy():
    base = MusicProfile(reference_artists=["A"])
    merged = base.merge(None)
    assert merged.reference_artists == ["A"]
    assert merged is not base


def test_to_guidance_text_includes_sections():
    p = MusicProfile.model_validate(DEFAULT_PROFILE_DATA)
    p.instructions = "more cinematic"
    text = p.to_guidance_text()
    assert "Reference artists:" in text
    assert "Avoid:" in text
    assert "more cinematic" in text


def test_repository_load_from_path(tmp_path):
    f = tmp_path / "MUSIC_PROFILE.md"
    f.write_text("```yaml\nreference_artists:\n  - Solo\n```", encoding="utf-8")
    repo = MusicProfileRepository(path=f)
    assert repo.load().reference_artists == ["Solo"]


def test_repository_falls_back_to_bundled(tmp_path):
    missing = tmp_path / "nope.md"
    repo = MusicProfileRepository(path=missing)  # bundled default still resolves
    assert "London Grammar" in repo.load().reference_artists


def test_repository_ensure_user_profile_creates_copy(tmp_path):
    target = tmp_path / "sub" / "MUSIC_PROFILE.md"
    repo = MusicProfileRepository(path=target)
    assert not target.exists()
    created = repo.ensure_user_profile()
    assert created.exists()
    assert "London Grammar" in repo.load().reference_artists


def test_repository_save_roundtrip(tmp_path):
    f = tmp_path / "MUSIC_PROFILE.md"
    repo = MusicProfileRepository(path=f)
    profile = MusicProfile(reference_artists=["X", "Y"])
    profile.preferences.vocals = ["airy"]
    repo.save(profile)
    reloaded = repo.load()
    assert reloaded.reference_artists == ["X", "Y"]
    assert reloaded.preferences.vocals == ["airy"]


def test_load_effective_merges_overrides(tmp_path):
    f = tmp_path / "MUSIC_PROFILE.md"
    f.write_text("```yaml\nreference_artists:\n  - Base\n```", encoding="utf-8")
    repo = MusicProfileRepository(path=f)
    eff = repo.load_effective({"reference_artists": ["Extra"], "prompt": "moody"})
    assert eff.reference_artists == ["Base", "Extra"]
    assert eff.instructions == "moody"


def test_render_markdown_contains_yaml_block():
    md = render_profile_markdown(MusicProfile.model_validate(DEFAULT_PROFILE_DATA))
    assert "```yaml" in md
    assert "reference_artists" in md
