"""Apple Music authentication.

Four concerns are kept deliberately separate (per the brief):

1. Developer token generation  -> ``generate_developer_token`` / ``DeveloperTokenProvider``
   A short-lived ES256 JWT signed with your MusicKit private key. Required on
   EVERY Apple Music API request as ``Authorization: Bearer <token>``.

2. Music User Token acquisition -> obtained in the browser via MusicKit JS
   (see ``music_service/auth_helper``). Not generatable server-side.

3. Token persistence            -> ``MusicUserTokenStore`` (local JSON file).

4. Expiration handling          -> the developer token caches until shortly
   before ``exp`` and regenerates; the Music User Token does not expire on a
   fixed schedule but can be revoked, in which case the API returns 401 and we
   raise AuthenticationRequiredError.

Secrets (the private key, the tokens) are NEVER logged.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import jwt  # PyJWT

from ..errors import AuthenticationRequiredError

# Apple requires the developer token lifetime to be <= ~6 months. We default to
# 150 days and refresh well before expiry.
DEFAULT_TTL_SECONDS = 150 * 24 * 60 * 60
MAX_TTL_SECONDS = 15777000  # Apple's documented hard maximum (~182.5 days)
REFRESH_SKEW_SECONDS = 24 * 60 * 60  # regenerate a day early


def generate_developer_token(
    *,
    team_id: str,
    key_id: str,
    private_key: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    issued_at: Optional[int] = None,
) -> str:
    """Create a signed Apple Music developer token (ES256 JWT).

    Header:  alg=ES256, kid=<key_id>
    Payload: iss=<team_id>, iat, exp
    """
    if ttl_seconds > MAX_TTL_SECONDS:
        raise ValueError(
            f"ttl_seconds must be <= {MAX_TTL_SECONDS} (Apple's ~6 month limit)."
        )
    iat = int(issued_at if issued_at is not None else time.time())
    payload = {"iss": team_id, "iat": iat, "exp": iat + ttl_seconds}
    headers = {"alg": "ES256", "kid": key_id}
    try:
        return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
    except Exception as exc:  # invalid/unsupported key material
        # Do not include the key contents in the message.
        raise AuthenticationRequiredError(
            user_message=(
                "Failed to sign the Apple Music developer token. Check that "
                "APPLE_PRIVATE_KEY_PATH points to a valid MusicKit .p8 key."
            ),
            service="apple_music",
        ) from exc


@dataclass
class DeveloperTokenProvider:
    """Caches a developer token and regenerates it before expiry."""

    team_id: str
    key_id: str
    private_key: str
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    _token: Optional[str] = None
    _exp: int = 0

    def get_token(self, *, now: Optional[int] = None) -> str:
        current = int(now if now is not None else time.time())
        if self._token is None or current >= (self._exp - REFRESH_SKEW_SECONDS):
            self._token = generate_developer_token(
                team_id=self.team_id,
                key_id=self.key_id,
                private_key=self.private_key,
                ttl_seconds=self.ttl_seconds,
                issued_at=current,
            )
            self._exp = current + self.ttl_seconds
        return self._token


class MusicUserTokenStore:
    """Persist the Music User Token to a local JSON file (not SQLite).

    The MUT is a user credential, so it lives in a protected local config file,
    consistent with the brief's credential-storage guidance.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (Path.home() / ".hermes" / "music_tools" / "apple_tokens.json")

    def load(self) -> Optional[str]:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        token = data.get("music_user_token")
        return token or None

    def save(self, music_user_token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"music_user_token": music_user_token}, indent=2),
            encoding="utf-8",
        )
        try:  # best-effort tighten permissions on POSIX
            self.path.chmod(0o600)
        except (OSError, NotImplementedError):
            pass

    def resolve(self, env_token: Optional[str]) -> Optional[str]:
        """Prefer an explicit env token, fall back to the persisted one."""
        return env_token or self.load()
