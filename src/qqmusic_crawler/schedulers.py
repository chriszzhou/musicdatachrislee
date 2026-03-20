"""
Web 进程内后台定时任务：榜单拉取、新歌页数据、crawl_track。

歌手名、间隔等来自 `config.settings`（项目根目录 `.env` + 可选 `.env.qqmc`），环境变量名仍为 QQMC_*。
"""

from __future__ import annotations

import threading
import time
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

_project_root: Optional[Path] = None
_crawl_track_last_cleanup_date: Optional[date_type] = None


def _root() -> Path:
    if _project_root is None:
        raise RuntimeError("schedulers 未初始化：请先调用 start_background_schedulers(project_root)")
    return _project_root


def run_scheduled_toplist_check() -> None:
    """对三平台依次执行上榜检查，结果写入各平台 toplist 库。"""
    root = _root()
    for platform in SUPPORTED_PLATFORMS:
        try:
            check_artist_toplist(
                platform=platform,
                artist_name=TOPLIST_ARTIST_NAME,
                top_n=300,
                base_dir=root,
            )
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
    while True:
        time.sleep(interval_sec)
        for platform in SUPPORTED_PLATFORMS:
            try:
                update_new_song_one_platform(platform, base_dir=root)
            except Exception as e:
                logger.warning(
                    "新歌页定时更新 {} 失败: {}",
                    platform,
                    e,
                    exc_info=True,
                )
        with NEW_SONG_LAST_UPDATE_LOCK:
            NEW_SONG_LAST_UPDATE_AT = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _run_crawl_track_round() -> None:
    global _crawl_track_last_cleanup_date
    root = _root()
    today = date_type.today()
    if _crawl_track_last_cleanup_date != today:
        _crawl_track_last_cleanup_date = today
        for platform in SUPPORTED_PLATFORMS:
            try:
                deleted = prune_old_snapshots(platform, keep_per_day=1, base_dir=root)
                if deleted > 0:
                    logger.info(
                        "定时抓取-快照清理 {} 已删除 {} 个旧快照",
                        get_platform_meta(platform)["name"],
                        deleted,
                    )
            except Exception as e:
                logger.warning("定时抓取-快照清理 {} 异常: {}", platform, e)
    for platform in SUPPORTED_PLATFORMS:
        try:
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
        except Exception as e:
            logger.warning("定时抓取 {} 异常: {}", platform, e, exc_info=True)

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
