"""Typed errors for the music service layer.

Adapters must raise a specific error rather than silently returning an empty
list for an unsupported capability. Every error carries a ``user_message`` that
is safe to show to the end user (no tokens, keys, or auth headers).
"""

from __future__ import annotations

from typing import Optional


class MusicServiceError(Exception):
    """Base class for all music-service errors.

    ``user_message`` is the redacted, human-facing string the agent should
    surface. ``service`` is the originating service id when known.
    """

    default_user_message = "A music service error occurred."

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        user_message: Optional[str] = None,
        service: Optional[str] = None,
    ) -> None:
        super().__init__(message or user_message or self.default_user_message)
        self.user_message = user_message or message or self.default_user_message
        self.service = service

    def to_dict(self) -> dict:
        """Structured, redaction-safe representation for tool JSON responses."""
        return {
            "type": type(self).__name__,
            "message": self.user_message,
            "service": self.service,
        }


class AuthenticationRequiredError(MusicServiceError):
    """Credentials are missing, invalid, or a token refresh failed."""

    default_user_message = "Authentication is required for this music service."


class CapabilityNotSupportedError(MusicServiceError):
    """The requested operation is not supported by this service/app registration."""

    default_user_message = "This operation is not supported by the music service."


class RateLimitError(MusicServiceError):
    """The service returned a rate-limit / throttling response (HTTP 429)."""

    default_user_message = "The music service rate limit was reached. Try again later."

    def __init__(self, *args, retry_after: Optional[float] = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["retry_after"] = self.retry_after
        return data


class TrackNotFoundError(MusicServiceError):
    """A referenced track/playlist could not be found."""

    default_user_message = "The requested item was not found."


class ConfirmationRequiredError(MusicServiceError):
    """A write operation was requested without explicit confirmation."""

    default_user_message = (
        "This operation modifies your library and requires confirmation "
        "(pass confirmed=true after reviewing the preview)."
    )

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["requires_confirmation"] = True
        return data
