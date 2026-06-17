"""TIDAL OAuth2 (Authorization Code + PKCE) against the official endpoints.

  authorize:  https://login.tidal.com/authorize
  token:      https://auth.tidal.com/v1/oauth2/token

Concerns kept separate (mirrors the Apple split):
  * PKCE pair generation                -> generate_pkce_pair / build_authorize_url
  * code -> token exchange + refresh    -> exchange_code / refresh_tokens
  * token persistence + expiry          -> TidalTokenStore / TidalTokens

Tokens (access + refresh) are user credentials, stored in a local JSON file
(chmod 600 on POSIX), never logged, never in SQLite or source control.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx

from ..errors import AuthenticationRequiredError, MusicServiceError

AUTHORIZE_URL = "https://login.tidal.com/authorize"
TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"

# Refresh a little before the real expiry to avoid races.
REFRESH_SKEW_SECONDS = 60


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_pkce_pair(*, verifier_bytes: Optional[bytes] = None) -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256.

    ``verifier_bytes`` is injectable for deterministic tests; otherwise random.
    """
    raw = verifier_bytes if verifier_bytes is not None else os.urandom(64)
    verifier = _b64url(raw)  # 43..128 chars, unreserved
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: tuple[str, ...] | list[str],
    code_challenge: str,
    state: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


@dataclass
class TidalTokens:
    access_token: str
    refresh_token: Optional[str]
    expires_at: float
    scope: str = ""
    user_id: Optional[str] = None

    def is_expired(self, *, now: Optional[float] = None) -> bool:
        current = now if now is not None else time.time()
        return current >= (self.expires_at - REFRESH_SKEW_SECONDS)


def _tokens_from_response(data: dict, *, previous: Optional[TidalTokens] = None) -> TidalTokens:
    expires_in = float(data.get("expires_in", 0))
    # A refresh response may omit refresh_token — keep the previous one.
    refresh = data.get("refresh_token") or (previous.refresh_token if previous else None)
    user_id = data.get("user_id")
    if user_id is None and isinstance(data.get("user"), dict):
        user_id = data["user"].get("userId") or data["user"].get("id")
    if user_id is None and previous is not None:
        user_id = previous.user_id
    return TidalTokens(
        access_token=data["access_token"],
        refresh_token=refresh,
        expires_at=time.time() + expires_in,
        scope=data.get("scope", previous.scope if previous else ""),
        user_id=str(user_id) if user_id is not None else None,
    )


def _post_token(form: dict, client_secret: Optional[str]) -> dict:
    # client_secret (if the app is confidential) is sent via HTTP Basic auth.
    auth = (form["client_id"], client_secret) if client_secret else None
    try:
        resp = httpx.post(TOKEN_URL, data=form, auth=auth, timeout=30.0)
    except httpx.HTTPError as exc:
        raise MusicServiceError(
            user_message="Could not reach the TIDAL auth service.", service="tidal"
        ) from exc
    if resp.status_code != 200:
        # Body may contain error description; do NOT echo tokens/secrets.
        detail = ""
        try:
            detail = resp.json().get("error_description") or resp.json().get("error", "")
        except Exception:
            pass
        raise AuthenticationRequiredError(
            user_message=f"TIDAL token request failed (HTTP {resp.status_code}). {detail}".strip(),
            service="tidal",
        )
    return resp.json()


def exchange_code(
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
    client_secret: Optional[str] = None,
) -> TidalTokens:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    return _tokens_from_response(_post_token(form, client_secret))


def refresh_tokens(
    *,
    tokens: TidalTokens,
    client_id: str,
    client_secret: Optional[str] = None,
) -> TidalTokens:
    if not tokens.refresh_token:
        raise AuthenticationRequiredError(
            user_message="The TIDAL session has no refresh token; please log in again.",
            service="tidal",
        )
    form = {
        "grant_type": "refresh_token",
        "refresh_token": tokens.refresh_token,
        "client_id": client_id,
    }
    return _tokens_from_response(_post_token(form, client_secret), previous=tokens)


class TidalTokenStore:
    """Persist TidalTokens to a local JSON file."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (Path.home() / ".hermes" / "music_tools" / "tidal_tokens.json")

    def load(self) -> Optional[TidalTokens]:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return TidalTokens(**data)
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    def save(self, tokens: TidalTokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(tokens), indent=2), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except (OSError, NotImplementedError):
            pass
