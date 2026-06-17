"""Unit tests for typed errors and adapter capability enforcement."""

import pytest

from music_service.adapter import MusicServiceAdapter
from music_service.errors import (
    AuthenticationRequiredError,
    CapabilityNotSupportedError,
    MusicServiceError,
    RateLimitError,
)
from music_service.models import Playlist, PlaylistDetails, Track


def test_error_hierarchy():
    for cls in (AuthenticationRequiredError, CapabilityNotSupportedError, RateLimitError):
        assert issubclass(cls, MusicServiceError)


def test_user_message_defaults_and_override():
    e = AuthenticationRequiredError()
    assert e.user_message == AuthenticationRequiredError.default_user_message

    e2 = RateLimitError(user_message="slow down", service="apple_music", retry_after=5.0)
    assert e2.user_message == "slow down"
    assert e2.to_dict()["retry_after"] == 5.0
    assert e2.to_dict()["service"] == "apple_music"


def test_to_dict_is_redaction_safe():
    e = AuthenticationRequiredError(user_message="token bad", service="apple_music")
    d = e.to_dict()
    assert set(d) == {"type", "message", "service"}
    assert d["type"] == "AuthenticationRequiredError"


class _StubAdapter(MusicServiceAdapter):
    """Minimal adapter that only implements reads, to test default write behaviour."""

    service = "apple_music"

    async def authenticate(self) -> None:  # pragma: no cover - trivial
        return None

    async def search_tracks(self, query, limit=20):
        return []

    async def get_playlists(self):
        return []

    async def get_playlist(self, playlist_id):
        return PlaylistDetails(playlist=Playlist(id=playlist_id, service="apple_music", name="x"))

    async def get_library_tracks(self, limit=None):
        return []


@pytest.mark.asyncio
async def test_unsupported_write_raises_not_silent():
    adapter = _StubAdapter()
    with pytest.raises(CapabilityNotSupportedError):
        await adapter.create_playlist("x")
    with pytest.raises(CapabilityNotSupportedError):
        await adapter.add_tracks("p1", ["t1"])


def test_supports_helper():
    adapter = _StubAdapter()
    assert adapter.supports("create_playlist") is False
