from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..crawler import CrawlerService
from ..sqlite_util import connect_sqlite

from .clients import build_client, _resolve_artist
from .paths import (
    _resolve_snapshots_dir,
    get_platform_meta,
)

def _ensure_songs_mixsongid(conn: sqlite3.Connection) -> None:
    """若 songs 表无 mixsongid 列则添加（兼容旧快照）。"""
    cur = conn.execute("PRAGMA table_info(songs)")
    cols = {row[1] for row in cur.fetchall()}
    if "mixsongid" not in cols:
        conn.execute("ALTER TABLE songs ADD COLUMN mixsongid INTEGER")
        conn.commit()


def _songs_has_column(conn: sqlite3.Connection, column: str) -> bool:
    """songs 表是否包含指定列。"""
    cur = conn.execute("PRAGMA table_info(songs)")
    return column in {row[1] for row in cur.fetchall()}


def _mixsongid_from_row(
    row: Tuple[object, ...],
    mixsongid_index: int,
    raw_json_index: Optional[int] = None,
) -> Optional[int]:
    """从查询行取 mixsongid：先读列值，为空则从 raw_json 解析（酷狗旧快照无该列时数据在 raw_json 里）。"""
    if mixsongid_index < len(row) and row[mixsongid_index] is not None:
        try:
            return int(row[mixsongid_index])
        except (TypeError, ValueError):
            pass
    if raw_json_index is not None and raw_json_index < len(row) and row[raw_json_index]:
        try:
            raw = json.loads(row[raw_json_index])
            if isinstance(raw, dict):
                v = raw.get("mixsongid") or raw.get("album_audio_id") or raw.get("audio_id")
                if v is not None:
                    return int(v)
        except (TypeError, ValueError, KeyError):
            pass
    return None


def search_songs(
    platform: str,
    keyword: str,
    base_dir: Optional[Path] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """
    从当前平台最新一次快照中搜索歌曲。
    匹配规则：歌曲名或专辑名包含关键词即匹配（LIKE %keyword%）。
    返回：歌曲名、专辑名、评论量、收藏量；QQ 平台可点击歌名跳转热度页。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return {"ok": False, "error": "请输入搜索关键词。"}
    meta = get_platform_meta(platform)
    snapshots_dir = _resolve_snapshots_dir(platform, base_dir)
    prefix = meta["snapshot_prefix"]
    pattern = "{}_*.db".format(prefix)
    if not snapshots_dir.is_dir():
        return {"ok": False, "error": "暂无快照目录，请先执行抓取。"}
    candidates = list(snapshots_dir.glob(pattern))
    if not candidates:
        return {"ok": False, "error": "未找到任何快照，请先执行抓取。"}
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    limit_safe = min(max(1, limit), 500)
    like_arg = "%{}%".format(keyword)
    conn = connect_sqlite(latest)
    try:
        _ensure_songs_mixsongid(conn)
        has_raw_json = _songs_has_column(conn, "raw_json")
        if has_raw_json:
            sel = "SELECT song_mid, name, album_name, comment_count, favorite_count_text, mixsongid, raw_json FROM songs"
            mixsongid_idx, raw_json_idx = 5, 6
        else:
            sel = "SELECT song_mid, name, album_name, comment_count, favorite_count_text, mixsongid FROM songs"
            mixsongid_idx, raw_json_idx = 5, None
        cur = conn.execute(
            sel + """
            WHERE (name LIKE ? OR (album_name IS NOT NULL AND album_name LIKE ?))
            ORDER BY COALESCE(favorite_count_text, 0) DESC, song_mid ASC
            LIMIT ?
            """,
            (like_arg, like_arg, limit_safe),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return {
        "ok": True,
        "snapshot_name": latest.name,
        "keyword": keyword,
        "rows": [
            {
                "song_mid": r[0] or "",
                "song_name": (r[1] or r[0] or "").strip() or "-",
                "album_name": (r[2] or "").strip() or "-",
                "comment_count": int(r[3]) if r[3] is not None else 0,
                "favorite_count": int(r[4]) if r[4] is not None else 0,
                "mixsongid": _mixsongid_from_row(r, mixsongid_idx, raw_json_idx),
            }
            for r in rows
        ],
    }



def get_top_songs(
    platform: str,
    artist_name: str,
    top_n: int = 15,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    top_n_safe = top_n if top_n and top_n > 0 else 15
    meta = get_platform_meta(platform)
    client = build_client(platform)
    service = CrawlerService(client=client)
    try:
        resolved = _resolve_artist(service, artist_name)
        if not resolved:
            return {"ok": False, "error": "未找到歌手，请重试。"}
        artist_mid, resolved_name = resolved
    finally:
        client.close()

    snapshots_dir = _resolve_snapshots_dir(platform, base_dir)
    candidates = sorted(
        snapshots_dir.glob("{}_{}_*.db".format(meta["snapshot_prefix"], artist_mid))
    )
    if not candidates:
        return {"ok": False, "error": "未找到该歌手快照，请先执行抓取。"}
    latest = max(candidates, key=lambda p: p.stat().st_mtime)

    conn = connect_sqlite(latest)
    try:
        _ensure_songs_mixsongid(conn)
        has_raw_json = _songs_has_column(conn, "raw_json")
        if has_raw_json:
            fav_sel = "SELECT song_mid, name, COALESCE(favorite_count_text, 0) AS favorite_count_text, mixsongid, raw_json FROM songs"
            com_sel = "SELECT song_mid, name, COALESCE(comment_count, 0) AS comment_count, mixsongid, raw_json FROM songs"
            raw_idx = 4
        else:
            fav_sel = "SELECT song_mid, name, COALESCE(favorite_count_text, 0) AS favorite_count_text, mixsongid FROM songs"
            com_sel = "SELECT song_mid, name, COALESCE(comment_count, 0) AS comment_count, mixsongid FROM songs"
            raw_idx = None
        cur = conn.cursor()
        fav_rows = cur.execute(
            fav_sel + " WHERE artist_mid = ? ORDER BY favorite_count_text DESC, song_mid ASC LIMIT ?",
            (artist_mid, top_n_safe),
        ).fetchall()
        comment_rows = cur.execute(
            com_sel + " WHERE artist_mid = ? ORDER BY comment_count DESC, song_mid ASC LIMIT ?",
            (artist_mid, top_n_safe),
        ).fetchall()
    finally:
        conn.close()

    return {
        "ok": True,
        "artist_mid": artist_mid,
        "artist_name": resolved_name,
        "snapshot_name": latest.name,
        "favorites": [
            {
                "rank": i + 1,
                "song_mid": r[0],
                "song_name": r[1] or r[0],
                "value": int(r[2] or 0),
                "mixsongid": _mixsongid_from_row(r, 3, raw_idx),
            }
            for i, r in enumerate(fav_rows)
        ],
        "comments": [
            {
                "rank": i + 1,
                "song_mid": r[0],
                "song_name": r[1] or r[0],
                "value": int(r[2] or 0),
                "mixsongid": _mixsongid_from_row(r, 3, raw_idx),
            }
            for i, r in enumerate(comment_rows)
        ],
    }

