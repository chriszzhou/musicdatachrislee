#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定时任务脚本：依次对 QQ、网易云、酷狗 执行指定歌手的歌曲抓取并追踪。
默认歌手：李宇春。默认每 30 分钟执行一轮并一直后台运行。

后台常驻（推荐）：
  nohup python scheduled_crawl.py >> /var/log/scheduled_crawl.log 2>&1 &
  或使用 --interval 指定间隔分钟数。

只执行一次（供 cron 调用）：
  python scheduled_crawl.py --once
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from loguru import logger

# 确保能导入包（在 qqmusic-crawler 目录下执行，或已 pip install -e .）
try:
    from qqmusic_crawler.web_service import (
        SUPPORTED_PLATFORMS,
        crawl_track,
        get_platform_meta,
        prune_old_snapshots,
    )
except ImportError:
    _root = os.path.dirname(os.path.abspath(__file__))
    _src = os.path.join(_root, "src")
    if _src not in sys.path:
        sys.path.insert(0, _src)
    from qqmusic_crawler.web_service import (
        SUPPORTED_PLATFORMS,
        crawl_track,
        get_platform_meta,
        prune_old_snapshots,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="三平台定时抓取指定歌手歌曲并追踪变化")
    parser.add_argument(
        "--artist",
        default="李宇春",
        help="歌手名，默认 李宇春",
    )
    parser.add_argument(
        "--platforms",
        nargs="*",
        default=list(SUPPORTED_PLATFORMS),
        choices=list(SUPPORTED_PLATFORMS),
        help="要执行的平台，默认 qq netease kugou",
    )
    parser.add_argument(
        "--song-limit",
        type=int,
        default=None,
        metavar="N",
        help="每个平台最多抓取歌曲数，不传则抓取全部，最大 2000",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        metavar="MINUTES",
        help="每轮执行间隔（分钟），默认 30。仅在不使用 --once 时生效",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只执行一轮后退出（供 cron 等外部调度使用）",
    )
    args = parser.parse_args()

    SONG_LIMIT_MAX = 2000
    if args.song_limit is not None:
        if args.song_limit < 1:
            args.song_limit = None
        elif args.song_limit > SONG_LIMIT_MAX:
            args.song_limit = SONG_LIMIT_MAX
            logger.info("song_limit 已限制为最大值 {}", SONG_LIMIT_MAX)

    logger.remove()
    logger.add(sys.stderr, level="INFO")

    artist_name = (args.artist or "").strip()
    if not artist_name:
        logger.error("请提供歌手名（--artist）")
        return 1

    interval_seconds = max(1, args.interval) * 60
    run_count = 0
    project_root = Path(__file__).resolve().parent
    last_cleanup_date = None  # 每天执行一次快照清理

    def run_daily_cleanup() -> None:
        """每个自然日执行一次：三平台各只保留最新 2 个快照 DB，删除更早的。"""
        nonlocal last_cleanup_date
        today = date.today()
        if last_cleanup_date == today:
            return
        last_cleanup_date = today
        logger.info("执行每日快照清理：各平台每个日期只保留 1 个快照 DB")
        for platform in SUPPORTED_PLATFORMS:
            meta = get_platform_meta(platform)
            name = meta.get("name", platform)
            try:
                deleted = prune_old_snapshots(platform, keep_per_day=1, base_dir=project_root)
                if deleted > 0:
                    logger.info("{} 已删除 {} 个旧快照 DB", name, deleted)
            except Exception as e:
                logger.exception("{} 快照清理异常: {}", name, e)

    def do_one_round() -> None:
        nonlocal run_count
        run_count += 1
        run_daily_cleanup()
        started = datetime.now().isoformat()
        logger.info("第 {} 轮开始 artist={} platforms={} at {}", run_count, artist_name, args.platforms, started)
        for platform in args.platforms:
            meta = get_platform_meta(platform)
            name = meta.get("name", platform)
            logger.info("开始执行 {} ({})", name, platform)
            try:
                result = crawl_track(
                    platform=platform,
                    artist_name=artist_name,
                    song_limit=args.song_limit,
                )
                if result.get("ok"):
                    logger.info(
                        "{} 完成: 保存 {} 首, 歌曲指标变化 {}, 歌手指标变化 {}",
                        name,
                        result.get("total_saved", 0),
                        result.get("metric_changes", 0),
                        result.get("artist_metric_changes", 0),
                    )
                else:
                    logger.warning("{} 失败: {}", name, result.get("error", "未知错误"))
            except Exception as e:
                logger.exception("{} 异常: {}", name, e)
        logger.info("第 {} 轮结束 at {}", run_count, datetime.now().isoformat())

    if args.once:
        do_one_round()
        return 0

    logger.info("定时抓取已启动：距每轮开始间隔 {} 分钟执行下一轮，Ctrl+C 停止", args.interval)
    try:
        while True:
            round_start = time.monotonic()
            do_one_round()
            elapsed = time.monotonic() - round_start
            sleep_secs = max(0, interval_seconds - elapsed)
            if sleep_secs > 0:
                logger.info("下一轮将在本轮开始后 {} 分钟执行，等待 {:.1f} 秒...", args.interval, sleep_secs)
                time.sleep(sleep_secs)
            else:
                logger.info("本轮耗时已超过 {} 分钟，立即开始下一轮", args.interval)
    except KeyboardInterrupt:
        logger.info("已收到中断，退出")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
