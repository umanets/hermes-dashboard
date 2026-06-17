"""Interactive TIDAL OAuth login (Authorization Code + PKCE).

Spins up a local HTTP server bound to the host/port of TIDAL_REDIRECT_URI,
opens the browser to the TIDAL login page, captures the ?code= on redirect,
exchanges it for tokens, and persists them.

The redirect URI MUST exactly match one registered for your TIDAL application
in the developer portal (default: http://127.0.0.1:8765/callback).
"""

from __future__ import annotations

import base64
import logging
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from ..config import TidalConfig, get_tidal_config
from .auth import (
    TidalTokenStore,
    build_authorize_url,
    exchange_code,
    generate_pkce_pair,
)

logger = logging.getLogger("hermes.music.tidal.login")


def _new_state() -> str:
    return base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode("ascii")


def run_login(
    config: Optional[TidalConfig] = None,
    *,
    open_browser: bool = True,
    store: Optional[TidalTokenStore] = None,
) -> str:
    config = config or get_tidal_config()
    store = store or TidalTokenStore()

    verifier, challenge = generate_pkce_pair()
    state = _new_state()
    parsed = urlparse(config.redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8765
    callback_path = parsed.path or "/callback"

    auth_url = build_authorize_url(
        client_id=config.client_id,
        redirect_uri=config.redirect_uri,
        scopes=config.scopes,
        code_challenge=challenge,
        state=state,
    )

    captured: dict[str, Optional[str]] = {"code": None, "error": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed_req = urlparse(self.path)
            if parsed_req.path != callback_path:
                self.send_response(404)
                self.end_headers()
                return
            qs = parse_qs(parsed_req.query)
            if qs.get("state", [None])[0] != state:
                captured["error"] = "state_mismatch"
            elif "error" in qs:
                captured["error"] = qs["error"][0]
            else:
                captured["code"] = qs.get("code", [None])[0]
            ok = captured["code"] and not captured["error"]
            msg = (
                "✅ TIDAL authorization complete. You can close this tab."
                if ok
                else f"❌ Authorization failed: {captured['error']}"
            )
            body = f"<html><body style='font-family:sans-serif'>{msg}</body></html>".encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            self.server.done = True  # type: ignore[attr-defined]

    httpd = HTTPServer((host, port), Handler)
    httpd.done = False  # type: ignore[attr-defined]
    print(f"Opening TIDAL login in your browser. If it doesn't open, visit:\n{auth_url}\n")
    if open_browser:
        webbrowser.open(auth_url)
    print(f"Waiting for redirect to {config.redirect_uri} ...")
    while not httpd.done:  # type: ignore[attr-defined]
        httpd.handle_request()
    httpd.server_close()

    if captured["error"] or not captured["code"]:
        raise RuntimeError(f"TIDAL login failed: {captured['error'] or 'no code returned'}")

    tokens = exchange_code(
        code=captured["code"],
        code_verifier=verifier,
        client_id=config.client_id,
        redirect_uri=config.redirect_uri,
        client_secret=config.client_secret,
    )
    store.save(tokens)
    granted = tokens.scope or "(none reported)"
    print(f"Tokens saved to {store.path}")
    print(f"Granted scopes: {granted}")
    if "playlists.write" not in (tokens.scope or ""):
        print(
            "NOTE: playlists.write was NOT granted — playlist creation will be "
            "unavailable for this application."
        )
    return str(store.path)
