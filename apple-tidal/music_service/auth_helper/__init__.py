"""Local MusicKit JS authorization helper.

Serves a one-page web app that runs MusicKit JS, signs the user in to Apple
Music, and posts the resulting Music User Token back to this local server, which
saves it via ``MusicUserTokenStore``. Used because the MUT can only be obtained
in a browser with an active subscription — it cannot be generated server-side.
"""

from .server import run_authorize_server

__all__ = ["run_authorize_server"]
