"""Configuration loading. Secrets come from environment / .env only.

No secret is ever hardcoded. The private key is read from a file path; its
contents are never logged. We intentionally read the same ``~/.hermes/.env``
that Hermes uses, plus a local ``.env`` for standalone runs, so the MCP server
works identically whether launched by Hermes or from a shell.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .errors import AuthenticationRequiredError

# Repo root (parent of the music_service package) — used to resolve relative
# paths (e.g. a bare APPLE_PRIVATE_KEY_PATH filename) no matter the cwd Hermes
# launches the server from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_path(raw: str) -> Path:
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    for base in (Path.cwd(), PROJECT_ROOT):
        candidate = base / p
        if candidate.exists():
            return candidate
    # Fall back to project-root so the not-found error names a stable location.
    return PROJECT_ROOT / p


def load_env() -> None:
    """Load env files without overriding already-set process env vars.

    Order (first found wins for a given key, because override=False):
      1. process environment (highest priority; set by Hermes)
      2. ./.env in the current working directory
      3. the .env next to this project (so the MCP server finds creds no matter
         what working directory Hermes launches it from)
      4. ~/.hermes/.env (Hermes' own secret store)
    """
    load_dotenv(Path.cwd() / ".env", override=False)
    # Project-root .env (parent of the music_service package).
    project_env = Path(__file__).resolve().parent.parent / ".env"
    if project_env.exists():
        load_dotenv(project_env, override=False)
    hermes_env = Path.home() / ".hermes" / ".env"
    if hermes_env.exists():
        load_dotenv(hermes_env, override=False)
    # Windows: Hermes stores its env under %LOCALAPPDATA%\hermes\.env
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        alt = Path(localappdata) / "hermes" / ".env"
        if alt.exists():
            load_dotenv(alt, override=False)


@dataclass(frozen=True)
class AppleConfig:
    team_id: str
    key_id: str
    private_key_path: Path
    storefront: str = "us"
    music_user_token: Optional[str] = None

    def read_private_key(self) -> str:
        if not self.private_key_path.exists():
            raise AuthenticationRequiredError(
                user_message=(
                    "Apple Music private key file was not found. "
                    "Set APPLE_PRIVATE_KEY_PATH to your MusicKit .p8 file."
                ),
                service="apple_music",
            )
        return self.private_key_path.read_text(encoding="utf-8")


# Scopes requested during the TIDAL OAuth flow. Read scopes are always needed;
# write scopes are only honoured if the registered app was granted them.
DEFAULT_TIDAL_SCOPES = (
    "user.read",
    "collection.read",
    "playlists.read",
    "search.read",
    "collection.write",
    "playlists.write",
)


@dataclass(frozen=True)
class TidalConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    country: str = "US"
    scopes: tuple[str, ...] = DEFAULT_TIDAL_SCOPES


def _require(name: str, *, service: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise AuthenticationRequiredError(
            user_message=f"{service} configuration is missing: set {name}.",
            service=service.lower().replace(" ", "_"),
        )
    return value


def get_apple_config() -> AppleConfig:
    """Build AppleConfig from env. Raises AuthenticationRequiredError if incomplete.

    Note: a valid config does NOT imply API access — the Team ID / Key ID /
    private key must belong to an Apple Developer Program membership, and the
    Music User Token requires an active Apple Music subscription.
    """
    load_env()
    return AppleConfig(
        team_id=_require("APPLE_TEAM_ID", service="Apple Music"),
        key_id=_require("APPLE_KEY_ID", service="Apple Music"),
        private_key_path=_resolve_path(_require("APPLE_PRIVATE_KEY_PATH", service="Apple Music")),
        storefront=os.environ.get("APPLE_MUSIC_STOREFRONT", "us"),
        music_user_token=os.environ.get("APPLE_MUSIC_USER_TOKEN"),
    )


def get_tidal_config() -> TidalConfig:
    load_env()
    scopes_env = os.environ.get("TIDAL_SCOPES")
    scopes = tuple(scopes_env.split()) if scopes_env else DEFAULT_TIDAL_SCOPES
    return TidalConfig(
        client_id=_require("TIDAL_CLIENT_ID", service="TIDAL"),
        client_secret=_require("TIDAL_CLIENT_SECRET", service="TIDAL"),
        redirect_uri=os.environ.get(
            "TIDAL_REDIRECT_URI", "http://127.0.0.1:8765/callback"
        ),
        country=os.environ.get("TIDAL_COUNTRY", "US"),
        scopes=scopes,
    )
