"""SQLite connection + schema management.

Schema is created idempotently and versioned via ``PRAGMA user_version`` so we
can add migrations later without breaking existing databases.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path.home() / ".hermes" / "music_tools" / "music_tools.db"

SCHEMA = """
-- Non-secret per-service account metadata. NO tokens or secrets here.
CREATE TABLE IF NOT EXISTS service_accounts (
    service       TEXT PRIMARY KEY,          -- apple_music | tidal
    user_id       TEXT,
    country       TEXT,
    scopes        TEXT,                       -- space-separated scope names only
    connected_at  TEXT,
    updated_at    TEXT
);

-- Cache of tracks seen on any service.
CREATE TABLE IF NOT EXISTS tracks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    service           TEXT NOT NULL,
    service_track_id  TEXT NOT NULL,
    title             TEXT,
    artist            TEXT,
    album             TEXT,
    duration_ms       INTEGER,
    isrc              TEXT,
    explicit          INTEGER,                -- 0/1/NULL
    url               TEXT,
    normalized_artist TEXT,
    normalized_title  TEXT,
    canonical_key     TEXT,
    updated_at        TEXT,
    UNIQUE(service, service_track_id)
);
CREATE INDEX IF NOT EXISTS idx_tracks_isrc ON tracks(isrc);
CREATE INDEX IF NOT EXISTS idx_tracks_canonical ON tracks(canonical_key);

-- Point-in-time snapshot of a playlist's metadata.
CREATE TABLE IF NOT EXISTS playlist_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    service      TEXT NOT NULL,
    playlist_id  TEXT NOT NULL,
    name         TEXT,
    description  TEXT,
    track_count  INTEGER,
    captured_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_playlist
    ON playlist_snapshots(service, playlist_id, captured_at);

-- Ordered tracks belonging to a snapshot.
CREATE TABLE IF NOT EXISTS playlist_tracks (
    snapshot_id       INTEGER NOT NULL REFERENCES playlist_snapshots(id) ON DELETE CASCADE,
    position          INTEGER NOT NULL,
    service           TEXT,
    service_track_id  TEXT,
    title             TEXT,
    artist            TEXT,
    album             TEXT,
    duration_ms       INTEGER,
    isrc              TEXT,
    PRIMARY KEY (snapshot_id, position)
);

-- Cross-service identity mapping (output of TrackMatchingService).
CREATE TABLE IF NOT EXISTS track_service_mapping (
    canonical_track_id TEXT PRIMARY KEY,
    isrc               TEXT,
    normalized_artist  TEXT,
    normalized_title   TEXT,
    duration_ms        INTEGER,
    apple_music_id     TEXT,
    tidal_id           TEXT,
    confidence         REAL,
    match_method       TEXT,
    updated_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_mapping_isrc ON track_service_mapping(isrc);
CREATE INDEX IF NOT EXISTS idx_mapping_apple ON track_service_mapping(apple_music_id);
CREATE INDEX IF NOT EXISTS idx_mapping_tidal ON track_service_mapping(tidal_id);

-- Every recommended track (so we don't repeat recommendations).
CREATE TABLE IF NOT EXISTS recommendation_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id         TEXT,
    prompt             TEXT,
    canonical_track_id TEXT,
    service            TEXT,
    service_track_id   TEXT,
    title              TEXT,
    artist             TEXT,
    reason             TEXT,
    created_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_rec_history_canonical
    ON recommendation_history(canonical_track_id);

-- User feedback on recommendations.
CREATE TABLE IF NOT EXISTS recommendation_feedback (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_track_id TEXT,
    service            TEXT,
    service_track_id   TEXT,
    feedback           TEXT NOT NULL,
    note               TEXT,
    created_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_rec_feedback_canonical
    ON recommendation_feedback(canonical_track_id);

-- Mood tags per artist (hybrid base: profile tags + auto/agent-supplied tags).
CREATE TABLE IF NOT EXISTS artist_moods (
    artist_norm   TEXT PRIMARY KEY,   -- normalized artist name (match key)
    display_name  TEXT NOT NULL,      -- original casing for output
    moods         TEXT NOT NULL,      -- comma-separated, lowercase
    source        TEXT,               -- profile | auto | agent | manual
    updated_at    TEXT
);

-- Previewed, not-yet-created playlist drafts (preview -> create flow).
CREATE TABLE IF NOT EXISTS playlist_drafts (
    draft_id    TEXT PRIMARY KEY,
    name        TEXT,
    description TEXT,
    payload     TEXT NOT NULL,        -- JSON of PlaylistDraft
    created_at  TEXT
);
"""


def connect(path: Union[str, Path, None] = None) -> sqlite3.Connection:
    """Open (creating if needed) the database and ensure the schema exists."""
    raw = str(path) if path is not None else str(DEFAULT_DB_PATH)
    if raw != ":memory:":
        Path(raw).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(raw)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
