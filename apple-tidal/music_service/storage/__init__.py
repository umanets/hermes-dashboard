"""Phase 5 — local SQLite storage.

A lightweight persistence layer for caching tracks, playlist snapshots,
cross-service track mappings, and recommendation history/feedback.

Security: this database NEVER stores private keys, client secrets, or access /
refresh tokens. Credentials live in protected config/JSON files (see
``apple/auth.py`` and ``tidal/auth.py``). ``service_accounts`` keeps only
non-secret metadata (user id, country, granted scope names, timestamps).
"""

from .db import DEFAULT_DB_PATH, connect
from .repository import FEEDBACK_VALUES, MusicRepository

__all__ = ["connect", "DEFAULT_DB_PATH", "MusicRepository", "FEEDBACK_VALUES"]
