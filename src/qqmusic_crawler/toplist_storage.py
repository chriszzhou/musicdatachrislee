from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List

from .sqlite_util import connect_sqlite


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
    conn = connect_sqlite(db_file)
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
    conn = connect_sqlite(db_file, row_factory=sqlite3.Row)
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


def get_artist_mid_from_toplist_db(db_file: Path, artist_name: str) -> str | None:
    """从榜单库里查已存在的 artist_mid（按 artist_name 匹配），避免为展示页调 API。"""
    if not db_file.is_file():
        return None
    conn = connect_sqlite(db_file)
    try:
        _ensure_toplist_table(conn)
        row = conn.execute(
            "SELECT artist_mid FROM artist_toplist_hits WHERE artist_name = ? LIMIT 1",
            (artist_name.strip(),),
        ).fetchone()
        return str(row[0]).strip() if row and row[0] else None
    finally:
        conn.close()


def query_artist_toplist_hits_since(
    db_file: Path,
    artist_mid: str,
    last_seen_since: str,
    limit: int = 500,
) -> List[Dict[str, object]]:
    """读取某歌手上榜记录，仅保留 last_seen_at >= last_seen_since 的（用于“今日”数据）。"""
    if limit <= 0:
        limit = 500
    if not db_file.is_file():
        return []
    conn = connect_sqlite(db_file, row_factory=sqlite3.Row)
    try:
        _ensure_toplist_table(conn)
        rows = conn.execute(
            """
            SELECT artist_mid, artist_name, top_id, top_name, top_period, top_update_time,
                   rank, song_mid, song_id, song_name, album_name, singer_names,
                   first_seen_at, last_seen_at
            FROM artist_toplist_hits
            WHERE artist_mid = ? AND last_seen_at >= ?
            ORDER BY top_name ASC, rank ASC
            LIMIT ?
            """,
            (artist_mid, last_seen_since.strip(), limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_all_toplist_hits_since(
    db_file: Path,
    last_seen_since: str,
    limit: int = 1000,
) -> List[Dict[str, object]]:
    """读取榜单库中所有歌手的今日上榜记录（last_seen_at >= last_seen_since），用于「榜单数据-所有歌曲」展示。"""
    if limit <= 0:
        limit = 1000
    if not db_file.is_file():
        return []
    conn = connect_sqlite(db_file, row_factory=sqlite3.Row)
    try:
        _ensure_toplist_table(conn)
        rows = conn.execute(
            """
            SELECT artist_mid, artist_name, top_id, top_name, top_period, top_update_time,
                   rank, song_mid, song_id, song_name, album_name, singer_names,
                   first_seen_at, last_seen_at
            FROM artist_toplist_hits
            WHERE last_seen_at >= ?
            ORDER BY top_name ASC, rank ASC
            LIMIT ?
            """,
            (last_seen_since.strip(), limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

