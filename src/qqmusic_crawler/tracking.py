from __future__ import annotations

import sqlite3
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


def _favorite_milestone_should_log(platform: str, old_v: int, new_v: int, delta: int) -> bool:
    if new_v <= 0:
        return False
    if delta > 1000:
        return True
    if platform == "qq":
        thresholds = [5000, 10000, 50000] + [50000 * k for k in range(2, max(2, new_v // 50000 + 2))]
    else:
        thresholds = [1000, 5000, 10000] + [10000 * k for k in range(2, max(2, new_v // 10000 + 2))]
    for t in thresholds:
        if t > new_v:
            break
        if old_v < t <= new_v:
            return True
    return False


def _append_favorite_milestone_log(log_path: Path, song_name: str, favorite_count: int) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        name_safe = (song_name or "").strip().replace("\n", " ").replace("\r", " ") or "-"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("{} {} {}\n".format(ts, name_safe, favorite_count))
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
    conn = sqlite3.connect(str(db_file))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT song_mid, name, comment_count, favorite_count_text
            FROM songs
            WHERE artist_mid = ?
            """,
            (artist_mid,),
        )
        for song_mid, name, comment_count, favorite_count_text in cur.fetchall():
            songs[str(song_mid)] = {
                "name": name or "",
                "comment_count": _parse_count_value(comment_count),
                "favorite_count_text": _parse_count_value(favorite_count_text),
            }
    finally:
        conn.close()
    return songs


def _read_artist_profile(db_file: Path, artist_mid: str) -> Dict[str, object]:
    conn = sqlite3.connect(str(db_file))
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


def _ensure_changes_tables(conn: sqlite3.Connection) -> None:
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
    payload = [
        (run_at, artist_mid, song_mid, song_name, change_type, snapshot_db)
        for song_mid, song_name, change_type in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO song_changes (
            run_at, artist_mid, song_mid, song_name, change_type, snapshot_db
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    conn.commit()
    return len(payload)


def _insert_metric_change_rows(
    conn: sqlite3.Connection,
    run_at: str,
    artist_mid: str,
    snapshot_db: str,
    rows: Iterable[Tuple[str, str, str, int, int, int]],
) -> int:
    payload = [
        (run_at, artist_mid, song_mid, song_name, metric, old_v, new_v, delta, snapshot_db)
        for song_mid, song_name, metric, old_v, new_v, delta in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO metric_changes (
            run_at, artist_mid, song_mid, song_name, metric,
            old_value, new_value, delta, snapshot_db
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    conn.commit()
    return len(payload)


def _insert_artist_metric_change_rows(
    conn: sqlite3.Connection,
    run_at: str,
    artist_mid: str,
    snapshot_db: str,
    rows: Iterable[Tuple[str, str, int, int, int]],
) -> int:
    payload = [
        (run_at, artist_mid, artist_name, metric, old_v, new_v, delta, snapshot_db)
        for artist_name, metric, old_v, new_v, delta in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO artist_metric_changes (
            run_at, artist_mid, artist_name, metric,
            old_value, new_value, delta, snapshot_db
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
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

    for song_mid in sorted(current_keys & previous_keys):
        curr_item = current[song_mid]
        prev_item = previous[song_mid]
        for metric in ("comment_count", "favorite_count_text"):
            old_v = int(prev_item.get(metric, 0) or 0)
            new_v = int(curr_item.get(metric, 0) or 0)
            delta = new_v - old_v
            if delta != 0:
                song_name = str(curr_item.get("name", "") or prev_item.get("name", ""))
                metric_changes.append(
                    (
                        song_mid,
                        song_name,
                        metric,
                        old_v,
                        new_v,
                        delta,
                    )
                )
                if metric == "favorite_count_text" and _favorite_milestone_should_log(
                    platform, old_v, new_v, delta
                ):
                    _append_favorite_milestone_log(milestone_log, song_name, new_v)

    old_fans = int(previous_artist.get("fans", 0) or 0)
    new_fans = int(current_artist.get("fans", 0) or 0)
    fans_delta = new_fans - old_fans
    if fans_delta != 0:
        artist_metric_changes.append(
            (
                str(current_artist.get("name", "") or previous_artist.get("name", "")),
                "fans",
                old_fans,
                new_fans,
                fans_delta,
            )
        )

    changes_db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(changes_db_file))
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
    }


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
    conn = sqlite3.connect(str(changes_db_file))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_changes_tables(conn)
        if year_str:
            where_sql = "substr(run_at, 1, 4) = ?"
            base_params: List[object] = [year_str]
        elif month_str:
            where_sql = "substr(run_at, 1, 7) = ?"
            base_params = [month_str]
        else:
            where_sql = "date(run_at) = ?"
            base_params = [date_str or datetime.now().strftime("%Y-%m-%d")]

        metric_sql = """
            SELECT run_at, artist_mid, song_mid, song_name, metric,
                   old_value, new_value, delta, snapshot_db
            FROM metric_changes
            WHERE
        """
        metric_sql += where_sql
        params: List[object] = list(base_params)
        if artist_mid:
            metric_sql += " AND artist_mid = ?"
            params.append(artist_mid)
        metric_sql += " ORDER BY id DESC LIMIT ?"
        metric_rows = conn.execute(metric_sql, params + [limit]).fetchall()

        artist_metric_sql = """
            SELECT run_at, artist_mid, artist_name, metric,
                   old_value, new_value, delta, snapshot_db
            FROM artist_metric_changes
            WHERE
        """
        artist_metric_sql += where_sql
        artist_metric_params: List[object] = list(base_params)
        if artist_mid:
            artist_metric_sql += " AND artist_mid = ?"
            artist_metric_params.append(artist_mid)
        artist_metric_sql += " ORDER BY id DESC LIMIT ?"
        artist_metric_rows = conn.execute(
            artist_metric_sql, artist_metric_params + [limit]
        ).fetchall()
    finally:
        conn.close()

    return {
        "song_changes": [],
        "metric_changes": [dict(r) for r in metric_rows],
        "artist_metric_changes": [dict(r) for r in artist_metric_rows],
    }

