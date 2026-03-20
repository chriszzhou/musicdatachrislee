from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..crawler import CrawlerService
from ..sqlite_util import connect_sqlite
from ..storage import Storage
from ..toplist_freshness import BEIJING_TZ, filter_toplist_rows_for_today
from ..toplist_storage import (
    get_artist_mid_from_toplist_db,
    query_artist_toplist_hits_since,
)
from ..tracking import (
    _ensure_changes_tables,
    _has_legacy_metric_changes_table,
    _list_change_month_tables,
    _table_name,
    insert_metric_changes_for_song,
)

from .clients import build_client, _resolve_artist
from .constants import (
    NEW_SONG_ARTIST,
    NEW_SONG_CHART_NUM_POINTS,
    NEW_SONG_CHART_START_DATE,
    NEW_SONG_NAME,
)
from .paths import (
    SUPPORTED_PLATFORMS,
    _resolve_changes_db_path,
    _resolve_snapshots_dir,
    _resolve_toplist_db_path,
    get_platform_meta,
    normalize_platform,
)


def _get_latest_snapshot_path(
    platform: str,
    artist_mid: str,
    base_dir: Optional[Path] = None,
) -> Optional[Path]:
    """返回该平台该歌手最新快照 DB 路径（按 mtime），不存在则 None。"""
    snapshots_dir = _resolve_snapshots_dir(platform, base_dir)
    meta = get_platform_meta(platform)
    prefix = meta["snapshot_prefix"]
    pattern = "{}_{}_*.db".format(prefix, artist_mid)
    if not snapshots_dir.is_dir():
        return None
    candidates = list(snapshots_dir.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _get_previous_snapshot_path(
    platform: str,
    artist_mid: str,
    base_dir: Optional[Path] = None,
    after_this: Optional[Path] = None,
) -> Optional[Path]:
    """返回该平台该歌手的「前一份」快照（mtime 仅小于 after_this 的最大者）；用于最新快照正在更新时回退取 id。"""
    snapshots_dir = _resolve_snapshots_dir(platform, base_dir)
    meta = get_platform_meta(platform)
    prefix = meta["snapshot_prefix"]
    pattern = "{}_{}_*.db".format(prefix, artist_mid)
    if not snapshots_dir.is_dir():
        return None
    candidates = list(snapshots_dir.glob(pattern))
    if not candidates:
        return None
    if after_this is not None:
        after_mtime = after_this.stat().st_mtime
        candidates = [p for p in candidates if p.stat().st_mtime < after_mtime]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _read_song_from_snapshot(
    db_path: Path,
    artist_mid: str,
    song_name: str,
) -> Optional[Dict[str, Any]]:
    """从快照库中按歌手+歌名读一条歌曲（含 song_mid, song_id, mixsongid 等供只更新一首时用）。"""
    if not db_path.is_file():
        return None
    conn = connect_sqlite(db_path)
    try:
        try:
            row = conn.execute(
                """
                SELECT song_mid, song_id, name, COALESCE(comment_count, 0), COALESCE(favorite_count_text, 0),
                       mixsongid
                FROM songs WHERE artist_mid = ? AND (name = ? OR name LIKE ?)
                LIMIT 1
                """,
                (artist_mid, song_name.strip(), "%" + song_name.strip() + "%"),
            ).fetchone()
            mixsongid = int(row[5]) if row and len(row) > 5 and row[5] is not None else None
        except sqlite3.OperationalError:
            row = conn.execute(
                """
                SELECT song_mid, song_id, name, COALESCE(comment_count, 0), COALESCE(favorite_count_text, 0)
                FROM songs WHERE artist_mid = ? AND (name = ? OR name LIKE ?)
                LIMIT 1
                """,
                (artist_mid, song_name.strip(), "%" + song_name.strip() + "%"),
            ).fetchone()
            mixsongid = None
        if not row:
            return None
        return {
            "song_mid": str(row[0] or "").strip(),
            "song_id": int(row[1]) if row[1] is not None else None,
            "name": str(row[2] or "").strip(),
            "comment_count": int(row[3] or 0),
            "favorite_count_text": int(row[4] or 0),
            "mixsongid": mixsongid,
        }
    finally:
        conn.close()


def _build_one_song_item_for_enrich(platform: str, snapshot_row: Dict[str, Any]) -> Dict[str, Any]:
    """用快照里的一行拼出各平台 enrich_song_metrics 所需的最简 item（只更新一首时用）。"""
    mid = (snapshot_row.get("song_mid") or "").strip()
    name = (snapshot_row.get("name") or "").strip()
    sid = snapshot_row.get("song_id")
    mixsongid = snapshot_row.get("mixsongid")
    p = normalize_platform(platform)
    if p == "qq":
        return {
            "id": int(sid) if sid is not None else 0,
            "songmid": mid,
            "songname": name,
            "name": name,
        }
    if p == "netease":
        return {
            "id": int(sid) if sid is not None else 0,
            "mid": mid,
            "name": name,
        }
    return {
        "id": int(sid) if sid is not None else None,
        "mid": mid,
        "name": name,
        "mixsongid": int(mixsongid) if mixsongid is not None else None,
    }


def update_new_song_one_platform(
    platform: str,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """单平台：从最新快照取出「春雨里」的 id，只对该一首拉取收藏/评论并更新快照与 metric_changes。"""
    base_dir = (base_dir or Path(".")).resolve()
    meta = get_platform_meta(platform)
    client = build_client(platform)
    service = CrawlerService(client=client)
    try:
        resolved = _resolve_artist(service, NEW_SONG_ARTIST)
        if not resolved:
            return {"ok": False, "platform": platform, "error": "未找到歌手 " + NEW_SONG_ARTIST}
        artist_mid, artist_name = resolved
    finally:
        client.close()

    latest = _get_latest_snapshot_path(platform, artist_mid, base_dir)
    if not latest:
        return {"ok": False, "platform": platform, "error": "暂无该歌手快照，请先执行抓取"}

    old_row = _read_song_from_snapshot(latest, artist_mid, NEW_SONG_NAME)
    if not old_row:
        previous = _get_previous_snapshot_path(platform, artist_mid, base_dir, after_this=latest)
        if previous:
            old_row = _read_song_from_snapshot(previous, artist_mid, NEW_SONG_NAME)
    if not old_row:
        return {"ok": False, "platform": platform, "error": "快照中无该歌曲，请先执行抓取"}

    item = _build_one_song_item_for_enrich(platform, old_row)
    client = build_client(platform)
    try:
        client.enrich_song_metrics([item])
    finally:
        client.close()

    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot_db_str = latest.name

    storage = Storage("sqlite:///{}".format(latest.as_posix()))
    storage.create_tables()
    storage.ensure_artist_stub(artist_mid, name=artist_name)
    storage.upsert_songs([item], artist_mid=artist_mid)

    new_comment = int(item.get("_metric_comment_count") or 0)
    new_fav = int(item.get("_metric_favorite_count_text") or 0)
    old_comment = int(old_row["comment_count"])
    old_fav = int(old_row["favorite_count_text"])

    song_mid = str(old_row.get("song_mid") or item.get("songmid") or item.get("mid") or item.get("id") or "").strip()
    changes_db = _resolve_changes_db_path(platform, base_dir)
    changes_db.parent.mkdir(parents=True, exist_ok=True)
    insert_metric_changes_for_song(
        changes_db,
        run_at=run_at,
        artist_mid=artist_mid,
        song_mid=song_mid,
        song_name=NEW_SONG_NAME,
        snapshot_db=snapshot_db_str,
        metrics=[
            ("comment_count", old_comment, new_comment),
            ("favorite_count_text", old_fav, new_fav),
        ],
    )
    return {"ok": True, "platform": platform, "favorite_count": new_fav, "comment_count": new_comment}


def get_new_song_current_metrics(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """新歌页用：三平台当前收藏量、评论数（从各平台最新快照读）。"""
    base_dir = (base_dir or Path(".")).resolve()
    out: Dict[str, Any] = {"ok": True, "song_name": NEW_SONG_NAME, "artist_name": NEW_SONG_ARTIST, "platforms": {}}
    for platform in SUPPORTED_PLATFORMS:
        meta = get_platform_meta(platform)
        artist_mid = get_artist_mid_from_toplist_db(_resolve_toplist_db_path(platform, base_dir), NEW_SONG_ARTIST)
        if not artist_mid:
            client = build_client(platform)
            service = CrawlerService(client=client)
            try:
                resolved = _resolve_artist(service, NEW_SONG_ARTIST)
                if resolved:
                    artist_mid, _ = resolved
            finally:
                client.close()
        if not artist_mid:
            out["platforms"][platform] = {"ok": False, "error": "未解析到歌手", "favorite_count": None, "comment_count": None}
            continue
        latest = _get_latest_snapshot_path(platform, artist_mid, base_dir)
        if not latest:
            out["platforms"][platform] = {"ok": False, "error": "暂无快照", "favorite_count": None, "comment_count": None}
            continue
        row = _read_song_from_snapshot(latest, artist_mid, NEW_SONG_NAME)
        if not row:
            out["platforms"][platform] = {"ok": False, "error": "快照中无该歌曲", "favorite_count": None, "comment_count": None}
            continue
        try:
            beijing_tz = timezone(timedelta(hours=8))
            snapshot_at = datetime.fromtimestamp(latest.stat().st_mtime, tz=beijing_tz).strftime("%Y-%m-%d %H:%M")
        except Exception:
            snapshot_at = ""
        out["platforms"][platform] = {
            "ok": True,
            "platform_name": meta["name"],
            "favorite_count": row["favorite_count_text"],
            "comment_count": row["comment_count"],
            "song_mid": row["song_mid"],
            "snapshot_at": snapshot_at,
        }
    return out


def get_new_song_chart_data_from_start(
    platform: str,
    base_dir: Optional[Path] = None,
    start_date: str = NEW_SONG_CHART_START_DATE,
    num_points: int = NEW_SONG_CHART_NUM_POINTS,
) -> Dict[str, Any]:
    """新歌页用：从 start_date 到当前时间，均匀取 num_points 个时间点的收藏数（春雨里）。"""
    base_dir = (base_dir or Path(".")).resolve()
    beijing_tz = timezone(timedelta(hours=8))
    end_str = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M:%S")
    start_str = (start_date or NEW_SONG_CHART_START_DATE).strip()
    if len(start_str) == 10:
        start_str = start_str + " 00:00:00"

    artist_mid = get_artist_mid_from_toplist_db(_resolve_toplist_db_path(platform, base_dir), NEW_SONG_ARTIST)
    if not artist_mid:
        client = build_client(platform)
        service = CrawlerService(client=client)
        try:
            resolved = _resolve_artist(service, NEW_SONG_ARTIST)
            artist_mid = resolved[0] if resolved else ""
        finally:
            client.close()
    artist_mid = (artist_mid or "").strip()

    db_path = _resolve_changes_db_path(platform, base_dir)
    if not db_path.is_file():
        return {"ok": True, "labels": [], "series": {"favorite": []}}

    conn = connect_sqlite(db_path, row_factory=sqlite3.Row)
    try:
        _ensure_changes_tables(conn)
        if _has_legacy_metric_changes_table(conn):
            sql = """
                SELECT run_at, new_value FROM metric_changes
                WHERE run_at >= ? AND run_at <= ? AND artist_mid = ? AND song_name = ? AND metric = 'favorite_count_text'
                ORDER BY run_at ASC
            """
            rows = conn.execute(sql, (start_str, end_str, artist_mid, NEW_SONG_NAME)).fetchall()
        else:
            month_keys = _list_change_month_tables(conn, "metric_changes")
            start_ym = start_str[:7].replace("-", "")
            end_ym = end_str[:7].replace("-", "")
            month_keys = [k for k in month_keys if start_ym <= k <= end_ym]
            if not month_keys:
                return {"ok": True, "labels": [], "series": {"favorite": []}}
            parts = []
            for mk in month_keys:
                table = _table_name("metric_changes", mk)
                parts.append(
                    "SELECT run_at, new_value FROM {} WHERE run_at >= ? AND run_at <= ? AND artist_mid = ? AND song_name = ? AND metric = 'favorite_count_text'".format(
                        table
                    )
                )
            union_sql = " UNION ALL ".join(parts)
            sql = "SELECT run_at, new_value FROM ({}) ORDER BY run_at ASC".format(union_sql)
            params = []
            for _ in month_keys:
                params.extend([start_str, end_str, artist_mid, NEW_SONG_NAME])
            rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"ok": True, "labels": [], "series": {"favorite": []}}

    run_ats = [str(r[0]) for r in rows]
    favs = [int(r[1] or 0) for r in rows]
    n = len(run_ats)
    if n <= num_points:
        labels = run_ats
        favorite = favs
    else:
        indices = [0]
        for i in range(1, num_points - 1):
            indices.append(i * (n - 1) // (num_points - 1))
        indices.append(n - 1)
        labels = [run_ats[i] for i in indices]
        favorite = [favs[i] for i in indices]
    return {"ok": True, "labels": labels, "series": {"favorite": favorite}}


def get_new_song_chart_data(
    platform: str,
    mode: str,
    value: str,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """新歌页用：单首「春雨里」的收藏/评论变化曲线。mode=range 时从开始统计日到当前均匀取 10 点。"""
    if (mode or "").strip().lower() == "range":
        return get_new_song_chart_data_from_start(platform, base_dir=base_dir)
    artist_mid = get_artist_mid_from_toplist_db(_resolve_toplist_db_path(platform, base_dir), NEW_SONG_ARTIST)
    if not artist_mid:
        client = build_client(platform)
        service = CrawlerService(client=client)
        try:
            resolved = _resolve_artist(service, NEW_SONG_ARTIST)
            artist_mid = resolved[0] if resolved else ""
        finally:
            client.close()
    from .reporting import get_report_chart_data

    return get_report_chart_data(
        platform=platform,
        mode=mode,
        value=value,
        artist_mid=artist_mid or "",
        base_dir=base_dir,
        song_name=NEW_SONG_NAME,
        use_absolute_favorite=True,
    )


def get_new_song_toplist_rows(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """新歌页用：三平台榜单中「春雨里」的上榜记录（从各 toplist 库查）。同一榜单同一天只保留最新一条（按 last_seen_at 取最大）。"""
    base_dir = (base_dir or Path(".")).resolve()
    today_bj = datetime.now(BEIJING_TZ).date()
    result: List[Dict[str, Any]] = []
    for platform in SUPPORTED_PLATFORMS:
        meta = get_platform_meta(platform)
        db_file = _resolve_toplist_db_path(platform, base_dir)
        artist_mid = get_artist_mid_from_toplist_db(db_file, NEW_SONG_ARTIST)
        if not artist_mid:
            continue
        rows = query_artist_toplist_hits_since(db_file, artist_mid, "2000-01-01 00:00:00", limit=500)
        rows_fresh, _ = filter_toplist_rows_for_today(list(rows), today_bj)
        song_rows = [
            r
            for r in rows_fresh
            if (r.get("song_name") or "").strip() == NEW_SONG_NAME
            or NEW_SONG_NAME in (r.get("song_name") or "")
        ]
        if not song_rows:
            continue
        # 同一榜单（top_id + top_name）只保留 last_seen_at 最新的一条
        by_chart: Dict[Tuple[int, str], Dict[str, Any]] = {}
        for r in song_rows:
            key = (int(r.get("top_id") or 0), str(r.get("top_name") or "").strip())
            seen = str(r.get("last_seen_at") or "")
            if key not in by_chart or seen > str(by_chart[key].get("last_seen_at") or ""):
                by_chart[key] = r
        result.append({"platform": platform, "platform_name": meta["name"], "rows": list(by_chart.values())})
    return result
