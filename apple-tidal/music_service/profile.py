"""Phase 7 — the user music profile and its repository.

``MUSIC_PROFILE.md`` (repo root / user copy) holds standing preferences as a
fenced ``yaml`` block. ``MusicProfileRepository`` loads/saves it, and
``MusicProfile.merge`` layers per-request instructions on top so a single
recommendation request can steer the engine without editing the file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Union

import yaml
from pydantic import BaseModel, Field

# Ultimate fallback so the engine always has a baseline, even if the .md file is
# missing (e.g. installed as a wheel without package data).
DEFAULT_PROFILE_DATA: dict = {
    "preferences": {
        "vocals": [
            "unusual",
            "soulful",
            "emotionally intense",
            "dramatic",
            "deep female vocals",
            "expressive high male vocals",
        ],
        "sound": [
            "punchy bass",
            "wide soundstage",
            "cinematic atmosphere",
            "strong vocal presence",
        ],
        "avoid": [
            "generic pop vocals",
            "emotionally flat performances",
            "repetitive mainstream recommendations",
        ],
    },
    "reference_artists": [
        "Adam Naas",
        "Josephine Oniyama",
        "Benjamin Clementine",
        "London Grammar",
        "Michael Kiwanuka",
        "Anastasia Minster",
        "Black Pumas",
    ],
}

BUNDLED_PROFILE = Path(__file__).resolve().parent.parent / "MUSIC_PROFILE.md"
DEFAULT_USER_PROFILE = Path.home() / ".hermes" / "music_tools" / "MUSIC_PROFILE.md"

_YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


# --- models ----------------------------------------------------------------

class Preferences(BaseModel):
    vocals: list[str] = Field(default_factory=list)
    sound: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class MusicProfile(BaseModel):
    preferences: Preferences = Field(default_factory=Preferences)
    reference_artists: list[str] = Field(default_factory=list)
    # Optional per-artist mood tags, e.g. {"Woodkid": ["energy", "cinematic"]}.
    artist_moods: dict[str, list[str]] = Field(default_factory=dict)
    # Free-form, per-request steering text (not persisted to the baseline file).
    instructions: Optional[str] = None

    def merge(self, overrides: Union["MusicProfile", dict, None]) -> "MusicProfile":
        """Layer per-request overrides on top of this profile (lists unioned)."""
        if overrides is None:
            return self.model_copy(deep=True)
        ov = overrides if isinstance(overrides, MusicProfile) else _profile_from_overrides(overrides)
        merged_moods = {k: list(v) for k, v in self.artist_moods.items()}
        for artist, moods in ov.artist_moods.items():
            merged_moods[artist] = _union(merged_moods.get(artist, []), moods)
        return MusicProfile(
            preferences=Preferences(
                vocals=_union(self.preferences.vocals, ov.preferences.vocals),
                sound=_union(self.preferences.sound, ov.preferences.sound),
                avoid=_union(self.preferences.avoid, ov.preferences.avoid),
            ),
            reference_artists=_union(self.reference_artists, ov.reference_artists),
            artist_moods=merged_moods,
            instructions=ov.instructions or self.instructions,
        )

    def artists_for_mood(self, mood: str) -> list[str]:
        """Return artists tagged with ``mood`` (case-insensitive), order-preserved."""
        target = mood.casefold().strip()
        # Case-insensitive view of the mood map.
        moods_by_artist = {a.casefold(): (a, [m.casefold() for m in ms]) for a, ms in self.artist_moods.items()}
        out: list[str] = []
        # Reference artists first (preserve their order), then any extra tagged artist.
        for artist in self.reference_artists:
            entry = moods_by_artist.get(artist.casefold())
            if entry and target in entry[1]:
                out.append(artist)
        for artist, moods in self.artist_moods.items():
            if target in [m.casefold() for m in moods] and artist not in out:
                out.append(artist)
        return out

    def to_guidance_text(self) -> str:
        """Human-readable summary used to seed query generation / reasons (Phase 8)."""
        lines: list[str] = []
        if self.reference_artists:
            lines.append("Reference artists: " + ", ".join(self.reference_artists))
        if self.preferences.vocals:
            lines.append("Preferred vocals: " + ", ".join(self.preferences.vocals))
        if self.preferences.sound:
            lines.append("Preferred sound: " + ", ".join(self.preferences.sound))
        if self.preferences.avoid:
            lines.append("Avoid: " + ", ".join(self.preferences.avoid))
        if self.instructions:
            lines.append("Request instructions: " + self.instructions)
        return "\n".join(lines)


# --- parsing / rendering ----------------------------------------------------

def parse_profile_text(text: str) -> MusicProfile:
    """Parse a MUSIC_PROFILE.md (fenced yaml) or a plain YAML string."""
    match = _YAML_BLOCK.search(text or "")
    raw = match.group(1) if match else (text or "")
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return MusicProfile.model_validate(data)


def render_profile_markdown(profile: MusicProfile) -> str:
    block = yaml.safe_dump(
        {
            "preferences": {
                "vocals": profile.preferences.vocals,
                "sound": profile.preferences.sound,
                "avoid": profile.preferences.avoid,
            },
            "reference_artists": profile.reference_artists,
            **({"artist_moods": profile.artist_moods} if profile.artist_moods else {}),
        },
        sort_keys=False,
        allow_unicode=True,
    )
    return (
        "# Music profile\n\n"
        "Edit freely — the parser reads the fenced `yaml` block below.\n\n"
        f"```yaml\n{block}```\n"
    )


# --- repository -------------------------------------------------------------

class MusicProfileRepository:
    def __init__(
        self,
        path: Union[str, Path, None] = None,
        *,
        bundled: Union[str, Path] = BUNDLED_PROFILE,
    ) -> None:
        self.path = Path(path or os.environ.get("MUSIC_PROFILE_PATH") or DEFAULT_USER_PROFILE)
        self.bundled = Path(bundled)

    def load(self) -> MusicProfile:
        """Load the user profile; fall back to the bundled file, then to defaults."""
        for candidate in (self.path, self.bundled):
            if candidate.exists():
                return parse_profile_text(candidate.read_text(encoding="utf-8"))
        return MusicProfile.model_validate(DEFAULT_PROFILE_DATA)

    def ensure_user_profile(self) -> Path:
        """Create the editable user copy if it doesn't exist yet."""
        if self.path.exists():
            return self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.bundled.exists():
            self.path.write_text(self.bundled.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            self.save(MusicProfile.model_validate(DEFAULT_PROFILE_DATA))
        return self.path

    def save(self, profile: MusicProfile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(render_profile_markdown(profile), encoding="utf-8")

    def load_effective(self, overrides: Union[MusicProfile, dict, None] = None) -> MusicProfile:
        """Baseline profile merged with per-request overrides."""
        return self.load().merge(overrides)


# --- helpers ----------------------------------------------------------------

def _union(a: list[str], b: list[str]) -> list[str]:
    """Order-preserving, case-insensitive union (dedups within both inputs)."""
    out: list[str] = []
    seen: set[str] = set()
    for x in list(a) + list(b):
        if x.casefold() not in seen:
            seen.add(x.casefold())
            out.append(x)
    return out


def _profile_from_overrides(overrides: dict) -> MusicProfile:
    """Accept the nested profile shape, flat per-request keys, or a mix of both."""
    nested = overrides.get("preferences") or {}
    if not isinstance(nested, dict):
        nested = {}

    def _field(name: str) -> list[str]:
        return list(nested.get(name, [])) + list(overrides.get(name, []))

    data = {
        "preferences": {
            "vocals": _field("vocals"),
            "sound": _field("sound"),
            "avoid": _field("avoid"),
        },
        "reference_artists": list(overrides.get("reference_artists", [])),
        "artist_moods": dict(overrides.get("artist_moods", {})),
    }
    profile = MusicProfile.model_validate(data)
    profile.instructions = overrides.get("instructions") or overrides.get("prompt")
    return profile
