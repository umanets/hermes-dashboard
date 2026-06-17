"""Resolve a service id to a concrete adapter.

Keeps the construction of adapters in one place so tools/CLI stay thin. TIDAL is
registered in a later phase; requesting it now yields a clear error.
"""

from __future__ import annotations

from .adapter import MusicServiceAdapter
from .errors import CapabilityNotSupportedError
from .models import Service

SUPPORTED_SERVICES: tuple[str, ...] = ("apple_music", "tidal")


def get_adapter(service: str) -> MusicServiceAdapter:
    if service == "apple_music":
        from .apple import AppleMusicAdapter

        return AppleMusicAdapter()
    if service == "tidal":
        from .tidal import TidalAdapter

        return TidalAdapter()
    raise CapabilityNotSupportedError(
        user_message=(
            f"Unknown service '{service}'. Expected one of: {', '.join(SUPPORTED_SERVICES)}."
        ),
        service=service,
    )
