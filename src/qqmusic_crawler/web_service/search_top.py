from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..sqlite_util import connect_sqlite

from .paths import (
    SUPPORTED_PLATFORMS,
    _resolve_snapshots_dir,
    get_platform_meta,
)


def _parse_snapshot_stem_for_artist_mid(stem: str, prefix: str) -> Optional[str]:
    """
    快照文件名（无 .db）与 crawl_ops 一致：prefix_artist_mid_YYYYMMDD_HHMMSS。
    artist_mid 本身可含下划线，日期、时间各占一段。
    """
    exp = prefix + "_"
    if not stem.startswith(exp):
        return None
    rest = stem[len(exp) :]
    parts = rest.split("_")
    if len(parts) < 3:
        return None
    if not (
        len(parts[-2]) == 8
        and parts[-2].isdigit()
        and len(parts[-1]) == 6
        and parts[-1].isdigit()
    ):
        return None
    mid = "_".join(parts[:-2])
    return mid if mid else None


def _latest_snapshot_paths_by_artist_mid(snapshots_dir: Path, prefix: str) -> Dict[str, Path]:
    """每个 artist_mid 只保留 mtime 最新的一份快照路径。"""
    if not snapshots_dir.is_dir():
        return {}
    by_mid: Dict[str, Path] = {}
    for p in snapshots_dir.glob("{}_*.db".format(prefix)):
        mid = _parse_snapshot_stem_for_artist_mid(p.stem, prefix)
        if not mid:
            continue
        prev = by_mid.get(mid)
        if prev is None or p.stat().st_mtime > prev.stat().st_mtime:
            by_mid[mid] = p
    return by_mid


def _artist_display_name_matches(configured: str, db_name: str) -> bool:
    """配置歌手名与快照 artists.name 是否视为同一人（仅本地匹配，不调接口）。"""
    a = (configured or "").strip()
    b = (db_name or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    return a.lower() == b.lower()


def _find_latest_snapshot_for_configured_artist(
    snapshots_dir: Path,
    prefix: str,
    artist_name: str,
) -> Optional[Tuple[Path, str, str]]:
    """
    在快照目录中，按 artists.name 匹配配置名，返回 (最新快照路径, artist_mid, 库内展示名)。
    无匹配返回 None。
    """
    want = (artist_name or "").strip()
    if not want:
        return None
    by_mid = _latest_snapshot_paths_by_artist_mid(snapshots_dir, prefix)
    if not by_mid:
        return None
    best: Optional[Tuple[Path, str, str, float]] = None
    for mid, path in by_mid.items():
        conn = connect_sqlite(path)
        try:
            row = conn.execute(
                "SELECT name FROM artists WHERE artist_mid = ? LIMIT 1",
                (mid,),
            ).fetchone()
            if not row:
                continue
            db_name = (row[0] or "").strip()
            if not _artist_display_name_matches(want, db_name):
                continue
            mtime = path.stat().st_mtime
            if best is None or mtime > best[3]:
                best = (path, mid, db_name, mtime)
        finally:
            conn.close()
    if best is None:
        return None
    return best[0], best[1], best[2]

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


def search_songs_all_platforms(
    keyword: str,
    base_dir: Optional[Path] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """三平台各自最新快照中搜索歌曲；各平台结果独立，失败不影响其它平台。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return {"ok": False, "error": "请输入搜索关键词。", "keyword": "", "by_platform": {}}

    by_platform: Dict[str, Dict[str, Any]] = {}
    err_parts: List[str] = []
    for plat in SUPPORTED_PLATFORMS:
        data = search_songs(platform=plat, keyword=keyword, base_dir=base_dir, limit=limit)
        by_platform[plat] = data
        if not data.get("ok"):
            err_parts.append(
                "{}: {}".format(
                    get_platform_meta(plat).get("name", plat),
                    str(data.get("error") or "失败"),
                )
            )

    any_ok = any(by_platform[p].get("ok") for p in SUPPORTED_PLATFORMS)
    if not any_ok:
        return {
            "ok": False,
            "error": "；".join(err_parts) if err_parts else "三平台搜索均失败。",
            "keyword": keyword,
            "by_platform": by_platform,
        }
    return {
        "ok": True,
        "keyword": keyword,
        "by_platform": by_platform,
    }


def get_artist_snapshot_metrics_all_platforms(
    artist_name: str,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    各平台「配置歌手」最新快照：粉丝数、全库歌曲收藏合计、评论合计（首页饼图用）。
    仅从本地快照库解析，不调各平台 HTTP 接口；按 artists.name 与配置名匹配。
    """
    name_stub = (artist_name or "").strip()
    if not name_stub:
        return {"ok": False, "error": "未配置歌手名。", "by_platform": {}, "display_name": ""}

    by_platform: Dict[str, Dict[str, Any]] = {}
    display_name: str = ""

    for plat in SUPPORTED_PLATFORMS:
        meta = get_platform_meta(plat)
        pname = meta.get("name", plat)
        prefix = meta["snapshot_prefix"]
        snapshots_dir = _resolve_snapshots_dir(plat, base_dir)

        if not snapshots_dir.is_dir():
            by_platform[plat] = {
                "ok": False,
                "error": "无快照目录",
                "fans": 0,
                "favorite_sum": 0,
                "comment_sum": 0,
                "platform_name": pname,
            }
            continue

        found = _find_latest_snapshot_for_configured_artist(
            snapshots_dir, prefix, name_stub
        )
        if not found:
            by_platform[plat] = {
                "ok": False,
                "error": "快照中未找到该歌手",
                "fans": 0,
                "favorite_sum": 0,
                "comment_sum": 0,
                "platform_name": pname,
            }
            continue

        latest, artist_mid, resolved_name = found
        if (resolved_name or "").strip() and not display_name:
            display_name = str(resolved_name).strip()

        conn = connect_sqlite(latest)
        try:
            fans = 0
            try:
                cur = conn.execute(
                    "SELECT COALESCE(fans, 0) FROM artists WHERE artist_mid = ? LIMIT 1",
                    (artist_mid,),
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    fans = int(row[0])
            except (sqlite3.OperationalError, TypeError, ValueError):
                fans = 0

            cur = conn.execute(
                """
                SELECT
                  COALESCE(SUM(COALESCE(favorite_count_text, 0)), 0),
                  COALESCE(SUM(COALESCE(comment_count, 0)), 0)
                FROM songs WHERE artist_mid = ?
                """,
                (artist_mid,),
            )
            sum_row = cur.fetchone()
            fav_sum = int(sum_row[0] or 0) if sum_row else 0
            com_sum = int(sum_row[1] or 0) if sum_row else 0
        finally:
            conn.close()

        by_platform[plat] = {
            "ok": True,
            "fans": fans,
            "favorite_sum": fav_sum,
            "comment_sum": com_sum,
            "platform_name": pname,
            "artist_mid": artist_mid,
            "resolved_name": resolved_name,
            "snapshot_name": latest.name,
        }

    return {
        "ok": True,
        "artist_query": name_stub,
        "display_name": display_name or name_stub,
        "by_platform": by_platform,
    }


def get_top_songs(
    platform: str,
    artist_name: str,
    top_n: int = 15,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    top_n_safe = top_n if top_n and top_n > 0 else 15
    meta = get_platform_meta(platform)
    snapshots_dir = _resolve_snapshots_dir(platform, base_dir)
    found = _find_latest_snapshot_for_configured_artist(
        snapshots_dir,
        meta["snapshot_prefix"],
        artist_name,
    )
    if not found:
        return {"ok": False, "error": "快照中未找到该歌手，请先执行抓取。"}
    latest, artist_mid, resolved_name = found

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


def get_top_songs_slice(
    platform: str,
    artist_name: str,
    offset: int = 0,
    limit: int = 10,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    从歌手快照读取「收藏」排行的一段（分页）。limit 最大 50；多取 1 条用于判断 has_more。
    """
    limit_safe = min(max(1, int(limit)), 50)
    offset_safe = max(0, int(offset))

    meta = get_platform_meta(platform)
    snapshots_dir = _resolve_snapshots_dir(platform, base_dir)
    found = _find_latest_snapshot_for_configured_artist(
        snapshots_dir,
        meta["snapshot_prefix"],
        artist_name,
    )
    if not found:
        return {"ok": False, "error": "快照中未找到该歌手，请先执行抓取。"}
    latest, artist_mid, resolved_name = found

    conn = connect_sqlite(latest)
    try:
        _ensure_songs_mixsongid(conn)
        has_raw_json = _songs_has_column(conn, "raw_json")
        if has_raw_json:
            fav_sel = "SELECT song_mid, name, COALESCE(favorite_count_text, 0) AS favorite_count_text, mixsongid, raw_json FROM songs"
            raw_idx = 4
        else:
            fav_sel = "SELECT song_mid, name, COALESCE(favorite_count_text, 0) AS favorite_count_text, mixsongid FROM songs"
            raw_idx = None
        cur = conn.cursor()
        rows = cur.execute(
            fav_sel
            + " WHERE artist_mid = ? ORDER BY favorite_count_text DESC, song_mid ASC LIMIT ? OFFSET ?",
            (artist_mid, limit_safe + 1, offset_safe),
        ).fetchall()
    finally:
        conn.close()

    has_more = len(rows) > limit_safe
    rows = rows[:limit_safe]

    out_rows = [
        {
            "rank": offset_safe + i + 1,
            "song_mid": r[0],
            "song_name": r[1] or r[0],
            "value": int(r[2] or 0),
            "mixsongid": _mixsongid_from_row(r, 3, raw_idx),
        }
        for i, r in enumerate(rows)
    ]

    return {
        "ok": True,
        "platform": platform,
        "platform_name": meta.get("name", platform),
        "artist_mid": artist_mid,
        "artist_name": resolved_name,
        "snapshot_name": latest.name,
        "offset": offset_safe,
        "limit": limit_safe,
        "has_more": has_more,
        "rows": out_rows,
    }

