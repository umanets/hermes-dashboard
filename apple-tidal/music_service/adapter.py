"""Phase 2 — the common adapter interface.

Every concrete service (Apple Music, TIDAL) implements ``MusicServiceAdapter``.
Capabilities that a given service/app cannot perform must raise
``CapabilityNotSupportedError`` — never return an empty result silently.

The ``capabilities`` mapping lets callers (and the music_get_accounts_status
tool) discover what is available without triggering an exception.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .errors import CapabilityNotSupportedError
from .models import Playlist, PlaylistDetails, Service, Track


class MusicServiceAdapter(ABC):
    """Abstract, async interface shared by all music services."""

    #: Concrete service id, set by subclasses.
    service: Service

    #: Coarse capability flags. Subclasses override what they actually support.
    capabilities: dict[str, bool] = {
        "search": False,
        "read_playlists": False,
        "read_library": False,
        "create_playlist": False,
        "add_tracks": False,
    }

    @abstractmethod
    async def authenticate(self) -> None:
        """Validate/refresh credentials. Raise AuthenticationRequiredError on failure."""

    @abstractmethod
    async def search_tracks(self, query: str, limit: int = 20) -> list[Track]:
        ...

    @abstractmethod
    async def get_playlists(self) -> list[Playlist]:
        ...

    @abstractmethod
    async def get_playlist(self, playlist_id: str) -> PlaylistDetails:
        ...

    @abstractmethod
    async def get_library_tracks(self, limit: Optional[int] = None) -> list[Track]:
        ...

    # ---- Write operations -------------------------------------------------
    # Default to "not supported" so an adapter that hasn't (yet) enabled writes
    # fails loudly and explicitly rather than silently.

    async def create_playlist(
        self, name: str, description: Optional[str] = None
    ) -> Playlist:
        raise CapabilityNotSupportedError(
            user_message=f"Creating playlists is not enabled for {self.service}.",
            service=self.service,
        )

    async def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        raise CapabilityNotSupportedError(
            user_message=f"Adding tracks is not enabled for {self.service}.",
            service=self.service,
        )

    async def add_to_library(self, track_ids: list[str]) -> None:
        """Add tracks to the user's library/favorites for this service."""
        raise CapabilityNotSupportedError(
            user_message=f"Adding to the library is not enabled for {self.service}.",
            service=self.service,
        )

    # ---- Helpers ----------------------------------------------------------

    def supports(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability, False))
