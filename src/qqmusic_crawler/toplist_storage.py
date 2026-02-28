from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List


def _ensure_toplist_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artist_toplist_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_mid TEXT NOT NULL,
            artist_name TEXT,
            top_id INTEGER NOT NULL,
            top_name TEXT,
            top_period TEXT,
            top_update_time TEXT,
            rank INTEGER NOT NULL,
            song_mid TEXT NOT NULL,
            song_id INTEGER,
            song_name TEXT,
            album_name TEXT,
            singer_names TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            UNIQUE (artist_mid, top_id, top_period, song_mid)
        )
        """
    )
    conn.commit()


def upsert_artist_toplist_hits(
    db_file: Path,
    artist_mid: str,
    artist_name: str,
    hits: Iterable[Dict[str, object]],
) -> int:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        _ensure_toplist_table(conn)
        count = 0
        for hit in hits:
            top_period = str(hit.get("top_period") or "")
            conn.execute(
                """
                INSERT INTO artist_toplist_hits (
                    artist_mid, artist_name, top_id, top_name, top_period, top_update_time,
                    rank, song_mid, song_id, song_name, album_name, singer_names,
                    first_seen_at, last_seen_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artist_mid, top_id, top_period, song_mid) DO UPDATE SET
                    artist_name=excluded.artist_name,
                    top_name=excluded.top_name,
                    top_update_time=excluded.top_update_time,
                    rank=excluded.rank,
                    song_id=excluded.song_id,
                    song_name=excluded.song_name,
                    album_name=excluded.album_name,
                    singer_names=excluded.singer_names,
                    last_seen_at=excluded.last_seen_at,
                    raw_json=excluded.raw_json
                """,
                (
                    artist_mid,
                    artist_name,
                    int(hit.get("top_id") or 0),
                    str(hit.get("top_name") or ""),
                    top_period,
                    str(hit.get("top_update_time") or ""),
                    int(hit.get("rank") or 0),
                    str(hit.get("song_mid") or ""),
                    hit.get("song_id"),
                    str(hit.get("song_name") or ""),
                    str(hit.get("album_name") or ""),
                    str(hit.get("singer_names") or ""),
                    now,
                    now,
                    json.dumps(hit.get("raw_json") or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def query_artist_toplist_hits(
    db_file: Path,
    artist_mid: str,
    limit: int = 200,
) -> List[Dict[str, object]]:
    if limit <= 0:
        limit = 200
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_toplist_table(conn)
        rows = conn.execute(
            """
            SELECT artist_mid, artist_name, top_id, top_name, top_period, top_update_time,
                   rank, song_mid, song_id, song_name, album_name, singer_names,
                   first_seen_at, last_seen_at
            FROM artist_toplist_hits
            WHERE artist_mid = ?
            ORDER BY top_name ASC, rank ASC
            LIMIT ?
            """,
            (artist_mid, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
