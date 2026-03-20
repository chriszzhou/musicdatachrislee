from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from ..config import settings
from ..crawler import CrawlerService
from ..sqlite_util import connect_sqlite
from ..storage import Storage
from ..tracking import (
    _ensure_changes_tables,
    get_changes_table_for_run_at,
    track_changes_for_artist,
)

from .clients import build_client, _resolve_artist
from .paths import get_platform_meta

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
        changes_conn = connect_sqlite(Path(meta["changes_db"]))
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

