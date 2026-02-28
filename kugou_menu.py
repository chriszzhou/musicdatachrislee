from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from loguru import logger

from qqmusic_crawler.config import settings
from qqmusic_crawler.crawler import CrawlerService
from qqmusic_crawler.kugou_client import KugouMusicClient
from qqmusic_crawler.storage import Storage
from qqmusic_crawler.toplist_storage import query_artist_toplist_hits, upsert_artist_toplist_hits
from qqmusic_crawler.tracking import report_changes, track_changes_for_artist


def _build_client() -> KugouMusicClient:
    return KugouMusicClient(
        base_url=settings.kugou_base_url,
        timeout=settings.qqmusic_timeout,
        max_retries=settings.qqmusic_max_retries,
        rate_limit_qps=settings.kugou_rate_limit_qps,
        metric_workers=settings.kugou_metric_workers,
        metric_batch_size=settings.kugou_metric_batch_size,
    )


def _build_snapshot_service(database_url: str) -> Tuple[KugouMusicClient, CrawlerService]:
    storage = Storage(database_url)
    storage.create_tables()
    client = _build_client()
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


def _run_crawl_track_auto_pages(base_client: KugouMusicClient, base_service: CrawlerService) -> None:
    artist_name = input("请输入歌手名: ").strip()
    resolved = _resolve_artist(base_service, artist_name)
    if not resolved:
        print("未找到歌手，请重试。")
        return
    artist_mid, resolved_name = resolved
    profile = base_client.fetch_artist_profile(artist_mid)
    page_size = settings.qqmusic_default_song_page_size
    total_song = int(profile.get("total_song") or 0)
    song_pages = (total_song + page_size - 1) // page_size if total_song > 0 else 200
    if song_pages <= 0:
        song_pages = 1

    snapshots_dir = Path("data/kugou_snapshots")
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_file = snapshots_dir / "kugou_{}_{}.db".format(
        artist_mid, datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    snapshot_db_url = "sqlite:///{}".format(snapshot_file.as_posix())

    snap_client, snap_service = _build_snapshot_service(snapshot_db_url)
    try:
        total_saved = snap_service.crawl_songs_for_artists(
            artist_mids=[artist_mid],
            song_pages=song_pages,
            page_size=page_size,
        )
    finally:
        snap_client.close()

    result = track_changes_for_artist(
        snapshots_dir=snapshots_dir,
        current_snapshot_file=snapshot_file,
        changes_db_file=Path("data/kugou_changes.db"),
        artist_mid=artist_mid,
    )
    print(
        "完成: 歌手={}({}), 自动页数={}, 保存歌曲={}, metric_changes={}, artist_metric_changes={}".format(
            resolved_name,
            artist_mid,
            song_pages,
            total_saved,
            result.get("metric_changes", 0),
            result.get("artist_metric_changes", 0),
        )
    )
    print("快照库: {}".format(snapshot_file.as_posix()))


def _run_report_menu() -> None:
    song_display_limit = 15
    print("请选择变化报告粒度: 1) 年  2) 月  3) 日")
    mode = input("请输入选项(1/2/3): ").strip()
    date_str = None
    month_str = None
    year_str = None
    label = ""
    value = ""
    if mode == "1":
        year_raw = input("请输入年份(YYYY): ").strip()
        try:
            year_str = "{:04d}".format(int(year_raw))
        except ValueError:
            print("年份输入不合法。")
            return
        label = "年份"
        value = year_str
    elif mode == "2":
        ym_raw = input("请输入月份(YYYY-MM): ").strip()
        try:
            dt = datetime.strptime(ym_raw, "%Y-%m")
            month_str = dt.strftime("%Y-%m")
        except ValueError:
            print("月份输入不合法。")
            return
        label = "月份"
        value = month_str
    elif mode == "3":
        ymd_raw = input("请输入日期(YYYY-MM-DD): ").strip()
        try:
            dt = datetime.strptime(ymd_raw, "%Y-%m-%d")
            date_str = dt.strftime("%Y-%m-%d")
        except ValueError:
            print("日期输入不合法。")
            return
        label = "日期"
        value = date_str
    else:
        print("无效选择。")
        return

    report = report_changes(
        changes_db_file=Path("data/kugou_changes.db"),
        date_str=date_str,
        month_str=month_str,
        year_str=year_str,
        artist_mid=None,
        limit=100000,
    )
    metric_rows = report.get("metric_changes", [])
    artist_metric_rows = report.get("artist_metric_changes", [])

    comment_delta = 0
    favorite_delta = 0
    affected_song_mids = set()
    song_deltas = {}
    for row in metric_rows:
        metric = str(row.get("metric") or "")
        delta = int(row.get("delta") or 0)
        run_at = str(row.get("run_at") or "")
        new_value = row.get("new_value")
        old_value = int(row.get("old_value") or 0)
        if old_value == 0:
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
    artist_deltas = {}
    for row in artist_metric_rows:
        if str(row.get("metric") or "") != "fans":
            continue
        old_value = int(row.get("old_value") or 0)
        if old_value == 0:
            continue
        delta = int(row.get("delta") or 0)
        run_at = str(row.get("run_at") or "")
        new_value = row.get("new_value")
        fans_delta += delta
        artist_mid = str(row.get("artist_mid") or "").strip()
        if artist_mid:
            affected_artist_mids.add(artist_mid)
        artist_name = str(row.get("artist_name") or row.get("artist_mid") or "").strip()
        if artist_name:
            if artist_name not in artist_deltas:
                artist_deltas[artist_name] = {"delta": 0, "new": None, "run_at": ""}
            artist_deltas[artist_name]["delta"] += delta
            if run_at >= str(artist_deltas[artist_name]["run_at"]):
                artist_deltas[artist_name]["run_at"] = run_at
                artist_deltas[artist_name]["new"] = new_value

    print("{} {} 变化汇总:".format(label, value))
    print(
        "[song-summary] 影响歌曲={} 首 | 评论总变化={} | 收藏总变化={}".format(
            len(affected_song_mids), comment_delta, favorite_delta
        )
    )
    print(
        "[artist-summary] 影响歌手={} 位 | 粉丝总变化={}".format(
            len(affected_artist_mids), fans_delta
        )
    )

    comment_items = []
    favorite_items = []
    for song_mid, values in song_deltas.items():
        comment_delta_value = int(values.get("comment") or 0)
        favorite_delta_value = int(values.get("favorite") or 0)
        if comment_delta_value != 0:
            comment_items.append(
                (
                    abs(comment_delta_value),
                    comment_delta_value < 0,
                    "{}(评论{:+d}->{}) [{}]".format(
                        values.get("song_name") or song_mid,
                        comment_delta_value,
                        values.get("comment_new") or "-",
                        song_mid,
                    ),
                )
            )
        if favorite_delta_value != 0:
            favorite_items.append(
                (
                    abs(favorite_delta_value),
                    favorite_delta_value < 0,
                    "{}(收藏{:+d}->{}) [{}]".format(
                        values.get("song_name") or song_mid,
                        favorite_delta_value,
                        values.get("favorite_new") or "-",
                        song_mid,
                    ),
                )
            )
    comment_items.sort(key=lambda x: (-x[0], x[1]))
    favorite_items.sort(key=lambda x: (-x[0], x[1]))

    artist_items = []
    for artist_name, values in artist_deltas.items():
        delta_value = int(values.get("delta") or 0)
        artist_items.append(
            (
                abs(delta_value),
                delta_value < 0,
                "{}(粉丝{:+d}->{})".format(artist_name, delta_value, values.get("new") or "-"),
            )
        )
    artist_items.sort(key=lambda x: (-x[0], x[1]))

    if comment_items:
        print("[song-names-comment]")
        for _, _, text in comment_items[:song_display_limit]:
            print("- {}".format(text))
    else:
        print("[song-names-comment] 无")
    if favorite_items:
        print("[song-names-favorite]")
        for _, _, text in favorite_items[:song_display_limit]:
            print("- {}".format(text))
    else:
        print("[song-names-favorite] 无")
    if artist_items:
        print("[artist-names]")
        for _, _, text in artist_items:
            print("- {}".format(text))
    else:
        print("[artist-names] 无")


def _run_check_artist_toplist(base_service: CrawlerService) -> None:
    artist_name = input("请输入歌手名: ").strip()
    resolved = _resolve_artist(base_service, artist_name)
    if not resolved:
        print("未找到歌手，请重试。")
        return
    artist_mid, resolved_name = resolved
    top_n_raw = input("每个榜单抓前N首(默认100): ").strip()
    try:
        top_n = int(top_n_raw) if top_n_raw else 100
    except ValueError:
        top_n = 100
    if top_n <= 0:
        top_n = 100

    hits = base_service.find_artist_toplist_hits(artist_mid=artist_mid, top_n=top_n)
    db_file = Path("data/kugou_toplist.db")
    upserted = upsert_artist_toplist_hits(
        db_file=db_file,
        artist_mid=artist_mid,
        artist_name=resolved_name,
        hits=hits,
    )
    rows = query_artist_toplist_hits(db_file=db_file, artist_mid=artist_mid, limit=300)
    print("完成: 歌手={}({}), 本次命中={}, 入库/更新={}, 库内总记录={}".format(resolved_name, artist_mid, len(hits), upserted, len(rows)))
    for row in rows:
        print(
            "[toplist] {}({}) #{} {} | song={} ({}) | first_seen={} last_seen={}".format(
                row.get("top_name"),
                row.get("top_id"),
                row.get("rank"),
                row.get("top_period") or row.get("top_update_time"),
                row.get("song_name"),
                row.get("song_mid"),
                row.get("first_seen_at"),
                row.get("last_seen_at"),
            )
        )


def _run_top_songs_menu(base_service: CrawlerService) -> None:
    artist_name = input("请输入歌手名: ").strip()
    resolved = _resolve_artist(base_service, artist_name)
    if not resolved:
        print("未找到歌手，请重试。")
        return
    artist_mid, resolved_name = resolved
    n_raw = input("请输入N(默认15): ").strip()
    try:
        top_n = int(n_raw) if n_raw else 15
    except ValueError:
        top_n = 15
    if top_n <= 0:
        top_n = 15

    snapshots_dir = Path("data/kugou_snapshots")
    candidates = sorted(snapshots_dir.glob("kugou_{}_*.db".format(artist_mid)))
    if not candidates:
        print("未找到该歌手快照，请先执行抓取。")
        return
    latest = max(candidates, key=lambda p: p.stat().st_mtime)

    conn = sqlite3.connect(str(latest))
    try:
        cur = conn.cursor()
        fav_rows = cur.execute(
            """
            SELECT song_mid, name, COALESCE(favorite_count_text, 0) AS favorite_count_text
            FROM songs
            WHERE artist_mid = ?
            ORDER BY favorite_count_text DESC, song_mid ASC
            LIMIT ?
            """,
            (artist_mid, top_n),
        ).fetchall()
        comment_rows = cur.execute(
            """
            SELECT song_mid, name, COALESCE(comment_count, 0) AS comment_count
            FROM songs
            WHERE artist_mid = ?
            ORDER BY comment_count DESC, song_mid ASC
            LIMIT ?
            """,
            (artist_mid, top_n),
        ).fetchall()
    finally:
        conn.close()

    print("歌手={}({}) | 快照={}".format(resolved_name, artist_mid, latest.name))
    if fav_rows:
        print("[top-favorite]")
        for idx, row in enumerate(fav_rows, start=1):
            print("{}. {} [{}] 收藏={}".format(idx, row[1] or row[0], row[0], row[2] or 0))
    else:
        print("[top-favorite] 无")
    if comment_rows:
        print("[top-comment]")
        for idx, row in enumerate(comment_rows, start=1):
            print("{}. {} [{}] 评论={}".format(idx, row[1] or row[0], row[0], row[2] or 0))
    else:
        print("[top-comment] 无")


def main() -> int:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    client = _build_client()
    service = CrawlerService(client=client)
    try:
        while True:
            print("\n===== 酷狗抓取菜单 =====")
            print("1) 查看歌手歌曲信息（自动最大页 crawl-track）")
            print("2) 查看变化报告（先选年/月/日，再输入日期）")
            print("3) 查询歌手是否有歌曲上榜")
            print("4) 查看歌曲TOP-N（收藏/评论）")
            print("0) 退出")
            choice = input("请选择功能: ").strip()
            if choice == "1":
                _run_crawl_track_auto_pages(client, service)
            elif choice == "2":
                _run_report_menu()
            elif choice == "3":
                _run_check_artist_toplist(service)
            elif choice == "4":
                _run_top_songs_menu(service)
            elif choice == "0":
                print("已退出。")
                return 0
            else:
                print("无效选择，请重新输入。")
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
