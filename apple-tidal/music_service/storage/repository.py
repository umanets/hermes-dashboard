"""High-level repository over the SQLite schema.

All persistence goes through ``MusicRepository`` so tools/services never write
raw SQL. Methods accept/return the common domain models where possible.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from ..matching import canonical_key, normalize_text
from ..models import Playlist, PlaylistDetails, PlaylistDraft, Service, Track, TrackCandidate
from .db import connect

# Feedback vocabulary preserved by the system (brief Phase 8).
FEEDBACK_VALUES = frozenset(
    {
        "liked",
        "disliked",
        "already_known",
        "wrong_version",
        "not_emotional_enough",
        "vocals_too_generic",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MusicRepository:
    def __init__(self, conn: Optional[sqlite3.Connection] = None, *, path: Union[str, Path, None] = None) -> None:
        self.conn = conn or connect(path)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "MusicRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- service accounts (no secrets) -----------------------------------

    def upsert_service_account(
        self,
        service: Service,
        *,
        user_id: Optional[str] = None,
        country: Optional[str] = None,
        scopes: Optional[str] = None,
    ) -> None:
        now = _now()
        self.conn.execute(
            """
            INSERT INTO service_accounts (service, user_id, country, scopes, connected_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(service) DO UPDATE SET
                user_id=excluded.user_id,
                country=excluded.country,
                scopes=excluded.scopes,
                updated_at=excluded.updated_at
            """,
            (service, user_id, country, scopes, now, now),
        )
        self.conn.commit()

    def get_service_account(self, service: Service) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM service_accounts WHERE service = ?", (service,)
        ).fetchone()
        return dict(row) if row else None

    # ---- tracks cache -----------------------------------------------------

    def upsert_track(self, track: Track) -> None:
        self.conn.execute(
            """
            INSERT INTO tracks (
                service, service_track_id, title, artist, album, duration_ms,
                isrc, explicit, url, normalized_artist, normalized_title,
                canonical_key, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(service, service_track_id) DO UPDATE SET
                title=excluded.title, artist=excluded.artist, album=excluded.album,
                duration_ms=excluded.duration_ms, isrc=excluded.isrc,
                explicit=excluded.explicit, url=excluded.url,
                normalized_artist=excluded.normalized_artist,
                normalized_title=excluded.normalized_title,
                canonical_key=excluded.canonical_key, updated_at=excluded.updated_at
            """,
            (
                track.service,
                track.id,
                track.title,
                track.artist,
                track.album,
                track.duration_ms,
                track.isrc,
                None if track.explicit is None else int(track.explicit),
                track.url,
                normalize_text(track.artist),
                normalize_text(track.title),
                canonical_key(track),
                _now(),
            ),
        )
        self.conn.commit()

    def upsert_tracks(self, tracks: list[Track]) -> None:
        for t in tracks:
            self.upsert_track(t)

    def get_track(self, service: Service, service_track_id: str) -> Optional[Track]:
        row = self.conn.execute(
            "SELECT * FROM tracks WHERE service = ? AND service_track_id = ?",
            (service, service_track_id),
        ).fetchone()
        return _row_to_track(row) if row else None

    # ---- playlist snapshots ----------------------------------------------

    def save_playlist_snapshot(self, details: PlaylistDetails) -> int:
        pl = details.playlist
        cur = self.conn.execute(
            """
            INSERT INTO playlist_snapshots
                (service, playlist_id, name, description, track_count, captured_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pl.service, pl.id, pl.name, pl.description, len(details.tracks), _now()),
        )
        snapshot_id = int(cur.lastrowid)
        self.conn.executemany(
            """
            INSERT INTO playlist_tracks
                (snapshot_id, position, service, service_track_id, title, artist,
                 album, duration_ms, isrc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (snapshot_id, pos, t.service, t.id, t.title, t.artist, t.album, t.duration_ms, t.isrc)
                for pos, t in enumerate(details.tracks)
            ],
        )
        # Also refresh the track cache.
        self.upsert_tracks(details.tracks)
        self.conn.commit()
        return snapshot_id

    def get_latest_snapshot(self, service: Service, playlist_id: str) -> Optional[PlaylistDetails]:
        snap = self.conn.execute(
            """
            SELECT * FROM playlist_snapshots
            WHERE service = ? AND playlist_id = ?
            ORDER BY captured_at DESC, id DESC LIMIT 1
            """,
            (service, playlist_id),
        ).fetchone()
        if not snap:
            return None
        rows = self.conn.execute(
            "SELECT * FROM playlist_tracks WHERE snapshot_id = ? ORDER BY position",
            (snap["id"],),
        ).fetchall()
        playlist = Playlist(
            id=snap["playlist_id"],
            service=snap["service"],
            name=snap["name"],
            description=snap["description"],
            track_count=snap["track_count"],
        )
        tracks = [
            Track(
                id=r["service_track_id"] or "",
                service=r["service"],
                title=r["title"] or "",
                artist=r["artist"] or "",
                album=r["album"],
                duration_ms=r["duration_ms"],
                isrc=r["isrc"],
            )
            for r in rows
        ]
        return PlaylistDetails(playlist=playlist, tracks=tracks)

    # ---- cross-service mapping -------------------------------------------

    def upsert_mapping(self, canonical_track_id: str, mapping: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO track_service_mapping (
                canonical_track_id, isrc, normalized_artist, normalized_title,
                duration_ms, apple_music_id, tidal_id, confidence, match_method, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_track_id) DO UPDATE SET
                isrc=excluded.isrc,
                normalized_artist=excluded.normalized_artist,
                normalized_title=excluded.normalized_title,
                duration_ms=excluded.duration_ms,
                apple_music_id=COALESCE(excluded.apple_music_id, track_service_mapping.apple_music_id),
                tidal_id=COALESCE(excluded.tidal_id, track_service_mapping.tidal_id),
                confidence=excluded.confidence,
                match_method=excluded.match_method,
                updated_at=excluded.updated_at
            """,
            (
                canonical_track_id,
                mapping.get("isrc"),
                mapping.get("normalized_artist"),
                mapping.get("normalized_title"),
                mapping.get("duration_ms"),
                mapping.get("apple_music_id"),
                mapping.get("tidal_id"),
                mapping.get("confidence"),
                mapping.get("match_method"),
                _now(),
            ),
        )
        self.conn.commit()

    def get_mapping(self, canonical_track_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM track_service_mapping WHERE canonical_track_id = ?",
            (canonical_track_id,),
        ).fetchone()
        return dict(row) if row else None

    def find_mapping_by_service_id(self, service: Service, service_track_id: str) -> Optional[dict]:
        column = "apple_music_id" if service == "apple_music" else "tidal_id"
        row = self.conn.execute(
            f"SELECT * FROM track_service_mapping WHERE {column} = ?",
            (service_track_id,),
        ).fetchone()
        return dict(row) if row else None

    # ---- recommendation history & feedback -------------------------------

    def add_recommendations(
        self, request_id: str, prompt: str, candidates: list[TrackCandidate]
    ) -> None:
        now = _now()
        self.conn.executemany(
            """
            INSERT INTO recommendation_history
                (request_id, prompt, canonical_track_id, service, service_track_id,
                 title, artist, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    request_id,
                    prompt,
                    c.canonical_id,
                    None,
                    c.apple_music_id or c.tidal_id,
                    c.title,
                    c.artist,
                    c.reason,
                    now,
                )
                for c in candidates
            ],
        )
        self.conn.commit()

    def already_recommended_ids(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT canonical_track_id FROM recommendation_history "
            "WHERE canonical_track_id IS NOT NULL"
        ).fetchall()
        return {r[0] for r in rows}

    def record_feedback(
        self,
        *,
        feedback: str,
        canonical_track_id: Optional[str] = None,
        service: Optional[Service] = None,
        service_track_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        if feedback not in FEEDBACK_VALUES:
            raise ValueError(
                f"Unknown feedback '{feedback}'. Expected one of: {sorted(FEEDBACK_VALUES)}"
            )
        self.conn.execute(
            """
            INSERT INTO recommendation_feedback
                (canonical_track_id, service, service_track_id, feedback, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (canonical_track_id, service, service_track_id, feedback, note, _now()),
        )
        self.conn.commit()

    def get_feedback(self, canonical_track_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM recommendation_feedback WHERE canonical_track_id = ? ORDER BY id",
            (canonical_track_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def disliked_ids(self) -> set[str]:
        """Canonical ids the user reacted negatively to (exclude from future recs)."""
        rows = self.conn.execute(
            """
            SELECT DISTINCT canonical_track_id FROM recommendation_feedback
            WHERE canonical_track_id IS NOT NULL
              AND feedback IN ('disliked', 'already_known', 'wrong_version',
                               'not_emotional_enough', 'vocals_too_generic')
            """
        ).fetchall()
        return {r[0] for r in rows}


    # ---- artist mood tags (hybrid base) ----------------------------------

    def upsert_artist_moods(self, display_name: str, moods: list[str], *, source: str = "agent") -> None:
        norm = normalize_text(display_name)
        moods_csv = ",".join(sorted({m.casefold().strip() for m in moods if m.strip()}))
        self.conn.execute(
            """
            INSERT INTO artist_moods (artist_norm, display_name, moods, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(artist_norm) DO UPDATE SET
                display_name=excluded.display_name, moods=excluded.moods,
                source=excluded.source, updated_at=excluded.updated_at
            """,
            (norm, display_name, moods_csv, source, _now()),
        )
        self.conn.commit()

    def get_artist_moods(self, display_name: str) -> list[str]:
        row = self.conn.execute(
            "SELECT moods FROM artist_moods WHERE artist_norm = ?", (normalize_text(display_name),)
        ).fetchone()
        return [m for m in (row["moods"].split(",") if row and row["moods"] else []) if m]

    def get_artists_for_mood(self, mood: str) -> list[str]:
        target = mood.casefold().strip()
        rows = self.conn.execute("SELECT display_name, moods FROM artist_moods").fetchall()
        return [r["display_name"] for r in rows if target in (r["moods"].split(",") if r["moods"] else [])]

    # ---- playlist drafts (preview -> create) -----------------------------

    def save_draft(self, draft_id: str, draft: "PlaylistDraft") -> None:
        self.conn.execute(
            """
            INSERT INTO playlist_drafts (draft_id, name, description, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(draft_id) DO UPDATE SET
                name=excluded.name, description=excluded.description,
                payload=excluded.payload, created_at=excluded.created_at
            """,
            (draft_id, draft.name, draft.description, draft.model_dump_json(), _now()),
        )
        self.conn.commit()

    def get_draft(self, draft_id: str) -> Optional["PlaylistDraft"]:
        row = self.conn.execute(
            "SELECT payload FROM playlist_drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        if not row:
            return None
        return PlaylistDraft.model_validate(json.loads(row["payload"]))


def _row_to_track(row: sqlite3.Row) -> Track:
    explicit = row["explicit"]
    return Track(
        id=row["service_track_id"],
        service=row["service"],
        title=row["title"] or "",
        artist=row["artist"] or "",
        album=row["album"],
        duration_ms=row["duration_ms"],
        isrc=row["isrc"],
        explicit=None if explicit is None else bool(explicit),
        url=row["url"],
    )
