"""
修正各平台变化库中「收藏 / 评论」因抓取异常导致的突变值（三平台表结构一致）。

规则：
  - 对同一 (artist_mid, song_mid, metric) 按 run_at 排序得到序列 v[0], v[1], ...
  - 若 |v[n]-v[n-1]| > threshold 且 |v[n]-v[n+1]| > threshold，且 |v[n-1]-v[n+1]| <= threshold，
    则判定 v[n] 为异常，按 method 修正
  - 可选修正快照库 songs 表；并清理 milestone_*.log 中对应异常收藏记录

由 web_service.remove_milestone_outliers / 定时任务 run_kugou_outlier_correction_until_clean 调用 run()；也可命令行：

  python -m qqmusic_crawler.metric_outlier_correction --changes-db data/kugou_changes.db
  python -m qqmusic_crawler.metric_outlier_correction --threshold 200 --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .sqlite_util import connect_sqlite


def _repo_root() -> Path:
    """仓库根目录（含 data/、src/ 的目录）。"""
    return Path(__file__).resolve().parents[2]


def _platform_from_changes_db(changes_db: Path) -> str:
    n = changes_db.name.lower()
    if "netease" in n:
        return "netease"
    if "kugou" in n:
        return "kugou"
    return "qq"


def _list_metric_change_tables(conn: sqlite3.Connection) -> List[str]:
    tables: List[str] = []
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metric_changes'"
    ).fetchone():
        tables.append("metric_changes")
    prefix = "metric_changes_m"
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?",
        (prefix + "%",),
    ).fetchall():
        if name.startswith(prefix) and len(name) > len(prefix):
            tables.append(name)
    tables.sort(key=lambda t: (0, t) if t == "metric_changes" else (1, t))
    return tables


def _fetch_all_rows(conn: sqlite3.Connection) -> List[Tuple[str, int, str, str, str, str, str, int, int, int, str]]:
    """返回 (table, id, run_at, artist_mid, song_mid, song_name, metric, old_value, new_value, delta, snapshot_db)。"""
    out: List[Tuple[str, int, str, str, str, str, str, int, int, int, str]] = []
    for table in _list_metric_change_tables(conn):
        try:
            rows = conn.execute(
                """
                SELECT id, run_at, artist_mid, song_mid, song_name, metric,
                       old_value, new_value, delta, snapshot_db
                FROM {}
                WHERE metric IN ('comment_count', 'favorite_count_text')
                ORDER BY run_at ASC
                """.format(table),
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for r in rows:
            out.append((table, r[0], r[1], r[2], r[3], r[4] or "", r[5], r[6], r[7], r[8], r[9]))
    return out


def _group_by_series(
    rows: List[Tuple[str, int, str, str, str, str, str, int, int, int, str]]
) -> Dict[Tuple[str, str, str], List[Tuple[str, int, str, int, int, int, str, str, str]]]:
    """按 (artist_mid, song_mid, metric) 分组。每项 (table, id, run_at, old_v, new_v, delta, snapshot_db, metric, song_name)。"""
    key_to_list: Dict[Tuple[str, str, str], List[Tuple[str, int, str, int, int, int, str, str, str]]] = {}
    for table, id_, run_at, artist_mid, song_mid, song_name, metric, old_v, new_v, delta, snapshot_db in rows:
        key = (artist_mid, song_mid, metric)
        key_to_list.setdefault(key, []).append((table, id_, run_at, old_v, new_v, delta, snapshot_db, metric, song_name))
    for key in key_to_list:
        key_to_list[key].sort(key=lambda x: x[2])  # run_at
    return key_to_list


def _corrected_value(method: str, v_prev: int, v_curr: int, v_next: int) -> int:
    if method == "neighbor":
        return v_prev
    if method == "median":
        return int(sorted([v_prev, v_curr, v_next])[1])
    if method == "neighbor_avg":
        return int(round((v_prev + v_next) / 2.0))
    return v_prev


def _find_outliers(
    series: List[Tuple[str, int, str, int, int, int, str, str, str]],
    threshold: int,
    method: str,
    artist_mid: str,
    song_mid: str,
) -> List[Tuple[int, int, str, int, str, str, str, str, int]]:
    """返回 (index, row_id, table, corrected_value, snapshot_db, artist_mid, song_mid, metric, wrong_new_value)。"""
    fixes: List[Tuple[int, int, str, int, str, str, str, str, int]] = []
    n = len(series)
    for i in range(1, n - 1):
        table, id_, run_at, old_v, new_v, delta, snapshot_db, metric, song_name = series[i]
        v_prev = series[i - 1][4]
        v_curr = new_v
        v_next_val = series[i + 1][4]
        if abs(v_curr - v_prev) <= threshold and abs(v_curr - v_next_val) <= threshold:
            continue
        if abs(v_prev - v_next_val) > threshold:
            continue
        if abs(v_curr - v_prev) <= threshold or abs(v_curr - v_next_val) <= threshold:
            continue
        corrected = _corrected_value(method, v_prev, v_curr, v_next_val)
        fixes.append((i, id_, table, corrected, snapshot_db, artist_mid, song_mid, metric, new_v))
    return fixes


def run(
    changes_db: Path,
    threshold: int = 100,
    method: str = "neighbor",
    dry_run: bool = False,
    fix_snapshot: bool = False,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    method: neighbor => 修正为 n-1 的值；median => 修正为 median(n-1, n, n+1)；neighbor_avg => 修正为 (n-1+n+1)/2。

    repo_root: 解析相对 snapshot 路径时用；默认仓库根（src 的上一级）。
    """
    root = (repo_root or _repo_root()).resolve()
    if not changes_db.is_file():
        return {"error": "changes_db 不存在: {}".format(changes_db), "updated": 0, "fixed_rows": []}

    conn = connect_sqlite(changes_db, row_factory=sqlite3.Row)
    try:
        all_rows = _fetch_all_rows(conn)
    finally:
        conn.close()

    grouped = _group_by_series(all_rows)
    to_update: List[Tuple[str, int, int]] = []  # (table, id, new_value)
    snapshot_updates: List[Tuple[str, str, str, str, int]] = []  # (snapshot_db, artist_mid, song_mid, metric, value)
    remove_from_milestone: List[Tuple[str, int]] = []  # (song_name, wrong_count) 仅 favorite_count_text

    for (artist_mid, song_mid, metric), series in grouped.items():
        fixes = _find_outliers(series, threshold, method, artist_mid, song_mid)
        for t in fixes:
            idx, row_id, table, corrected, snapshot_db, am, sm, m, wrong_new_value = t
            to_update.append((table, row_id, corrected))
            snapshot_updates.append((snapshot_db, am, sm, m, corrected))
            if m == "favorite_count_text":
                song_name = series[idx][8]  # song_name in same row
                remove_from_milestone.append((song_name.strip(), wrong_new_value))

    if dry_run:
        return {
            "updated": 0,
            "would_update": len(to_update),
            "fixed_rows": [
                {"table": t, "id": i, "new_value": v}
                for t, i, v in to_update
            ],
            "snapshot_updates": len(snapshot_updates) if fix_snapshot else 0,
            "remove_from_milestone": remove_from_milestone,
        }

    conn = connect_sqlite(changes_db)
    try:
        for table, row_id, new_value in to_update:
            conn.execute(
                "UPDATE {} SET new_value = ?, delta = 0 WHERE id = ?".format(table),
                (new_value, row_id),
            )
        conn.commit()
    finally:
        conn.close()

    if fix_snapshot and snapshot_updates:
        by_snapshot: Dict[str, List[Tuple[str, str, str, int]]] = {}
        for snapshot_db, artist_mid, song_mid, metric, corrected in snapshot_updates:
            by_snapshot.setdefault(snapshot_db, []).append((artist_mid, song_mid, metric, corrected))
        for snapshot_path, triples in by_snapshot.items():
            p = Path(snapshot_path)
            if not p.is_absolute():
                p = root / p
            if not p.is_file():
                continue
            try:
                snap_conn = connect_sqlite(p)
                for artist_mid, song_mid, metric, corrected in triples:
                    col = "comment_count" if metric == "comment_count" else "favorite_count_text"
                    snap_conn.execute(
                        "UPDATE songs SET {} = ? WHERE song_mid = ? AND artist_mid = ?".format(col),
                        (corrected, song_mid, artist_mid),
                    )
                snap_conn.commit()
                snap_conn.close()
            except Exception:
                pass

    removed_log_lines = 0
    if remove_from_milestone:
        remove_set = set(remove_from_milestone)  # (song_name, count)
        platform = _platform_from_changes_db(changes_db)
        log_path = changes_db.parent / "milestone_{}.log".format(platform)
        if log_path.is_file():
            try:
                kept: List[str] = []
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line_strip = line.strip()
                        if not line_strip:
                            kept.append(line)
                            continue
                        parts = line_strip.split()
                        if len(parts) < 3:
                            kept.append(line)
                            continue
                        try:
                            song_name_line = " ".join(parts[2:-1]) if len(parts) > 3 else parts[2]
                            count_line = int(parts[-1])
                            if (song_name_line.strip(), count_line) in remove_set:
                                removed_log_lines += 1
                                continue
                        except (ValueError, IndexError):
                            pass
                        kept.append(line)
                with open(log_path, "w", encoding="utf-8") as f:
                    f.writelines(kept)
            except OSError:
                pass

    return {
        "updated": len(to_update),
        "fixed_rows": [{"table": t, "id": i, "new_value": v} for t, i, v in to_update],
        "remove_from_milestone": remove_from_milestone,
        "removed_log_lines": removed_log_lines,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="修正收藏/评论异常突变值并更新变化表（三平台）")
    ap.add_argument("--changes-db", default="data/kugou_changes.db", help="变化库路径")
    ap.add_argument("--threshold", type=int, default=100, help="相邻差大于此视为异常；n-1 与 n+1 差小于此视为一致")
    ap.add_argument(
        "--method",
        choices=("neighbor", "median", "neighbor_avg"),
        default="neighbor",
        help="neighbor=改为 n-1；median=三者中位数；neighbor_avg=(n-1+n+1)/2",
    )
    ap.add_argument("--dry-run", action="store_true", help="只检测不写入")
    ap.add_argument("--fix-snapshot", action="store_true", help="同时修正对应快照 DB 中的 songs 表")
    args = ap.parse_args()

    root = _repo_root()
    changes_db = Path(args.changes_db)
    if not changes_db.is_absolute():
        changes_db = root / changes_db

    result = run(
        changes_db=changes_db,
        threshold=args.threshold,
        method=args.method,
        dry_run=args.dry_run,
        fix_snapshot=args.fix_snapshot,
        repo_root=root,
    )

    if result.get("error"):
        print(result["error"], file=sys.stderr)
        return 1

    if args.dry_run:
        print("【dry-run】将修正 {} 条变化表记录".format(result.get("would_update", 0)))
        for r in result.get("fixed_rows", [])[:50]:
            print("  ", r)
        if len(result.get("fixed_rows", [])) > 50:
            print("  ... 共 {} 条".format(len(result["fixed_rows"])))
    else:
        print("已修正 {} 条变化表记录".format(result.get("updated", 0)))
        for r in result.get("fixed_rows", [])[:30]:
            print("  ", r)
        if len(result.get("fixed_rows", [])) > 30:
            print("  ... 共 {} 条".format(len(result["fixed_rows"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
