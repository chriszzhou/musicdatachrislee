#!/usr/bin/env python3
"""
查询某首歌的收藏数首次达到某值的时间（从 changes 库的 metric_changes 表）。
用法：
  python scripts/query_song_favorite_milestone.py "闭嘴跳舞" 20000 netease
  python scripts/query_song_favorite_milestone.py "闭嘴跳舞" 20000   # 默认目标 20000
  python scripts/query_song_favorite_milestone.py "闭嘴跳舞"         # 默认 20000 + 默认平台 qq
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_src = ROOT / "src"
if _src.exists() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from qqmusic_crawler.tracking import fetch_metric_changes_all

PLATFORM_DB = {
    "qq": ROOT / "data" / "qqmusic_changes.db",
    "netease": ROOT / "data" / "netease_changes.db",
    "kugou": ROOT / "data" / "kugou_changes.db",
}


def main() -> None:
    argv = [a.strip() for a in sys.argv[1:] if a.strip()]
    song_name = (argv[0] or "").strip()
    if not song_name:
        print("用法: python scripts/query_song_favorite_milestone.py <歌曲名> [目标收藏数] [平台]")
        print("平台: qq | netease | kugou，默认 qq")
        print("示例: python scripts/query_song_favorite_milestone.py 闭嘴跳舞 20000 netease")
        sys.exit(1)
    try:
        target = int(argv[1]) if len(argv) > 1 else 20000
    except (ValueError, IndexError):
        target = 20000
    platform = (argv[2] or "qq").lower() if len(argv) > 2 else "qq"
    if platform not in PLATFORM_DB:
        print("未知平台:", platform, "，可选: qq | netease | kugou")
        sys.exit(1)

    db_path = PLATFORM_DB[platform]
    if not db_path.is_file():
        print("未找到 changes 库:", db_path)
        print("请确认已跑过爬虫并存在该文件")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = fetch_metric_changes_all(
            conn,
            where_sql="metric = 'favorite_count_text' AND (song_name LIKE ? OR song_name = ?)",
            params=("%" + song_name + "%", song_name),
            order_asc=True,
        )
    finally:
        conn.close()

    if not rows:
        print("未找到歌曲「{}」在 {} 平台的收藏变化记录。".format(song_name, platform))
        print("可能原因：歌曲名不完全匹配、或该歌手尚未被爬取/未写入 changes。")
        sys.exit(0)

    # 找到首次 new_value >= target 的那次 run_at
    first_reach = None
    for r in rows:
        if int(r["new_value"]) >= target:
            first_reach = r
            break

    if first_reach:
        print("歌曲「{}」收藏数首次达到 {} 的时间（{} 平台，根据 changes 记录）：".format(song_name, target, platform))
        print("  run_at: {}".format(first_reach["run_at"]))
        print("  当时 new_value: {}, old_value: {}, delta: {}".format(
            first_reach["new_value"], first_reach["old_value"], first_reach["delta"]))
    else:
        print("歌曲「{}」在 {} 平台现有记录中尚未出现收藏数 >= {} 的条目。".format(song_name, platform, target))
        print("最近一条收藏变化：run_at={} new_value={}".format(rows[-1]["run_at"], rows[-1]["new_value"]))

    # 说明
    print("")
    if platform == "qq":
        print("说明：QQ 平台收藏里程碑阈值为 5k/1w/2w/5w/10w/…，首次突破 2 万会写里程碑。")
    else:
        print("说明：{} 平台收藏里程碑阈值为 1k/5k/1w/2w/3w/…，已包含 2w。".format(platform))
        print("      若页面上未看到该条里程碑，可能是：当时没有「上一份快照」可对比（首次爬该歌手），")
        print("      或跨过 2w 的那次运行未成功写入 data/milestone_{}.log。".format(platform))


if __name__ == "__main__":
    main()
