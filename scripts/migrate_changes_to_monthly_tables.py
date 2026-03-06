#!/usr/bin/env python3
"""
将旧版 changes 库中的单表 metric_changes / artist_metric_changes 迁移为按月分表。
迁移后原表会被删除，仅保留 metric_changes_mYYYYMM / artist_metric_changes_mYYYYMM。
可对同一库重复执行（若已是分表则跳过）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_src = ROOT / "src"
if _src.exists() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import sqlite3

from qqmusic_crawler.tracking import (
    _ensure_month_table,
    _has_legacy_metric_changes_table,
    _month_key,
    _table_name,
)
from qqmusic_crawler.web_service import get_platform_meta

SUPPORTED_PLATFORMS = ("qq", "netease", "kugou")


def _has_legacy_artist_table(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artist_metric_changes'"
        ).fetchone()
        is not None
    )


def _migrate_metric_changes(conn: sqlite3.Connection) -> int:
    if not _has_legacy_metric_changes_table(conn):
        return 0
    rows = conn.execute(
        """
        SELECT run_at, artist_mid, song_mid, song_name, metric,
               old_value, new_value, delta, snapshot_db
        FROM metric_changes
        ORDER BY run_at
        """
    ).fetchall()
    if not rows:
        conn.execute("DROP TABLE metric_changes")
        conn.commit()
        return 0
    by_month: dict[str, list] = {}
    for r in rows:
        mk = _month_key(r[0])
        by_month.setdefault(mk, []).append(r)
    for mk, month_rows in by_month.items():
        _ensure_month_table(conn, "metric_changes", mk)
        table = _table_name("metric_changes", mk)
        conn.executemany(
            """
            INSERT INTO {} (
                run_at, artist_mid, song_mid, song_name, metric,
                old_value, new_value, delta, snapshot_db
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.format(table),
            month_rows,
        )
    conn.execute("DROP TABLE metric_changes")
    conn.commit()
    return sum(len(v) for v in by_month.values())


def _migrate_artist_metric_changes(conn: sqlite3.Connection) -> int:
    if not _has_legacy_artist_table(conn):
        return 0
    rows = conn.execute(
        """
        SELECT run_at, artist_mid, artist_name, metric,
               old_value, new_value, delta, snapshot_db
        FROM artist_metric_changes
        ORDER BY run_at
        """
    ).fetchall()
    if not rows:
        conn.execute("DROP TABLE artist_metric_changes")
        conn.commit()
        return 0
    by_month: dict[str, list] = {}
    for r in rows:
        mk = _month_key(r[0])
        by_month.setdefault(mk, []).append(r)
    for mk, month_rows in by_month.items():
        _ensure_month_table(conn, "artist_metric_changes", mk)
        table = _table_name("artist_metric_changes", mk)
        conn.executemany(
            """
            INSERT INTO {} (
                run_at, artist_mid, artist_name, metric,
                old_value, new_value, delta, snapshot_db
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """.format(table),
            month_rows,
        )
    conn.execute("DROP TABLE artist_metric_changes")
    conn.commit()
    return sum(len(v) for v in by_month.values())


def main() -> int:
    base_dir = Path(os.environ.get("QQMUSIC_CRAWLER_BASE", str(ROOT)))
    total_metric = 0
    total_artist = 0
    for platform in SUPPORTED_PLATFORMS:
        meta = get_platform_meta(platform)
        db_path = base_dir / meta["changes_db"]
        if not db_path.is_file():
            print("跳过 {}: 未找到 {}".format(meta.get("name", platform), db_path))
            continue
        conn = sqlite3.connect(str(db_path))
        try:
            n_metric = _migrate_metric_changes(conn)
            n_artist = _migrate_artist_metric_changes(conn)
            if n_metric or n_artist:
                print(
                    "{}: metric_changes {} 条 -> 分表, artist_metric_changes {} 条 -> 分表".format(
                        meta.get("name", platform), n_metric, n_artist
                    )
                )
                total_metric += n_metric
                total_artist += n_artist
            else:
                print("{}: 已是分表或表为空，未迁移。".format(meta.get("name", platform)))
        finally:
            conn.close()
    if total_metric == 0 and total_artist == 0:
        print("未发现需要迁移的旧版单表。")
    else:
        print("迁移完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
