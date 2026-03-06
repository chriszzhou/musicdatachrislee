from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from .client import QQMusicClient
from .config import settings
from .crawler import CrawlerService
from .kugou_client import KugouMusicClient
from .netease_client import NeteaseMusicClient
from .storage import Storage
from .toplist_storage import query_artist_toplist_hits, upsert_artist_toplist_hits
from .tracking import (
    _ensure_changes_tables,
    _report_month_keys,
    _table_name,
    get_changes_table_for_run_at,
    report_changes,
    track_changes_for_artist,
)

SUPPORTED_PLATFORMS = ("qq", "netease", "kugou")

PLATFORM_CONFIG: Dict[str, Dict[str, str]] = {
    "qq": {
        "name": "QQ音乐",
        "snapshots_dir": "data/snapshots",
        "snapshot_prefix": "qqmusic",
        "changes_db": "data/qqmusic_changes.db",
        "toplist_db": "data/qqmusic_toplist.db",
    },
    "netease": {
        "name": "网易云音乐",
        "snapshots_dir": "data/netease_snapshots",
        "snapshot_prefix": "netease",
        "changes_db": "data/netease_changes.db",
        "toplist_db": "data/netease_toplist.db",
    },
    "kugou": {
        "name": "酷狗音乐",
        "snapshots_dir": "data/kugou_snapshots",
        "snapshot_prefix": "kugou",
        "changes_db": "data/kugou_changes.db",
        "toplist_db": "data/kugou_toplist.db",
    },
}


def normalize_platform(platform: str) -> str:
    p = (platform or "").strip().lower()
    if p not in SUPPORTED_PLATFORMS:
        return "qq"
    return p


def get_platform_meta(platform: str) -> Dict[str, str]:
    return PLATFORM_CONFIG[normalize_platform(platform)]


def _resolve_changes_db_path(platform: str, base_dir: Optional[Path] = None) -> Path:
    """解析变化库绝对路径：先试 base_dir（项目根），再试 cwd，返回第一个存在的路径。"""
    meta = get_platform_meta(platform)
    rel = meta["changes_db"]
    for root in [(base_dir or Path(".")).resolve(), Path.cwd().resolve()]:
        p = root / rel
        if p.is_file():
            return p
    return (base_dir or Path(".")).resolve() / rel


def _resolve_snapshots_dir(platform: str, base_dir: Optional[Path] = None) -> Path:
    """解析快照目录绝对路径：先试 base_dir（项目根）下存在则用，再试 cwd，否则返回 base_dir 下路径。"""
    meta = get_platform_meta(platform)
    rel = meta["snapshots_dir"]
    for root in [(base_dir or Path(".")).resolve(), Path.cwd().resolve()]:
        p = root / rel
        if p.is_dir():
            return p
    return (base_dir or Path(".")).resolve() / rel


def _resolve_toplist_db_path(platform: str, base_dir: Optional[Path] = None) -> Path:
    """解析榜单库绝对路径：先试 base_dir（项目根），再试 cwd，返回第一个存在的路径。"""
    meta = get_platform_meta(platform)
    rel = meta["toplist_db"]
    for root in [(base_dir or Path(".")).resolve(), Path.cwd().resolve()]:
        p = root / rel
        if p.is_file():
            return p
    return (base_dir or Path(".")).resolve() / rel


def resolve_data_paths_for_debug(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """返回各平台解析后的 data 路径，用于排查路径问题。"""
    root = base_dir or Path(".")
    out: Dict[str, Any] = {
        "cwd": str(Path.cwd().resolve()),
        "base_dir": str(root.resolve()),
        "platforms": {},
    }
    for platform in SUPPORTED_PLATFORMS:
        changes = _resolve_changes_db_path(platform, base_dir)
        snapshots = _resolve_snapshots_dir(platform, base_dir)
        toplist = _resolve_toplist_db_path(platform, base_dir)
        out["platforms"][platform] = {
            "changes_db": str(changes),
            "changes_db_exists": changes.is_file(),
            "snapshots_dir": str(snapshots),
            "snapshots_dir_exists": snapshots.is_dir(),
            "toplist_db": str(toplist),
            "toplist_db_exists": toplist.is_file(),
        }
    return out


def _snapshot_date_key(path: Path) -> str:
    """从快照文件名或 mtime 得到日期键 YYYYMMDD。文件名格式：prefix_mid_YYYYMMDD_HHMMSS.db"""
    stem = path.stem
    parts = stem.split("_")
    if len(parts) >= 2 and len(parts[-2]) == 8 and parts[-2].isdigit():
        return parts[-2]
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d")


def prune_old_snapshots(platform: str, keep_per_day: int = 1, base_dir: Optional[Path] = None) -> int:
    """
    按日期分组：每个自然日（如 20260227、20260228）只保留 keep_per_day 个快照 DB（默认 1 个），
    同一天多个时保留 mtime 最新的，其余删除。不删除 changes_db / toplist_db。
    返回删除的文件数量。
    """
    meta = get_platform_meta(platform)
    root = base_dir or Path(".")
    snapshots_dir = root / meta["snapshots_dir"]
    prefix = meta["snapshot_prefix"]
    if not snapshots_dir.is_dir():
        return 0
    pattern = "{}_*.db".format(prefix)
    all_files = list(snapshots_dir.glob(pattern))
    by_date: Dict[str, List[Path]] = {}
    for p in all_files:
        try:
            date_key = _snapshot_date_key(p)
            by_date.setdefault(date_key, []).append(p)
        except OSError:
            continue
    to_delete: List[Path] = []
    for date_key, paths in by_date.items():
        sorted_paths = sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
        to_delete.extend(sorted_paths[keep_per_day:])
    deleted = 0
    for p in to_delete:
        try:
            p.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted


def build_client(platform: str) -> Any:
    p = normalize_platform(platform)
    if p == "netease":
        return NeteaseMusicClient(
            base_url=settings.netease_base_url,
            timeout=settings.qqmusic_timeout,
            max_retries=settings.qqmusic_max_retries,
            rate_limit_qps=settings.netease_rate_limit_qps,
            metric_workers=settings.netease_metric_workers,
            metric_batch_size=settings.netease_metric_batch_size,
        )
    if p == "kugou":
        return KugouMusicClient(
            base_url=settings.kugou_base_url,
            timeout=settings.qqmusic_timeout,
            max_retries=settings.qqmusic_max_retries,
            rate_limit_qps=settings.kugou_rate_limit_qps,
            metric_workers=settings.kugou_metric_workers,
            metric_batch_size=settings.kugou_metric_batch_size,
        )
    return QQMusicClient(
        base_url=settings.qqmusic_base_url,
        timeout=settings.qqmusic_timeout,
        max_retries=settings.qqmusic_max_retries,
        rate_limit_qps=settings.qqmusic_rate_limit_qps,
    )


def _build_snapshot_service(platform: str, database_url: str) -> Tuple[Any, CrawlerService]:
    storage = Storage(database_url)
    storage.create_tables()
    client = build_client(platform)
    return client, CrawlerService(client=client, storage=storage)


def _resolve_artist(service: CrawlerService, artist_name: str) -> Optional[Tuple[str, str]]:
    name = (artist_name or "").strip()
    if not name:
        return None
    candidates = service.find_artist_candidates_by_name(
        keyword=name,
        max_pages=8,
        page_size=settings.qqmusic_default_artist_page_size,
    )
    if not candidates:
        return None
    first = candidates[0]
    return first["artist_mid"], first["name"]


def find_artists(platform: str, keyword: str, max_items: int = 20) -> Dict[str, Any]:
    client = build_client(platform)
    service = CrawlerService(client=client)
    try:
        items = service.find_artist_candidates_by_name(
            keyword=(keyword or "").strip(),
            max_pages=8,
            page_size=settings.qqmusic_default_artist_page_size,
        )
        limited_items = items[: max_items if max_items > 0 else 20]
        enriched_items: List[Dict[str, Any]] = []
        for item in limited_items:
            artist_mid = str(item.get("artist_mid") or "").strip()
            artist_name = str(item.get("name") or "").strip()
            fans: Optional[int] = None
            if artist_mid:
                try:
                    profile = client.fetch_artist_profile(artist_mid)
                    raw_fans = profile.get("fans")
                    fans = int(raw_fans) if raw_fans is not None else None
                except Exception:
                    fans = None
            enriched_items.append(
                {
                    "artist_mid": artist_mid,
                    "name": artist_name,
                    "fans": fans,
                }
            )
        return {
            "ok": True,
            "items": enriched_items,
            "count": len(items),
        }
    finally:
        client.close()


def crawl_track(
    platform: str,
    artist_name: str,
    song_limit: Optional[int] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    def _emit(progress_pct: int, message: str) -> None:
        if not callable(progress_callback):
            return
        progress_callback(
            {
                "progress_pct": max(0, min(100, int(progress_pct))),
                "message": message,
            }
        )

    meta = get_platform_meta(platform)
    client = build_client(platform)
    service = CrawlerService(client=client)
    try:
        _emit(2, "正在解析歌手")
        resolved = _resolve_artist(service, artist_name)
        if not resolved:
            return {"ok": False, "error": "未找到歌手，请重试。"}
        artist_mid, resolved_name = resolved
        _emit(5, "已定位歌手，正在读取歌手信息")
        profile = client.fetch_artist_profile(artist_mid)
        page_size = settings.qqmusic_default_song_page_size
        total_song = int(profile.get("total_song") or 0)

        song_limit_safe: Optional[int] = None
        if song_limit is not None and int(song_limit) > 0:
            song_limit_safe = int(song_limit)

        target_song_count: Optional[int] = None
        if song_limit_safe is not None:
            target_song_count = min(song_limit_safe, total_song) if total_song > 0 else song_limit_safe
        elif total_song > 0:
            target_song_count = total_song

        if target_song_count is not None:
            song_pages = (target_song_count + page_size - 1) // page_size
        else:
            song_pages = 200
        if song_pages <= 0:
            song_pages = 1

        snapshots_dir = Path(meta["snapshots_dir"])
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file = snapshots_dir / "{}_{}_{}.db".format(
            meta["snapshot_prefix"], artist_mid, datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        snapshot_db_url = "sqlite:///{}".format(snapshot_file.as_posix())

        storage = Storage(snapshot_db_url)
        storage.create_tables()
        storage.ensure_artist_stub(
            artist_mid,
            name=str(profile.get("name") or "").strip() or resolved_name,
            fans=int(profile.get("fans")) if profile.get("fans") is not None else None,
        )

        _emit(8, "开始抓取歌曲列表")
        total_saved = 0
        for page in range(1, song_pages + 1):
            remaining = None
            if target_song_count is not None:
                remaining = target_song_count - total_saved
                if remaining <= 0:
                    break

            songs = client.fetch_songs_by_artist(
                artist_mid=artist_mid,
                page=page,
                page_size=page_size,
            )
            if remaining is not None and remaining < len(songs):
                songs = songs[:remaining]
            if not songs:
                break

            songs = client.enrich_song_metrics(songs)
            saved = storage.upsert_songs(songs, artist_mid=artist_mid)
            total_saved += saved

            base_progress = int(page / max(song_pages, 1) * 90)
            _emit(base_progress, "抓取中: 第{}/{}页，已保存{}首".format(page, song_pages, total_saved))

            if len(songs) < page_size and target_song_count is None:
                # 返回不足一页时认为到达末尾，避免未知总量下继续空跑。
                break

        _emit(92, "抓取完成，正在追踪变化")
        result = track_changes_for_artist(
            snapshots_dir=snapshots_dir,
            current_snapshot_file=snapshot_file,
            changes_db_file=Path(meta["changes_db"]),
            artist_mid=artist_mid,
        )
        for song_name, count in result.get("milestones", []):
            logger.info(
                "{} 收藏里程碑: {} {}",
                meta.get("name", platform),
                song_name,
                count,
            )
        _emit(96, "变化追踪完成，正在汇总明细")
        metric_field_changes: List[Dict[str, Any]] = []
        artist_metric_field_changes: List[Dict[str, Any]] = []
        metric_change_rows: List[Dict[str, Any]] = []
        artist_metric_change_rows: List[Dict[str, Any]] = []
        changes_conn = sqlite3.connect(str(Path(meta["changes_db"])))
        try:
            _ensure_changes_tables(changes_conn)
            tbl_metric = get_changes_table_for_run_at("metric_changes")
            tbl_artist = get_changes_table_for_run_at("artist_metric_changes")
            cur = changes_conn.cursor()
            metric_rows = cur.execute(
                """
                SELECT metric, COUNT(*) AS cnt, COALESCE(SUM(delta), 0) AS delta_sum
                FROM {}
                WHERE artist_mid = ? AND snapshot_db = ?
                GROUP BY metric
                ORDER BY metric ASC
                """.format(tbl_metric),
                (artist_mid, snapshot_file.as_posix()),
            ).fetchall()
            for metric, cnt, delta_sum in metric_rows:
                metric_field_changes.append(
                    {
                        "metric": str(metric or ""),
                        "count": int(cnt or 0),
                        "delta_sum": int(delta_sum or 0),
                    }
                )

            artist_metric_rows = cur.execute(
                """
                SELECT metric, COUNT(*) AS cnt, COALESCE(SUM(delta), 0) AS delta_sum
                FROM {}
                WHERE artist_mid = ? AND snapshot_db = ?
                GROUP BY metric
                ORDER BY metric ASC
                """.format(tbl_artist),
                (artist_mid, snapshot_file.as_posix()),
            ).fetchall()
            for metric, cnt, delta_sum in artist_metric_rows:
                artist_metric_field_changes.append(
                    {
                        "metric": str(metric or ""),
                        "count": int(cnt or 0),
                        "delta_sum": int(delta_sum or 0),
                    }
                )

            detail_rows = cur.execute(
                """
                SELECT song_name, song_mid, metric, old_value, new_value, delta
                FROM {}
                WHERE artist_mid = ? AND snapshot_db = ?
                ORDER BY ABS(delta) DESC, delta DESC, id DESC
                LIMIT 200
                """.format(tbl_metric),
                (artist_mid, snapshot_file.as_posix()),
            ).fetchall()
            for song_name, song_mid, metric, old_value, new_value, delta in detail_rows:
                metric_change_rows.append(
                    {
                        "song_name": str(song_name or song_mid or ""),
                        "song_mid": str(song_mid or ""),
                        "metric": str(metric or ""),
                        "old_value": int(old_value or 0),
                        "new_value": int(new_value or 0),
                        "delta": int(delta or 0),
                    }
                )

            artist_detail_rows = cur.execute(
                """
                SELECT artist_name, metric, old_value, new_value, delta
                FROM {}
                WHERE artist_mid = ? AND snapshot_db = ?
                ORDER BY ABS(delta) DESC, delta DESC, id DESC
                LIMIT 100
                """.format(tbl_artist),
                (artist_mid, snapshot_file.as_posix()),
            ).fetchall()
            for artist_name, metric, old_value, new_value, delta in artist_detail_rows:
                artist_metric_change_rows.append(
                    {
                        "artist_name": str(artist_name or artist_mid or ""),
                        "metric": str(metric or ""),
                        "old_value": int(old_value or 0),
                        "new_value": int(new_value or 0),
                        "delta": int(delta or 0),
                    }
                )
        finally:
            changes_conn.close()

        return {
            "ok": True,
            "artist_mid": artist_mid,
            "artist_name": resolved_name,
            "song_pages": song_pages,
            "requested_song_count": song_limit_safe,
            "target_song_count": target_song_count,
            "total_saved": total_saved,
            "metric_changes": result.get("metric_changes", 0),
            "artist_metric_changes": result.get("artist_metric_changes", 0),
            "metric_change_fields": metric_field_changes,
            "artist_metric_change_fields": artist_metric_field_changes,
            "metric_change_rows": metric_change_rows,
            "artist_metric_change_rows": artist_metric_change_rows,
            "snapshot_file": snapshot_file.as_posix(),
        }
    finally:
        _emit(100, "完成")
        client.close()


def get_report(
    platform: str,
    mode: str,
    value: str,
    artist_mid: str = "",
    song_display_limit: int = 15,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    mode_clean = (mode or "").strip()
    value_clean = (value or "").strip()
    if mode_clean not in ("year", "month", "day"):
        return {"ok": False, "error": "报告粒度必须是 year/month/day。"}
    if not value_clean:
        if mode_clean == "year":
            value_clean = str(datetime.now().year)
        elif mode_clean == "month":
            value_clean = datetime.now().strftime("%Y-%m")
        elif mode_clean == "day":
            value_clean = datetime.now().strftime("%Y-%m-%d")
    if not value_clean:
        return {"ok": False, "error": "请输入报告日期。"}

    date_str = None
    month_str = None
    year_str = None
    label = ""
    if mode_clean == "year":
        try:
            year_str = "{:04d}".format(int(value_clean))
        except ValueError:
            return {"ok": False, "error": "年份输入不合法。"}
        label = "年份"
    elif mode_clean == "month":
        try:
            month_str = datetime.strptime(value_clean, "%Y-%m").strftime("%Y-%m")
        except ValueError:
            return {"ok": False, "error": "月份输入不合法。"}
        label = "月份"
    else:
        try:
            date_str = datetime.strptime(value_clean, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": "日期输入不合法。"}
        label = "日期"

    changes_db_path = _resolve_changes_db_path(platform, base_dir)
    report = report_changes(
        changes_db_file=changes_db_path,
        date_str=date_str,
        month_str=month_str,
        year_str=year_str,
        artist_mid=(artist_mid or "").strip() or None,
        limit=100000,
    )
    metric_rows = report.get("metric_changes", [])
    artist_metric_rows = report.get("artist_metric_changes", [])

    comment_delta = 0
    favorite_delta = 0
    affected_song_mids = set()
    song_deltas: Dict[str, Dict[str, Any]] = {}
    for row in metric_rows:
        metric = str(row.get("metric") or "")
        delta = int(row.get("delta") or 0)
        run_at = str(row.get("run_at") or "")
        new_value = row.get("new_value")
        old_value = int(row.get("old_value") or 0)
        if old_value == 0:
            continue
        if delta <= 0:
            # 仅展示增长数据，过滤减少与无变化。
            continue
        if metric == "comment_count":
            comment_delta += delta
        elif metric == "favorite_count_text":
            favorite_delta += delta
        song_mid = str(row.get("song_mid") or "").strip()
        if not song_mid:
            continue
        affected_song_mids.add(song_mid)
        song_name = str(row.get("song_name") or song_mid).strip()
        if song_mid not in song_deltas:
            song_deltas[song_mid] = {
                "song_name": song_name,
                "comment": 0,
                "favorite": 0,
                "comment_new": None,
                "favorite_new": None,
                "comment_run_at": "",
                "favorite_run_at": "",
            }
        if song_name and song_name != song_mid:
            song_deltas[song_mid]["song_name"] = song_name
        if metric == "comment_count":
            song_deltas[song_mid]["comment"] += delta
            if run_at >= str(song_deltas[song_mid]["comment_run_at"]):
                song_deltas[song_mid]["comment_run_at"] = run_at
                song_deltas[song_mid]["comment_new"] = new_value
        elif metric == "favorite_count_text":
            song_deltas[song_mid]["favorite"] += delta
            if run_at >= str(song_deltas[song_mid]["favorite_run_at"]):
                song_deltas[song_mid]["favorite_run_at"] = run_at
                song_deltas[song_mid]["favorite_new"] = new_value

    fans_delta = 0
    affected_artist_mids = set()
    artist_deltas: Dict[str, Dict[str, Any]] = {}
    for row in artist_metric_rows:
        if str(row.get("metric") or "") != "fans":
            continue
        old_value = int(row.get("old_value") or 0)
        if old_value == 0:
            continue
        delta = int(row.get("delta") or 0)
        if delta <= 0:
            # 仅展示增长数据，过滤减少与无变化。
            continue
        run_at = str(row.get("run_at") or "")
        new_value = row.get("new_value")
        fans_delta += delta
        row_artist_mid = str(row.get("artist_mid") or "").strip()
        if row_artist_mid:
            affected_artist_mids.add(row_artist_mid)
        artist_name = str(row.get("artist_name") or row.get("artist_mid") or "").strip()
        if artist_name:
            if artist_name not in artist_deltas:
                artist_deltas[artist_name] = {"delta": 0, "new": None, "run_at": ""}
            artist_deltas[artist_name]["delta"] += delta
            if run_at >= str(artist_deltas[artist_name]["run_at"]):
                artist_deltas[artist_name]["run_at"] = run_at
                artist_deltas[artist_name]["new"] = new_value

    comment_items: List[Tuple[int, bool, str]] = []
    favorite_items: List[Tuple[int, bool, str]] = []
    comment_chart_rows: List[Dict[str, Any]] = []
    favorite_chart_rows: List[Dict[str, Any]] = []
    for song_mid, values in song_deltas.items():
        comment_new = values.get("comment_new")
        favorite_new = values.get("favorite_new")
        comment_delta_value = int(values.get("comment") or 0)
        favorite_delta_value = int(values.get("favorite") or 0)
        if comment_delta_value != 0:
            comment_items.append(
                (
                    abs(comment_delta_value),
                    comment_delta_value < 0,
                    "{}(评论{:+d}->{}) [{}]".format(
                        str(values.get("song_name") or song_mid),
                        comment_delta_value,
                        comment_new if comment_new is not None else "-",
                        song_mid,
                    ),
                )
            )
            comment_chart_rows.append(
                {
                    "name": str(values.get("song_name") or song_mid),
                    "song_mid": song_mid,
                    "delta": comment_delta_value,
                    "delta_abs": abs(comment_delta_value),
                    "new_value": comment_new,
                }
            )
        if favorite_delta_value != 0:
            favorite_items.append(
                (
                    abs(favorite_delta_value),
                    favorite_delta_value < 0,
                    "{}(收藏{:+d}->{}) [{}]".format(
                        str(values.get("song_name") or song_mid),
                        favorite_delta_value,
                        favorite_new if favorite_new is not None else "-",
                        song_mid,
                    ),
                )
            )
            favorite_chart_rows.append(
                {
                    "name": str(values.get("song_name") or song_mid),
                    "song_mid": song_mid,
                    "delta": favorite_delta_value,
                    "delta_abs": abs(favorite_delta_value),
                    "new_value": favorite_new,
                }
            )
    comment_items.sort(key=lambda x: (-x[0], x[1]))
    favorite_items.sort(key=lambda x: (-x[0], x[1]))
    comment_chart_rows.sort(key=lambda x: (-int(x.get("delta_abs") or 0), int(x.get("delta") or 0) < 0))
    favorite_chart_rows.sort(key=lambda x: (-int(x.get("delta_abs") or 0), int(x.get("delta") or 0) < 0))

    artist_items: List[Tuple[int, bool, str]] = []
    artist_chart_rows: List[Dict[str, Any]] = []
    for artist_name, values in artist_deltas.items():
        delta_value = int(values.get("delta") or 0)
        artist_items.append(
            (
                abs(delta_value),
                delta_value < 0,
                "{}(粉丝{:+d}->{})".format(
                    artist_name,
                    delta_value,
                    values.get("new") if values.get("new") is not None else "-",
                ),
            )
        )
        artist_chart_rows.append(
            {
                "name": artist_name,
                "delta": delta_value,
                "delta_abs": abs(delta_value),
                "new_value": values.get("new"),
            }
        )
    artist_items.sort(key=lambda x: (-x[0], x[1]))
    artist_chart_rows.sort(key=lambda x: (-int(x.get("delta_abs") or 0), int(x.get("delta") or 0) < 0))

    return {
        "ok": True,
        "label": label,
        "value": value_clean,
        "mode": mode_clean,
        "song_summary": {
            "affected_songs": len(affected_song_mids),
            "comment_delta": comment_delta,
            "favorite_delta": favorite_delta,
        },
        "artist_summary": {
            "affected_artists": len(affected_artist_mids),
            "fans_delta": fans_delta,
        },
        "song_names_comment": [x[2] for x in comment_items[:song_display_limit]],
        "song_names_favorite": [x[2] for x in favorite_items[:song_display_limit]],
        "artist_names": [x[2] for x in artist_items],
        "comment_chart_rows": comment_chart_rows[:song_display_limit],
        "favorite_chart_rows": favorite_chart_rows[:song_display_limit],
        "artist_chart_rows": artist_chart_rows[:song_display_limit],
    }


def get_milestone_logs(base_dir: Optional[Path] = None, limit: int = 500) -> Dict[str, Any]:
    """
    读取三平台收藏量里程碑日志，按时间倒序合并返回。
    日志行格式：YYYY-MM-DD HH:MM:SS 歌曲名 收藏量
    """
    root = base_dir or Path(".")
    entries: List[Dict[str, Any]] = []
    for platform in SUPPORTED_PLATFORMS:
        meta = get_platform_meta(platform)
        log_path = root / Path(meta["changes_db"]).parent / "milestone_{}.log".format(platform)
        if not log_path.is_file():
            continue
        name = meta.get("name", platform)
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    try:
                        ts = "{} {}".format(parts[0], parts[1])
                        count = int(parts[-1])
                        song_name = " ".join(parts[2:-1]) if len(parts) > 3 else parts[2]
                        entries.append(
                            {"platform": platform, "platform_name": name, "time": ts, "song_name": song_name, "favorite_count": count}
                        )
                    except (ValueError, IndexError):
                        continue
        except OSError:
            continue
    entries.sort(key=lambda x: x["time"], reverse=True)
    return {"ok": True, "entries": entries[:limit]}


def delete_milestone_entry(
    platform: str,
    time_str: str,
    song_name: str,
    favorite_count: int,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    从指定平台的里程碑 log 中删除一条记录（完全匹配：时间 + 歌曲名 + 收藏量）。
    日志行格式：YYYY-MM-DD HH:MM:SS 歌曲名 收藏量
    """
    root = (base_dir or Path(".")).resolve()
    meta = get_platform_meta(platform)
    log_path = root / Path(meta["changes_db"]).parent / "milestone_{}.log".format(platform)
    if not log_path.is_file():
        return {"ok": False, "error": "未找到日志文件: {}".format(log_path)}

    count_str = str(favorite_count)
    removed = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        kept = []
        for line in lines:
            raw = line.rstrip("\n\r")
            s = raw.strip()
            if not s:
                kept.append(line)
                continue
            parts = s.split()
            if len(parts) < 3:
                kept.append(line)
                continue
            line_time = "{} {}".format(parts[0], parts[1])
            line_count = parts[-1]
            line_song = " ".join(parts[2:-1]) if len(parts) > 3 else parts[2]
            if line_time == time_str and line_count == count_str and line_song == song_name:
                removed.append(raw)
                continue
            kept.append(line)
        if not removed:
            return {"ok": False, "error": "未找到匹配的记录"}
        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except OSError as e:
        return {"ok": False, "error": "读写日志失败: {}".format(e)}
    return {"ok": True, "removed": len(removed), "message": "已删除 1 条里程碑记录"}


def remove_milestone_outliers(
    platform: str,
    base_dir: Optional[Path] = None,
    threshold: int = 100,
) -> Dict[str, Any]:
    """
    剔除异常数据：对指定平台的变化表做「n-1 与 n+1 接近、n 异常」的修正，
    并删除里程碑 log 中对应异常收藏量记录。
    依赖 scripts/correct_kugou_metric_outliers.py 的 run()，三平台共用同一套表结构故均可调用。
    """
    root = (base_dir or Path(".")).resolve()
    meta = get_platform_meta(platform)
    changes_db = root / meta["changes_db"]
    if not changes_db.is_file():
        return {"ok": False, "error": "未找到变化库: {}".format(changes_db)}

    import sys
    scripts_dir = root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from correct_kugou_metric_outliers import run as run_outlier_correction
    except ImportError as e:
        return {"ok": False, "error": "无法加载修正脚本: {}".format(e)}

    result = run_outlier_correction(
        changes_db=changes_db,
        threshold=threshold,
        method="neighbor",
        dry_run=False,
        fix_snapshot=False,
    )
    if result.get("error"):
        return {"ok": False, "error": result["error"]}
    return {
        "ok": True,
        "updated": result.get("updated", 0),
        "removed_log_lines": result.get("removed_log_lines", 0),
        "message": "已修正 {} 条变化表记录，并从里程碑 log 中删除 {} 条异常记录。".format(
            result.get("updated", 0),
            result.get("removed_log_lines", 0),
        ),
    }


def get_report_chart_data(
    platform: str,
    mode: str,
    value: str,
    artist_mid: str = "",
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    获取变化折线图数据：年按月份聚合、月按日聚合、日按当天每次 run_at 聚合。
    返回 labels 与 series（comment / favorite / fans 的增量序列）。
    """
    mode_clean = (mode or "").strip()
    value_clean = (value or "").strip()
    if mode_clean not in ("year", "month", "day"):
        return {"ok": False, "error": "报告粒度必须是 year/month/day。"}
    if not value_clean:
        if mode_clean == "year":
            value_clean = str(datetime.now().year)
        elif mode_clean == "month":
            value_clean = datetime.now().strftime("%Y-%m")
        elif mode_clean == "day":
            value_clean = datetime.now().strftime("%Y-%m-%d")
    if not value_clean:
        return {"ok": False, "error": "请输入报告日期。"}

    date_str = None
    month_str = None
    year_str = None
    if mode_clean == "year":
        try:
            year_str = "{:04d}".format(int(value_clean))
        except ValueError:
            return {"ok": False, "error": "年份输入不合法。"}
    elif mode_clean == "month":
        try:
            month_str = datetime.strptime(value_clean, "%Y-%m").strftime("%Y-%m")
        except ValueError:
            return {"ok": False, "error": "月份输入不合法。"}
    else:
        try:
            date_str = datetime.strptime(value_clean, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": "日期输入不合法。"}

    db_path = _resolve_changes_db_path(platform, base_dir)
    if not db_path.is_file():
        empty = {"labels": [], "datasets": []}
        return {"ok": True, "labels": [], "series": {"comment": [], "favorite": [], "fans": []}, "song_comment": empty, "song_favorite": empty, "song_fans": empty}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_changes_tables(conn)
        month_keys_metric = _report_month_keys(
            conn, "metric_changes", year_str, month_str, date_str
        )
        month_keys_artist = _report_month_keys(
            conn, "artist_metric_changes", year_str, month_str, date_str
        )
        if not month_keys_metric:
            month_keys_metric = [datetime.now().strftime("%Y%m")]
        if not month_keys_artist:
            month_keys_artist = [datetime.now().strftime("%Y%m")]

        def _from_clause(base: str, keys: List[str]) -> str:
            if len(keys) == 1:
                return _table_name(base, keys[0])
            parts = [
                "SELECT * FROM {}".format(_table_name(base, mk)) for mk in keys
            ]
            return "({}) AS {}".format(" UNION ALL ".join(parts), base)

        metric_from = _from_clause("metric_changes", month_keys_metric)
        artist_from = _from_clause("artist_metric_changes", month_keys_artist)

        if year_str:
            where_sql = "substr(run_at, 1, 4) = ?"
            group_sql = "substr(run_at, 1, 7)"
            base_params: List[object] = [year_str]
        elif month_str:
            where_sql = "substr(run_at, 1, 7) = ?"
            group_sql = "date(run_at)"
            base_params = [month_str]
        else:
            where_sql = "date(run_at) = ?"
            group_sql = "run_at"
            base_params = [date_str or datetime.now().strftime("%Y-%m-%d")]

        params = list(base_params)
        if (artist_mid or "").strip():
            params.append((artist_mid or "").strip())

        artist_filter = " AND artist_mid = ?" if (artist_mid or "").strip() else ""

        labels_sql = (
            "SELECT {} AS period FROM {} WHERE {} {} GROUP BY period ORDER BY period".format(
                group_sql, metric_from, where_sql, artist_filter
            )
        )
        labels_rows = conn.execute(labels_sql, params).fetchall()
        labels = [str(r[0]) for r in labels_rows]

        if not labels:
            empty = {"labels": [], "datasets": []}
            return {"ok": True, "labels": [], "series": {"comment": [], "favorite": [], "fans": []}, "song_comment": empty, "song_favorite": empty, "song_fans": empty}

        series_comment: List[int] = []
        series_favorite: List[int] = []
        series_fans: List[int] = []

        for period in labels:
            if mode_clean == "day":
                period_where = "run_at = ?"
                period_params = [period]
            else:
                period_where = "substr(run_at, 1, {}) = ?".format(len(period))
                period_params = [period]
            if (artist_mid or "").strip():
                period_params = period_params + [(artist_mid or "").strip()]

            comment_row = conn.execute(
                """
                SELECT COALESCE(SUM(delta), 0) AS s
                FROM {}
                WHERE {} {} AND metric = 'comment_count'
                """.format(metric_from, period_where, artist_filter),
                period_params,
            ).fetchone()
            series_comment.append(int(comment_row[0] or 0))

            fav_row = conn.execute(
                """
                SELECT COALESCE(SUM(delta), 0) AS s
                FROM {}
                WHERE {} {} AND metric = 'favorite_count_text'
                """.format(metric_from, period_where, artist_filter),
                period_params,
            ).fetchone()
            series_favorite.append(int(fav_row[0] or 0))

            fans_row = conn.execute(
                """
                SELECT COALESCE(SUM(delta), 0) AS s
                FROM {}
                WHERE {} {} AND metric = 'fans'
                """.format(artist_from, period_where, artist_filter),
                period_params,
            ).fetchone()
            series_fans.append(int(fans_row[0] or 0))

        # 按歌曲维度的评论/收藏：每期按 song_mid 聚合，取总变化量最大的 top_songs 首
        top_songs = 10
        comment_by_song: Dict[str, List[int]] = {}  # song_mid -> [delta per period]
        favorite_by_song: Dict[str, List[int]] = {}
        song_names: Dict[str, str] = {}

        for period_idx, period in enumerate(labels):
            if mode_clean == "day":
                period_where = "run_at = ?"
                period_params = [period]
            else:
                period_where = "substr(run_at, 1, {}) = ?".format(len(period))
                period_params = [period]
            if (artist_mid or "").strip():
                period_params = period_params + [(artist_mid or "").strip()]

            for row in conn.execute(
                """
                SELECT song_mid, song_name, COALESCE(SUM(delta), 0) AS s
                FROM {}
                WHERE {} {} AND metric = 'comment_count'
                GROUP BY song_mid
                """.format(metric_from, period_where, artist_filter),
                period_params,
            ).fetchall():
                mid = str(row[0] or "").strip()
                if not mid:
                    continue
                name = str(row[1] or mid).strip() or mid
                delta = int(row[2] or 0)
                if mid not in song_names:
                    song_names[mid] = name
                if mid not in comment_by_song:
                    comment_by_song[mid] = [0] * len(labels)
                comment_by_song[mid][period_idx] = delta

            for row in conn.execute(
                """
                SELECT song_mid, song_name, COALESCE(SUM(delta), 0) AS s
                FROM {}
                WHERE {} {} AND metric = 'favorite_count_text'
                GROUP BY song_mid
                """.format(metric_from, period_where, artist_filter),
                period_params,
            ).fetchall():
                mid = str(row[0] or "").strip()
                if not mid:
                    continue
                name = str(row[1] or mid).strip() or mid
                delta = int(row[2] or 0)
                if mid not in song_names:
                    song_names[mid] = name
                if mid not in favorite_by_song:
                    favorite_by_song[mid] = [0] * len(labels)
                favorite_by_song[mid][period_idx] = delta

        def _top_datasets(
            by_song: Dict[str, List[int]],
            names: Dict[str, str],
            limit: int,
        ) -> List[Dict[str, Any]]:
            total_abs = [(mid, sum(abs(v) for v in vals)) for mid, vals in by_song.items()]
            total_abs.sort(key=lambda x: -x[1])
            datasets = []
            for mid, _ in total_abs[:limit]:
                name = names.get(mid, mid)
                if len(name) > 20:
                    name = name[:17] + "..."
                datasets.append({"name": name, "data": by_song[mid]})
            return datasets

        song_comment_datasets = _top_datasets(comment_by_song, song_names, top_songs)
        song_favorite_datasets = _top_datasets(favorite_by_song, song_names, top_songs)

        return {
            "ok": True,
            "labels": labels,
            "series": {
                "comment": series_comment,
                "favorite": series_favorite,
                "fans": series_fans,
            },
            "song_comment": {"labels": labels, "datasets": song_comment_datasets},
            "song_favorite": {"labels": labels, "datasets": song_favorite_datasets},
            "song_fans": {"labels": labels, "datasets": [{"name": "粉丝", "data": series_fans}]},
        }
    except (sqlite3.Error, ValueError, OSError) as e:
        return {"ok": False, "error": "数据查询异常: {}".format(str(e))}
    finally:
        conn.close()


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
    conn = sqlite3.connect(str(latest))
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


def check_artist_toplist(
    platform: str,
    artist_name: str,
    top_n: int = 300,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    top_n_safe = top_n if top_n and top_n > 0 else 300
    meta = get_platform_meta(platform)
    client = build_client(platform)
    service = CrawlerService(client=client)
    try:
        resolved = _resolve_artist(service, artist_name)
        if not resolved:
            return {"ok": False, "error": "未找到歌手，请重试。"}
        artist_mid, resolved_name = resolved

        hits = service.find_artist_toplist_hits(artist_mid=artist_mid, top_n=top_n_safe)
        db_file = _resolve_toplist_db_path(platform, base_dir)
        upserted = upsert_artist_toplist_hits(
            db_file=db_file,
            artist_mid=artist_mid,
            artist_name=resolved_name,
            hits=hits,
        )
        rows = query_artist_toplist_hits(db_file=db_file, artist_mid=artist_mid, limit=300)
        return {
            "ok": True,
            "artist_mid": artist_mid,
            "artist_name": resolved_name,
            "hits_count": len(hits),
            "upserted": upserted,
            "rows_count": len(rows),
            "rows": rows,
        }
    finally:
        client.close()


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

    conn = sqlite3.connect(str(latest))
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
