#!/usr/bin/env python3
"""
从各平台 changes 表的 metric_changes 中找出符合收藏里程碑条件的记录，
追加到 data/milestone_<platform>.log。已存在的行（相同 run_at + 歌曲名 + 收藏数）会跳过，可重复执行。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 先加 src 以便 import 包
_src = ROOT / "src"
if _src.exists() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from qqmusic_crawler.web_service import get_platform_meta
from qqmusic_crawler.tracking import _favorite_milestone_should_log, fetch_metric_changes_all

SUPPORTED_PLATFORMS = ("qq", "netease", "kugou")


def _parse_existing_log(log_path: Path) -> set[tuple[str, str, int]]:
    """解析已有里程碑日志，返回 set of (run_at, song_name, count)。"""
    seen: set[tuple[str, str, int]] = set()
    if not log_path.is_file():
        return seen
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
                seen.add((ts, song_name, count))
            except (ValueError, IndexError):
                continue
    return seen


def _append_milestone_line(log_path: Path, run_at: str, song_name: str, count: int) -> None:
    """追加一行到里程碑日志，使用 run_at 作为时间戳。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    name_safe = (song_name or "").strip().replace("\n", " ").replace("\r", " ") or "-"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("{} {} {}\n".format(run_at, name_safe, count))


def main() -> int:
    base_dir = Path(os.environ.get("QQMUSIC_CRAWLER_BASE", str(ROOT)))
    total_appended = 0

    for platform in SUPPORTED_PLATFORMS:
        meta = get_platform_meta(platform)
        changes_db = base_dir / meta["changes_db"]
        log_path = base_dir / Path(meta["changes_db"]).parent / "milestone_{}.log".format(platform)

        if not changes_db.is_file():
            print("跳过 {}: 未找到 {}".format(meta.get("name", platform), changes_db))
            continue

        existing = _parse_existing_log(log_path)
        appended = 0

        import sqlite3
        conn = sqlite3.connect(str(changes_db))
        conn.row_factory = sqlite3.Row
        try:
            rows = fetch_metric_changes_all(
                conn,
                where_sql="metric = 'favorite_count_text'",
                params=(),
                order_asc=True,
            )
            for row in rows:
                run_at = str(row["run_at"] or "").strip()
                song_name = str(row["song_name"] or row["song_mid"] or "").strip()
                old_v = int(row["old_value"] or 0)
                new_v = int(row["new_value"] or 0)
                delta = int(row["delta"] or 0)
                if not _favorite_milestone_should_log(platform, old_v, new_v, delta):
                    continue
                key = (run_at, song_name, new_v)
                if key in existing:
                    continue
                _append_milestone_line(log_path, run_at, song_name, new_v)
                existing.add(key)
                appended += 1
        finally:
            conn.close()

        if appended > 0:
            print("{}: 追加 {} 条到 {}".format(meta.get("name", platform), appended, log_path))
            total_appended += appended

    if total_appended == 0:
        print("未发现需要补写的里程碑记录（可能均已存在）。")
    else:
        print("共追加 {} 条里程碑。".format(total_appended))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
