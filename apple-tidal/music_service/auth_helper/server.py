"""Minimal local server that drives the MusicKit JS authorize page.

Uses only the standard library (no extra deps). Flow:
  1. Generate a developer token from your MusicKit key.
  2. Serve apple_authorize.html with the token injected.
  3. Receive the Music User Token via POST /save-token and persist it.
"""

from __future__ import annotations

import json
import logging
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

from ..config import get_apple_config
from ..apple.auth import DeveloperTokenProvider, MusicUserTokenStore

logger = logging.getLogger("hermes.music.apple.authorize")

_HTML_PATH = Path(__file__).parent / "apple_authorize.html"


def _build_handler(developer_token: str, store: MusicUserTokenStore):
    class Handler(BaseHTTPRequestHandler):
        # Silence default noisy logging (and avoid leaking request lines).
        def log_message(self, *args) -> None:  # noqa: D401
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html", "/authorize"):
                html = _HTML_PATH.read_text(encoding="utf-8").replace(
                    "{{DEVELOPER_TOKEN}}", developer_token
                )
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/save-token":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                token = payload["music_user_token"]
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                self.send_response(400)
                self.end_headers()
                return
            store.save(token)
            logger.info("Music User Token saved to %s", store.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            # Signal the server loop to stop after responding.
            self.server.received_token = True  # type: ignore[attr-defined]

    return Handler


def run_authorize_server(
    *, host: str = "127.0.0.1", port: int = 8770, open_browser: bool = True
) -> Optional[str]:
    """Run the authorize flow until a token is captured. Returns the token path."""
    config = get_apple_config()
    provider = DeveloperTokenProvider(
        team_id=config.team_id,
        key_id=config.key_id,
        private_key=config.read_private_key(),
    )
    store = MusicUserTokenStore()
    handler = _build_handler(provider.get_token(), store)

    httpd = HTTPServer((host, port), handler)
    httpd.received_token = False  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    print(f"Open {url} and click 'Authorize Apple Music'.")
    if open_browser:
        webbrowser.open(url)
    while not httpd.received_token:  # type: ignore[attr-defined]
        httpd.handle_request()
    httpd.server_close()
    print(f"Music User Token saved to {store.path}")
    return str(store.path)
