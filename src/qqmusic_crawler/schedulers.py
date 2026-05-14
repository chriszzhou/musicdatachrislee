"""
Web 进程内后台定时任务：榜单拉取、新歌页数据、crawl_track。

歌手名、间隔等来自 `config.settings`（项目根目录 `.env` + 可选 `.env.qqmc`），环境变量名仍为 QQMC_*。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as date_type, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from .config import settings
from .web_service import (
    SUPPORTED_PLATFORMS,
    check_artist_toplist,
    crawl_track,
    get_platform_meta,
    prune_old_snapshots,
    update_new_song_one_platform,
)
from .tracking import compact_old_changes
from .web_service.milestones import run_kugou_outlier_correction_until_clean

BEIJING_TZ = timezone(timedelta(hours=8))

TOPLIST_ARTIST_NAME = (settings.qqmc_toplist_artist_name or "").strip() or "李宇春"
TOPLIST_SCHEDULE_START_HOUR = settings.qqmc_toplist_schedule_start_hour
TOPLIST_INTERVAL_MINUTES = settings.qqmc_toplist_interval_minutes
NEW_SONG_UPDATE_INTERVAL_SEC = settings.qqmc_new_song_update_interval_sec
CRAWL_TRACK_ARTIST_NAME = (settings.qqmc_crawl_track_artist_name or "").strip() or "李宇春"
CRAWL_TRACK_INTERVAL_MINUTES = settings.qqmc_crawl_track_interval_minutes

# 新歌页「上次更新时间」（API 读取；须在 schedulers 模块上访问以保持与后台线程一致）
NEW_SONG_LAST_UPDATE_AT: Optional[str] = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
NEW_SONG_LAST_UPDATE_LOCK = threading.Lock()

# crawl_track 一轮（三平台抓取 + 异常修正）完成时间（供首页按“任务完成”刷新）
CRAWL_TRACK_LAST_FINISHED_AT: Optional[str] = None
CRAWL_TRACK_LAST_FINISHED_LOCK = threading.Lock()

_project_root: Optional[Path] = None
_crawl_track_last_cleanup_date: Optional[date_type] = None


def _root() -> Path:
    if _project_root is None:
        raise RuntimeError("schedulers 未初始化：请先调用 start_background_schedulers(project_root)")
    return _project_root


def run_scheduled_toplist_check() -> None:
    """对三平台并发执行上榜检查，结果写入各平台 toplist 库。"""
    root = _root()

    def _check(platform):
        check_artist_toplist(
            platform=platform,
            artist_name=TOPLIST_ARTIST_NAME,
            top_n=300,
            base_dir=root,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_check, p): p for p in SUPPORTED_PLATFORMS}
        for f in as_completed(futures):
            try:
                f.result()
            except Exception:
                pass


def _toplist_scheduler_loop() -> None:
    last_slot: Optional[str] = None
    interval = TOPLIST_INTERVAL_MINUTES
    start_h = TOPLIST_SCHEDULE_START_HOUR
    while True:
        time.sleep(60)
        now = datetime.now(BEIJING_TZ)
        if now.hour < start_h:
            continue
        minute = now.minute
        if minute % interval != 0:
            continue
        aligned = (minute // interval) * interval
        slot = now.replace(minute=aligned, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        if slot == last_slot:
            continue
        last_slot = slot
        run_scheduled_toplist_check()


def _new_song_scheduler_loop() -> None:
    global NEW_SONG_LAST_UPDATE_AT
    root = _root()
    interval_sec = NEW_SONG_UPDATE_INTERVAL_SEC
    retry_sleep_seconds = (2, 5, 10)
    while True:
        time.sleep(interval_sec)

        def _update_platform(platform):
            for idx, sleep_sec in enumerate(retry_sleep_seconds, start=1):
                try:
                    update_new_song_one_platform(platform, base_dir=root)
                    return True
                except Exception as e:
                    is_last = idx == len(retry_sleep_seconds)
                    if is_last:
                        logger.warning(
                            "新歌页定时更新 {} 失败(已重试{}次): {}",
                            platform,
                            len(retry_sleep_seconds),
                            e,
                            exc_info=True,
                        )
                    else:
                        logger.warning(
                            "新歌页定时更新 {} 失败(第{}/{}次): {}，{}秒后重试",
                            platform,
                            idx,
                            len(retry_sleep_seconds),
                            e,
                            sleep_sec,
                        )
                        time.sleep(sleep_sec)
            return False

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_update_platform, p): p for p in SUPPORTED_PLATFORMS}
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception:
                    pass

        with NEW_SONG_LAST_UPDATE_LOCK:
            NEW_SONG_LAST_UPDATE_AT = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _run_crawl_track_round() -> None:
    global _crawl_track_last_cleanup_date, CRAWL_TRACK_LAST_FINISHED_AT
    root = _root()
    today = date_type.today()
    if _crawl_track_last_cleanup_date != today:
        _crawl_track_last_cleanup_date = today

        def _cleanup(platform):
            deleted = prune_old_snapshots(platform, keep_per_day=1, base_dir=root)
            if deleted > 0:
                logger.info(
                    "定时抓取-快照清理 {} 已删除 {} 个旧快照",
                    get_platform_meta(platform)["name"],
                    deleted,
                )

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_cleanup, p): p for p in SUPPORTED_PLATFORMS}
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    logger.warning("定时抓取-快照清理异常: {}", e)

        # 变化表清理：一周前的数据每天只保留一条合并记录
        def _compact(platform):
            from .web_service.paths import _resolve_changes_db_path
            db = _resolve_changes_db_path(platform, root)
            compacted = compact_old_changes(db)
            total_del = sum(compacted.values())
            if total_del > 0:
                logger.info(
                    "定时抓取-变化表清理 {} 已合并删除 {} 条旧记录",
                    get_platform_meta(platform)["name"],
                    total_del,
                )

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_compact, p): p for p in SUPPORTED_PLATFORMS}
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    logger.warning("定时抓取-变化表清理异常: {}", e)

    def _crawl_one(platform):
        result = crawl_track(
            platform=platform,
            artist_name=CRAWL_TRACK_ARTIST_NAME,
            song_limit=None,
        )
        if result.get("ok"):
            logger.info(
                "定时抓取 {} 完成: 保存 {} 首, 歌曲指标变化 {}, 歌手指标变化 {}",
                get_platform_meta(platform)["name"],
                result.get("total_saved", 0),
                result.get("metric_changes", 0),
                result.get("artist_metric_changes", 0),
            )
        else:
            logger.warning("定时抓取 {} 失败: {}", platform, result.get("error", "未知错误"))

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_crawl_one, p): p for p in SUPPORTED_PLATFORMS}
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                logger.warning("定时抓取异常: {}", e, exc_info=True)

    # 三平台本轮 crawl_track 结束后：自动对酷狗变化库做多轮异常修正，直到本轮无修正
    try:
        oc = run_kugou_outlier_correction_until_clean(base_dir=root, threshold=100, max_rounds=50)
        if oc.get("ok"):
            if oc.get("total_updated", 0) or oc.get("total_removed_log_lines", 0):
                logger.info(
                    "酷狗异常修正完成: {} 轮, 累计修正 {} 条变化表, 里程碑 log 删除 {} 行",
                    oc.get("rounds", 0),
                    oc.get("total_updated", 0),
                    oc.get("total_removed_log_lines", 0),
                )
        else:
            logger.warning("酷狗异常修正未执行: {}", oc.get("error", ""))
    except Exception as e:
        logger.warning("酷狗异常修正异常: {}", e, exc_info=True)

    # 标记本轮结束（已完成三平台抓取及后处理）
    with CRAWL_TRACK_LAST_FINISHED_LOCK:
        CRAWL_TRACK_LAST_FINISHED_AT = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _crawl_track_scheduler_loop() -> None:
    last_slot: Optional[str] = None
    interval = CRAWL_TRACK_INTERVAL_MINUTES
    while True:
        time.sleep(60)
        now = datetime.now(BEIJING_TZ)
        minute = now.minute
        if minute % interval != 0:
            continue
        slot = now.replace(minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        if slot == last_slot:
            continue
        last_slot = slot
        logger.info("定时抓取开始 at {}", slot)
        _run_crawl_track_round()
        logger.info("定时抓取结束 at {}", datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"))


def start_background_schedulers(project_root: Path) -> None:
    """在 Web 启动时调用：设置数据根目录并启动三个 daemon 线程。"""
    global _project_root
    _project_root = project_root.resolve()

    threading.Thread(target=_toplist_scheduler_loop, daemon=True).start()
    threading.Thread(target=_new_song_scheduler_loop, daemon=True).start()
    threading.Thread(target=_crawl_track_scheduler_loop, daemon=True).start()
