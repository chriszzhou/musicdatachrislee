from __future__ import annotations

import sqlite3

from .sqlite_util import connect_sqlite
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


def _platform_from_changes_db(changes_db_file: Path) -> str:
    s = changes_db_file.name.lower()
    if "netease" in s:
        return "netease"
    if "kugou" in s:
        return "kugou"
    return "qq"


def _favorite_milestone_should_log(_platform: str, old_v: int, new_v: int, _delta: int) -> bool:
    """
    三平台统一：仅当收藏量跨越「万」档时记录里程碑，即依次检测 10000、20000、30000…
    条件：上一份收藏有效（>0）、当前收藏 >0，且存在整数 k>=1 使得 old_v < 10000*k <= new_v。
    """
    if new_v <= 0 or old_v <= 0:
        return False
    step = 10_000
    t = step
    while t <= new_v:
        if old_v < t <= new_v:
            return True
        t += step
    return False


def _append_favorite_milestone_log(log_path: Path, song_name: str, favorite_count: int) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        name_safe = (song_name or "").strip().replace("\n", " ").replace("\r", " ") or "-"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 记录跨越的最高万档（10000 的整数倍）
        milestone_value = (favorite_count // 10_000) * 10_000
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("{} {} {}\n".format(ts, name_safe, milestone_value))
    except OSError:
        pass


def _parse_count_value(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace("+", "")
    if not text:
        return 0
    multiplier = 1
    if text.endswith(("k",)):
        multiplier = 1_000
        text = text[:-1]
    elif text.endswith(("w", "万")):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith(("y", "亿")):
        multiplier = 100_000_000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


def _read_artist_songs(db_file: Path, artist_mid: str) -> Dict[str, Dict[str, object]]:
    songs: Dict[str, Dict[str, object]] = {}
    conn = connect_sqlite(db_file)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT song_mid, name, favorite_count_text, publish_time
            FROM songs
            WHERE artist_mid = ?
            """,
            (artist_mid,),
        )
        for song_mid, name, favorite_count_text, publish_time in cur.fetchall():
            songs[str(song_mid)] = {
                "name": name or "",
                "favorite_count_text": _parse_count_value(favorite_count_text),
                "publish_time": publish_time or "",
            }
    finally:
        conn.close()
    return songs


def _read_artist_profile(db_file: Path, artist_mid: str) -> Dict[str, object]:
    conn = connect_sqlite(db_file)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT artist_mid, name, fans
            FROM artists
            WHERE artist_mid = ?
            LIMIT 1
            """,
            (artist_mid,),
        )
        row = cur.fetchone()
        if not row:
            return {}
        return {
            "artist_mid": str(row[0] or "").strip(),
            "name": str(row[1] or "").strip(),
            "fans": _parse_count_value(row[2]),
        }
    finally:
        conn.close()


def _month_key(run_at: str) -> str:
    """从 run_at (YYYY-MM-DD HH:MM:SS) 得到月份键 YYYYMM。"""
    if not run_at or len(run_at) < 7:
        return datetime.now().strftime("%Y%m")
    return run_at[:7].replace("-", "")


def _table_name(base: str, month_key: str) -> str:
    """变化表按月分表名，如 metric_changes_202503。"""
    return "{}_m{}".format(base, month_key)


_migrated_is_init_tables: set = set()


def _migrate_add_is_init(conn: sqlite3.Connection, table: str) -> None:
    """为旧表添加 is_init 列（如果不存在），并将 old_value=0 的旧记录标记为 is_init=1。"""
    if table in _migrated_is_init_tables:
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info({})".format(table)).fetchall()}
    if "is_init" in cols:
        _migrated_is_init_tables.add(table)
        return
    conn.execute("ALTER TABLE {} ADD COLUMN is_init INTEGER NOT NULL DEFAULT 0".format(table))
    conn.execute("UPDATE {} SET is_init = 1 WHERE old_value = 0".format(table))
    _migrated_is_init_tables.add(table)


_INIT_THRESHOLD_DAYS = 7


def _is_init_metric(old_v: int, publish_time: str, run_at: str) -> int:
    """判断歌曲指标变化是否为初始化记录。old_value=0 且发布超过7天视为初始化。"""
    if old_v != 0:
        return 0
    if not publish_time:
        return 1
    try:
        pub_date = datetime.strptime(publish_time[:10], "%Y-%m-%d")
        run_date = datetime.strptime(run_at[:10], "%Y-%m-%d")
        if (run_date - pub_date).days > _INIT_THRESHOLD_DAYS:
            return 1
    except (ValueError, IndexError):
        return 1
    return 0


def _ensure_month_table(conn: sqlite3.Connection, base: str, month_key: str) -> None:
    """确保指定月份的基表存在（如 metric_changes_m202503）。"""
    table = _table_name(base, month_key)
    if base == "song_changes":
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS {} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                artist_mid TEXT NOT NULL,
                song_mid TEXT NOT NULL,
                song_name TEXT,
                change_type TEXT NOT NULL,
                snapshot_db TEXT NOT NULL
            )
            """.format(table)
        )
    elif base == "metric_changes":
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS {} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                artist_mid TEXT NOT NULL,
                song_mid TEXT NOT NULL,
                song_name TEXT,
                metric TEXT NOT NULL,
                old_value INTEGER NOT NULL,
                new_value INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                snapshot_db TEXT NOT NULL,
                is_init INTEGER NOT NULL DEFAULT 0
            )
            """.format(table)
        )
        _migrate_add_is_init(conn, table)
    elif base == "artist_metric_changes":
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS {} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                artist_mid TEXT NOT NULL,
                artist_name TEXT,
                metric TEXT NOT NULL,
                old_value INTEGER NOT NULL,
                new_value INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                snapshot_db TEXT NOT NULL,
                is_init INTEGER NOT NULL DEFAULT 0
            )
            """.format(table)
        )
        _migrate_add_is_init(conn, table)
    conn.commit()


def _list_change_month_tables(conn: sqlite3.Connection, base: str) -> List[str]:
    """列出已存在的某类变化表的所有月份键（如 metric_changes_m202503 -> 202503），按升序。"""
    prefix = base + "_m"
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?",
        (prefix + "%",),
    ).fetchall()
    month_keys: List[str] = []
    for (name,) in rows:
        if name.startswith(prefix) and len(name) > len(prefix):
            month_keys.append(name[len(prefix) :])
    month_keys.sort()
    return month_keys


def get_changes_table_for_run_at(base: str, run_at: Optional[str] = None) -> str:
    """返回某 run_at 对应月份的变化表名（如 metric_changes_m202503）；run_at 为空则用当前时间。"""
    month_key = _month_key(run_at) if run_at else datetime.now().strftime("%Y%m")
    return _table_name(base, month_key)


def _has_legacy_metric_changes_table(conn: sqlite3.Connection) -> bool:
    """是否存在旧版单表 metric_changes（未分表）。"""
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metric_changes'"
        ).fetchone()
        is not None
    )


def fetch_metric_changes_all(
    conn: sqlite3.Connection,
    where_sql: str = "1=1",
    params: Tuple[object, ...] = (),
    order_asc: bool = True,
    columns: str = "run_at, song_name, song_mid, old_value, new_value, delta",
) -> List[object]:
    """
    从 metric_changes 或所有月度分表中查询，兼容旧版单表与按月分表。
    返回按 run_at 排序的行列表（Row 或 tuple 取决于 conn.row_factory）。
    """
    if _has_legacy_metric_changes_table(conn):
        sql = "SELECT {} FROM metric_changes WHERE {} ORDER BY run_at {}".format(
            columns, where_sql, "ASC" if order_asc else "DESC"
        )
        return conn.execute(sql, params).fetchall()
    month_keys = _list_change_month_tables(conn, "metric_changes")
    if not month_keys:
        return []
    rows: List[object] = []
    for mk in month_keys:
        table = _table_name("metric_changes", mk)
        sql = "SELECT {} FROM {} WHERE {}".format(columns, table, where_sql)
        rows.extend(conn.execute(sql, params).fetchall())
    if not rows:
        return []
    # 按 run_at 排序（支持 sqlite3.Row 与 tuple）
    def _run_at(r: object) -> str:
        if hasattr(r, "keys") and hasattr(r, "__getitem__"):
            try:
                k = r.keys() if callable(getattr(r, "keys", None)) else []
                if "run_at" in (list(k) if not isinstance(k, list) else k):
                    return str(r["run_at"] or "")
            except Exception:
                pass
        if isinstance(r, (list, tuple)) and len(r) > 0:
            return str(r[0] or "")
        return ""

    rows.sort(key=_run_at, reverse=not order_asc)
    return rows


def _ensure_changes_tables(conn: sqlite3.Connection) -> None:
    """确保至少有一个月份的变化表存在（使用当前月），兼容旧代码调用。"""
    month_key = datetime.now().strftime("%Y%m")
    _ensure_month_table(conn, "metric_changes", month_key)
    _ensure_month_table(conn, "artist_metric_changes", month_key)
    _ensure_month_table(conn, "song_changes", month_key)


def _ensure_legacy_changes_tables(conn: sqlite3.Connection) -> None:
    """仅用于迁移：创建旧版单表（无分表时使用）。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS song_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            artist_mid TEXT NOT NULL,
            song_mid TEXT NOT NULL,
            song_name TEXT,
            change_type TEXT NOT NULL,
            snapshot_db TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metric_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            artist_mid TEXT NOT NULL,
            song_mid TEXT NOT NULL,
            song_name TEXT,
            metric TEXT NOT NULL,
            old_value INTEGER NOT NULL,
            new_value INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            snapshot_db TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artist_metric_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            artist_mid TEXT NOT NULL,
            artist_name TEXT,
            metric TEXT NOT NULL,
            old_value INTEGER NOT NULL,
            new_value INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            snapshot_db TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _insert_song_change_rows(
    conn: sqlite3.Connection,
    run_at: str,
    artist_mid: str,
    snapshot_db: str,
    rows: Iterable[Tuple[str, str, str]],
) -> int:
    if not rows:
        return 0
    month_key = _month_key(run_at)
    _ensure_month_table(conn, "song_changes", month_key)
    table = _table_name("song_changes", month_key)
    payload = [
        (run_at, artist_mid, song_mid, song_name, change_type, snapshot_db)
        for song_mid, song_name, change_type in rows
    ]
    conn.executemany(
        """
        INSERT INTO {} (
            run_at, artist_mid, song_mid, song_name, change_type, snapshot_db
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """.format(table),
        payload,
    )
    conn.commit()
    return len(payload)


def insert_metric_changes_for_song(
    changes_db_file: Path,
    run_at: str,
    artist_mid: str,
    song_mid: str,
    song_name: str,
    snapshot_db: str,
    metrics: Iterable[Tuple[str, int, int]],
    publish_time: str = "",
) -> int:
    """写入单首歌曲的 metric 变化记录（用于新歌页定时更新）。metrics: [(metric_name, old_value, new_value), ...]。"""
    rows = [
        (song_mid, song_name, name, old_v or 0, new_v or 0, (new_v or 0) - (old_v or 0),
         _is_init_metric(old_v or 0, publish_time, run_at))
        for name, old_v, new_v in metrics
    ]
    conn = connect_sqlite(changes_db_file)
    try:
        _ensure_changes_tables(conn)
        return _insert_metric_change_rows(conn, run_at, artist_mid, snapshot_db, rows)
    finally:
        conn.close()


def _insert_metric_change_rows(
    conn: sqlite3.Connection,
    run_at: str,
    artist_mid: str,
    snapshot_db: str,
    rows: Iterable[Tuple[str, str, str, int, int, int, int]],
) -> int:
    payload = [
        (run_at, artist_mid, song_mid, song_name, metric, old_v, new_v, delta, snapshot_db, is_init)
        for song_mid, song_name, metric, old_v, new_v, delta, is_init in rows
    ]
    if not payload:
        return 0
    month_key = _month_key(run_at)
    _ensure_month_table(conn, "metric_changes", month_key)
    table = _table_name("metric_changes", month_key)
    conn.executemany(
        """
        INSERT INTO {} (
            run_at, artist_mid, song_mid, song_name, metric,
            old_value, new_value, delta, snapshot_db, is_init
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """.format(table),
        payload,
    )
    conn.commit()
    return len(payload)


def _insert_artist_metric_change_rows(
    conn: sqlite3.Connection,
    run_at: str,
    artist_mid: str,
    snapshot_db: str,
    rows: Iterable[Tuple[str, str, int, int, int, int]],
) -> int:
    payload = [
        (run_at, artist_mid, artist_name, metric, old_v, new_v, delta, snapshot_db, is_init)
        for artist_name, metric, old_v, new_v, delta, is_init in rows
    ]
    if not payload:
        return 0
    month_key = _month_key(run_at)
    _ensure_month_table(conn, "artist_metric_changes", month_key)
    table = _table_name("artist_metric_changes", month_key)
    conn.executemany(
        """
        INSERT INTO {} (
            run_at, artist_mid, artist_name, metric,
            old_value, new_value, delta, snapshot_db, is_init
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """.format(table),
        payload,
    )
    conn.commit()
    return len(payload)


def _find_previous_snapshot(snapshots_dir: Path, current_snapshot: Path) -> Optional[Path]:
    all_files = sorted(snapshots_dir.glob("*.db"))
    candidates = [p for p in all_files if p.resolve() != current_snapshot.resolve()]
    if not candidates:
        return None
    return candidates[-1]


def track_changes_for_artist(
    snapshots_dir: Path,
    current_snapshot_file: Path,
    changes_db_file: Path,
    artist_mid: str,
) -> Dict[str, int]:
    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current = _read_artist_songs(current_snapshot_file, artist_mid)
    current_artist = _read_artist_profile(current_snapshot_file, artist_mid)
    prev_file = _find_previous_snapshot(snapshots_dir, current_snapshot_file)
    previous = _read_artist_songs(prev_file, artist_mid) if prev_file else {}
    previous_artist = _read_artist_profile(prev_file, artist_mid) if prev_file else {}

    metric_changes = []
    artist_metric_changes = []

    current_keys = set(current.keys())
    previous_keys = set(previous.keys())

    platform = _platform_from_changes_db(changes_db_file)
    milestone_log = changes_db_file.parent / "milestone_{}.log".format(platform)
    milestone_entries: List[Tuple[str, int]] = []

    for song_mid in sorted(current_keys & previous_keys):
        curr_item = current[song_mid]
        prev_item = previous[song_mid]
        for metric in ("favorite_count_text",):
            old_v = int(prev_item.get(metric, 0) or 0)
            new_v = int(curr_item.get(metric, 0) or 0)
            delta = new_v - old_v
            if delta == 0:
                continue
            # 抖动预防：API 临时返回 0 不记录，等下次恢复后自然跳过
            if new_v == 0 and old_v > 0:
                continue
            song_name = str(curr_item.get("name", "") or prev_item.get("name", ""))
            publish_time = str(curr_item.get("publish_time", "") or "")
            is_init = _is_init_metric(old_v, publish_time, run_at)
            metric_changes.append(
                (
                    song_mid,
                    song_name,
                    metric,
                    old_v,
                    new_v,
                    delta,
                    is_init,
                )
            )
            if metric == "favorite_count_text" and _favorite_milestone_should_log(
                platform, old_v, new_v, delta
            ):
                _append_favorite_milestone_log(milestone_log, song_name, new_v)
                milestone_entries.append((song_name, new_v))

    old_fans = int(previous_artist.get("fans", 0) or 0)
    new_fans = int(current_artist.get("fans", 0) or 0)
    fans_delta = new_fans - old_fans
    if fans_delta != 0 and not (new_fans == 0 and old_fans > 0):
        artist_metric_changes.append(
            (
                str(current_artist.get("name", "") or previous_artist.get("name", "")),
                "fans",
                old_fans,
                new_fans,
                fans_delta,
                1 if old_fans == 0 else 0,
            )
        )

    changes_db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(changes_db_file)
    try:
        _ensure_changes_tables(conn)
        metric_changes_count = _insert_metric_change_rows(
            conn,
            run_at=run_at,
            artist_mid=artist_mid,
            snapshot_db=current_snapshot_file.as_posix(),
            rows=metric_changes,
        )
        artist_metric_changes_count = _insert_artist_metric_change_rows(
            conn,
            run_at=run_at,
            artist_mid=artist_mid,
            snapshot_db=current_snapshot_file.as_posix(),
            rows=artist_metric_changes,
        )
    finally:
        conn.close()

    return {
        "song_changes": 0,
        "metric_changes": metric_changes_count,
        "artist_metric_changes": artist_metric_changes_count,
        "milestones": milestone_entries,
    }


def _report_month_keys(
    conn: sqlite3.Connection,
    base: str,
    year_str: Optional[str],
    month_str: Optional[str],
    date_str: Optional[str],
) -> List[str]:
    """确定 report 要查询的月份键列表（分表用）。"""
    if year_str:
        all_months = _list_change_month_tables(conn, base)
        return [m for m in all_months if m.startswith(year_str)]
    if month_str:
        return [month_str.replace("-", "")]
    if date_str:
        return [date_str[:7].replace("-", "")]
    return [datetime.now().strftime("%Y%m")]


def report_changes(
    changes_db_file: Path,
    date_str: Optional[str] = None,
    month_str: Optional[str] = None,
    year_str: Optional[str] = None,
    artist_mid: Optional[str] = None,
    limit: int = 200,
) -> Dict[str, List[Dict[str, object]]]:
    if limit <= 0:
        limit = 200

    changes_db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(changes_db_file, row_factory=sqlite3.Row)
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

        if year_str:
            where_sql = "substr(run_at, 1, 4) = ?"
            base_params: List[object] = [year_str]
        elif month_str:
            where_sql = "substr(run_at, 1, 7) = ?"
            base_params = [month_str]
        else:
            where_sql = "date(run_at) = ?"
            base_params = [date_str or datetime.now().strftime("%Y-%m-%d")]

        def _run_metric_report() -> List[object]:
            if len(month_keys_metric) == 1:
                table = _table_name("metric_changes", month_keys_metric[0])
                sql = """
                    SELECT run_at, artist_mid, song_mid, song_name, metric,
                           old_value, new_value, delta, snapshot_db
                    FROM {}
                    WHERE {}
                """.format(table, where_sql)
                params: List[object] = list(base_params)
                if artist_mid:
                    sql += " AND artist_mid = ?"
                    params.append(artist_mid)
                sql += " ORDER BY id DESC LIMIT ?"
                params.append(limit)
                return conn.execute(sql, params).fetchall()
            parts = []
            for mk in month_keys_metric:
                table = _table_name("metric_changes", mk)
                part = """
                    SELECT run_at, artist_mid, song_mid, song_name, metric,
                           old_value, new_value, delta, snapshot_db FROM {}
                """.format(table)
                if artist_mid:
                    part += " WHERE artist_mid = ?"
                parts.append(part)
            union_sql = " UNION ALL ".join(parts)
            sql = "SELECT * FROM ({}) ORDER BY run_at DESC LIMIT ?".format(union_sql)
            params = ([artist_mid] * len(month_keys_metric) if artist_mid else []) + [limit]
            return conn.execute(sql, params).fetchall()

        def _run_artist_report() -> List[object]:
            if len(month_keys_artist) == 1:
                table = _table_name("artist_metric_changes", month_keys_artist[0])
                sql = """
                    SELECT run_at, artist_mid, artist_name, metric,
                           old_value, new_value, delta, snapshot_db
                    FROM {}
                    WHERE {}
                """.format(table, where_sql)
                params = list(base_params)
                if artist_mid:
                    sql += " AND artist_mid = ?"
                    params.append(artist_mid)
                sql += " ORDER BY id DESC LIMIT ?"
                params.append(limit)
                return conn.execute(sql, params).fetchall()
            parts = []
            for mk in month_keys_artist:
                table = _table_name("artist_metric_changes", mk)
                part = """
                    SELECT run_at, artist_mid, artist_name, metric,
                           old_value, new_value, delta, snapshot_db FROM {}
                """.format(table)
                if artist_mid:
                    part += " WHERE artist_mid = ?"
                parts.append(part)
            union_sql = " UNION ALL ".join(parts)
            sql = "SELECT * FROM ({}) ORDER BY run_at DESC LIMIT ?".format(union_sql)
            params = ([artist_mid] * len(month_keys_artist) if artist_mid else []) + [limit]
            return conn.execute(sql, params).fetchall()

        metric_rows = _run_metric_report()
        artist_metric_rows = _run_artist_report()
    finally:
        conn.close()

    return {
        "song_changes": [],
        "metric_changes": [dict(r) for r in metric_rows],
        "artist_metric_changes": [dict(r) for r in artist_metric_rows],
    }


_COMPACT_KEEP_DAYS = 7


def compact_old_changes(changes_db_file: Path, keep_days: int = _COMPACT_KEEP_DAYS) -> Dict[str, int]:
    """清理变化表：1) 删除数据抖动（同一天净变化为0的记录）；
    2) 超过 keep_days 天的记录每天只保留一条合并记录；
    3) 重建里程碑日志。
    返回 {表名: 删除行数}。
    """
    from datetime import timedelta

    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    changes_db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(changes_db_file)
    result: Dict[str, int] = {}
    try:
        _ensure_changes_tables(conn)

        for base in ("metric_changes", "artist_metric_changes"):
            month_keys = _list_change_month_tables(conn, base)
            for mk in month_keys:
                table = _table_name(base, mk)
                _ensure_month_table(conn, base, mk)
                jitter_del = _delete_jitter(conn, table, base)
                deleted = _compact_table(conn, table, base, cutoff)
                total = jitter_del + deleted
                if total > 0:
                    result[table] = total
    finally:
        conn.close()

    # 重建里程碑日志
    platform = _platform_from_changes_db(changes_db_file)
    log_path = changes_db_file.parent / "milestone_{}.log".format(platform)
    rebuild_milestone_log(changes_db_file, log_path)

    return result


def _delete_jitter(conn: sqlite3.Connection, table: str, base: str) -> int:
    """删除数据抖动：同一天同一对象同一指标的净变化为0（SUM(delta)=0）且记录数>1的全部删除。"""
    if base == "metric_changes":
        group_cols = "date(run_at), artist_mid, song_mid, metric"
    else:
        group_cols = "date(run_at), artist_mid, metric"

    cur = conn.execute(
        """
        DELETE FROM {table} WHERE ({group}) IN (
            SELECT {group} FROM {table}
            GROUP BY {group}
            HAVING SUM(delta) = 0 AND COUNT(*) > 1
        )
        """.format(table=table, group=group_cols)
    )
    deleted = cur.rowcount
    if deleted > 0:
        conn.commit()
    return deleted


def _compact_table(
    conn: sqlite3.Connection, table: str, base: str, cutoff: str,
) -> int:
    """对单张表执行合并：date(run_at) < cutoff 的记录按天+分组键合并。"""

    if base == "metric_changes":
        group_cols = "date(run_at), artist_mid, song_mid, metric, is_init"
        insert_cols = "run_at, artist_mid, song_mid, song_name, metric, old_value, new_value, delta, snapshot_db, is_init"
        select_merged = """
            SELECT
                MAX(run_at),
                artist_mid,
                song_mid,
                SUBSTR(
                    MAX(run_at || '|' || COALESCE(song_name, '')),
                    INSTR(MAX(run_at || '|' || COALESCE(song_name, '')), '|') + 1
                ),
                metric,
                CAST(SUBSTR(
                    MIN(run_at || '|' || CAST(old_value AS TEXT)),
                    INSTR(MIN(run_at || '|' || CAST(old_value AS TEXT)), '|') + 1
                ) AS INTEGER),
                CAST(SUBSTR(
                    MAX(run_at || '|' || CAST(new_value AS TEXT)),
                    INSTR(MAX(run_at || '|' || CAST(new_value AS TEXT)), '|') + 1
                ) AS INTEGER),
                SUM(delta),
                SUBSTR(
                    MAX(run_at || '|' || snapshot_db),
                    INSTR(MAX(run_at || '|' || snapshot_db), '|') + 1
                ),
                is_init
            FROM {table}
            WHERE date(run_at) < ?
            GROUP BY {group}
            HAVING COUNT(*) > 1
        """.format(table=table, group=group_cols)
    else:
        group_cols = "date(run_at), artist_mid, metric, is_init"
        insert_cols = "run_at, artist_mid, artist_name, metric, old_value, new_value, delta, snapshot_db, is_init"
        select_merged = """
            SELECT
                MAX(run_at),
                artist_mid,
                SUBSTR(
                    MAX(run_at || '|' || COALESCE(artist_name, '')),
                    INSTR(MAX(run_at || '|' || COALESCE(artist_name, '')), '|') + 1
                ),
                metric,
                CAST(SUBSTR(
                    MIN(run_at || '|' || CAST(old_value AS TEXT)),
                    INSTR(MIN(run_at || '|' || CAST(old_value AS TEXT)), '|') + 1
                ) AS INTEGER),
                CAST(SUBSTR(
                    MAX(run_at || '|' || CAST(new_value AS TEXT)),
                    INSTR(MAX(run_at || '|' || CAST(new_value AS TEXT)), '|') + 1
                ) AS INTEGER),
                SUM(delta),
                SUBSTR(
                    MAX(run_at || '|' || snapshot_db),
                    INSTR(MAX(run_at || '|' || snapshot_db), '|') + 1
                ),
                is_init
            FROM {table}
            WHERE date(run_at) < ?
            GROUP BY {group}
            HAVING COUNT(*) > 1
        """.format(table=table, group=group_cols)

    merged_rows = conn.execute(select_merged, [cutoff]).fetchall()
    if not merged_rows:
        return 0

    # 删除旧记录（有多条的那些天）
    if base == "metric_changes":
        delete_sql = """
            DELETE FROM {table} WHERE date(run_at) < ?
            AND ({group}) IN (
                SELECT {group} FROM {table}
                WHERE date(run_at) < ?
                GROUP BY {group}
                HAVING COUNT(*) > 1
            )
        """.format(table=table, group=group_cols)
    else:
        delete_sql = """
            DELETE FROM {table} WHERE date(run_at) < ?
            AND ({group}) IN (
                SELECT {group} FROM {table}
                WHERE date(run_at) < ?
                GROUP BY {group}
                HAVING COUNT(*) > 1
            )
        """.format(table=table, group=group_cols)

    cur = conn.execute(delete_sql, [cutoff, cutoff])
    deleted_count = cur.rowcount

    # 插入合并后的记录
    placeholders = ", ".join(["?"] * len(merged_rows[0]))
    conn.executemany(
        "INSERT INTO {table} ({cols}) VALUES ({ph})".format(
            table=table, cols=insert_cols, ph=placeholders
        ),
        merged_rows,
    )
    conn.commit()

    return deleted_count - len(merged_rows)


def rebuild_milestone_log(changes_db_file: Path, log_path: Path) -> int:
    """从变化表重建里程碑日志。遍历每首歌的变化记录（排除 is_init），
    追踪收藏量，检测何时真正跨越万档。返回写入的里程碑条数。"""
    platform = _platform_from_changes_db(changes_db_file)
    conn = connect_sqlite(changes_db_file)
    try:
        _ensure_changes_tables(conn)
        month_keys = _list_change_month_tables(conn, "metric_changes")
        if not month_keys:
            return 0

        if len(month_keys) == 1:
            from_clause = _table_name("metric_changes", month_keys[0])
        else:
            parts = ["SELECT * FROM {}".format(_table_name("metric_changes", mk)) for mk in month_keys]
            from_clause = "({})".format(" UNION ALL ".join(parts))

        # 按 song_mid 分组，按时间排序，取所有非 init 的 favorite 变化
        rows = conn.execute(
            """
            SELECT run_at, song_mid, song_name, old_value, new_value
            FROM {}
            WHERE metric = 'favorite_count_text' AND is_init = 0
            ORDER BY song_mid, run_at
            """.format(from_clause)
        ).fetchall()
    finally:
        conn.close()

    # 按 song_mid 分组追踪
    # key: (song_mid, 万档) -> (run_at, song_name, new_value)
    # 同一首歌同一万档多次跨越时只保留最后一次
    milestone_map: Dict[tuple, tuple] = {}
    current_mid = None
    last_value = 0
    step = 10_000

    for run_at, song_mid, song_name, old_value, new_value in rows:
        mid = str(song_mid or "").strip()
        name = str(song_name or "").strip()
        if mid != current_mid:
            current_mid = mid
            last_value = int(old_value or 0)

        nv = int(new_value or 0)
        if _favorite_milestone_should_log(platform, last_value, nv, nv - last_value):
            # 找出本次跨越的最高万档
            t = step
            highest_t = 0
            while t <= nv:
                if last_value < t <= nv:
                    highest_t = t
                t += step
            if highest_t > 0:
                milestone_map[(mid, highest_t)] = (str(run_at), name, highest_t)
        last_value = nv

    entries = list(milestone_map.values())
    entries.sort(key=lambda x: x[0])

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        for ts, name, fav in entries:
            f.write("{} {} {}\n".format(ts, name, fav))

    return len(entries)

