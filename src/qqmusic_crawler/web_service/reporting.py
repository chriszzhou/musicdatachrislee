from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..sqlite_util import connect_sqlite
from ..tracking import (
    _ensure_changes_tables,
    _list_change_month_tables,
    _report_month_keys,
    _table_name,
    report_changes,
)

from .constants import NEW_SONG_NAME
from .paths import _resolve_changes_db_path

def get_report(
    platform: str,
    mode: str,
    value: str,
    artist_mid: str = "",
    song_display_limit: int = 15,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    mode_clean = (mode or "").strip()
    value_clean = (value or "").strip()
    if mode_clean not in ("year", "month", "day"):
        return {"ok": False, "error": "报告粒度必须是 year/month/day。"}
    if not value_clean:
        if mode_clean == "year":
            value_clean = str(datetime.now().year)
        elif mode_clean == "month":
            value_clean = datetime.now().strftime("%Y-%m")
        elif mode_clean == "day":
            value_clean = datetime.now().strftime("%Y-%m-%d")
    if not value_clean:
        return {"ok": False, "error": "请输入报告日期。"}

    date_str = None
    month_str = None
    year_str = None
    label = ""
    if mode_clean == "year":
        try:
            year_str = "{:04d}".format(int(value_clean))
        except ValueError:
            return {"ok": False, "error": "年份输入不合法。"}
        label = "年份"
    elif mode_clean == "month":
        try:
            month_str = datetime.strptime(value_clean, "%Y-%m").strftime("%Y-%m")
        except ValueError:
            return {"ok": False, "error": "月份输入不合法。"}
        label = "月份"
    else:
        try:
            date_str = datetime.strptime(value_clean, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": "日期输入不合法。"}
        label = "日期"

    changes_db_path = _resolve_changes_db_path(platform, base_dir)
    report = report_changes(
        changes_db_file=changes_db_path,
        date_str=date_str,
        month_str=month_str,
        year_str=year_str,
        artist_mid=(artist_mid or "").strip() or None,
        limit=100000,
    )
    metric_rows = report.get("metric_changes", [])
    artist_metric_rows = report.get("artist_metric_changes", [])

    comment_delta = 0
    favorite_delta = 0
    affected_song_mids = set()
    song_deltas: Dict[str, Dict[str, Any]] = {}
    for row in metric_rows:
        metric = str(row.get("metric") or "")
        delta = int(row.get("delta") or 0)
        run_at = str(row.get("run_at") or "")
        new_value = row.get("new_value")
        old_value = int(row.get("old_value") or 0)
        if old_value == 0:
            continue
        if delta <= 0:
            # 仅展示增长数据，过滤减少与无变化。
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
        if song_name and song_name != song_mid:
            song_deltas[song_mid]["song_name"] = song_name
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
    artist_deltas: Dict[str, Dict[str, Any]] = {}
    for row in artist_metric_rows:
        if str(row.get("metric") or "") != "fans":
            continue
        old_value = int(row.get("old_value") or 0)
        if old_value == 0:
            continue
        delta = int(row.get("delta") or 0)
        if delta <= 0:
            # 仅展示增长数据，过滤减少与无变化。
            continue
        run_at = str(row.get("run_at") or "")
        new_value = row.get("new_value")
        fans_delta += delta
        row_artist_mid = str(row.get("artist_mid") or "").strip()
        if row_artist_mid:
            affected_artist_mids.add(row_artist_mid)
        artist_name = str(row.get("artist_name") or row.get("artist_mid") or "").strip()
        if artist_name:
            if artist_name not in artist_deltas:
                artist_deltas[artist_name] = {"delta": 0, "new": None, "run_at": ""}
            artist_deltas[artist_name]["delta"] += delta
            if run_at >= str(artist_deltas[artist_name]["run_at"]):
                artist_deltas[artist_name]["run_at"] = run_at
                artist_deltas[artist_name]["new"] = new_value

    comment_items: List[Tuple[int, bool, str]] = []
    favorite_items: List[Tuple[int, bool, str]] = []
    comment_chart_rows: List[Dict[str, Any]] = []
    favorite_chart_rows: List[Dict[str, Any]] = []
    for song_mid, values in song_deltas.items():
        comment_new = values.get("comment_new")
        favorite_new = values.get("favorite_new")
        comment_delta_value = int(values.get("comment") or 0)
        favorite_delta_value = int(values.get("favorite") or 0)
        if comment_delta_value != 0:
            comment_items.append(
                (
                    abs(comment_delta_value),
                    comment_delta_value < 0,
                    "{}(评论{:+d}->{}) [{}]".format(
                        str(values.get("song_name") or song_mid),
                        comment_delta_value,
                        comment_new if comment_new is not None else "-",
                        song_mid,
                    ),
                )
            )
            comment_chart_rows.append(
                {
                    "name": str(values.get("song_name") or song_mid),
                    "song_mid": song_mid,
                    "delta": comment_delta_value,
                    "delta_abs": abs(comment_delta_value),
                    "new_value": comment_new,
                }
            )
        if favorite_delta_value != 0:
            favorite_items.append(
                (
                    abs(favorite_delta_value),
                    favorite_delta_value < 0,
                    "{}(收藏{:+d}->{}) [{}]".format(
                        str(values.get("song_name") or song_mid),
                        favorite_delta_value,
                        favorite_new if favorite_new is not None else "-",
                        song_mid,
                    ),
                )
            )
            favorite_chart_rows.append(
                {
                    "name": str(values.get("song_name") or song_mid),
                    "song_mid": song_mid,
                    "delta": favorite_delta_value,
                    "delta_abs": abs(favorite_delta_value),
                    "new_value": favorite_new,
                }
            )
    comment_items.sort(key=lambda x: (-x[0], x[1]))
    favorite_items.sort(key=lambda x: (-x[0], x[1]))
    comment_chart_rows.sort(key=lambda x: (-int(x.get("delta_abs") or 0), int(x.get("delta") or 0) < 0))
    favorite_chart_rows.sort(key=lambda x: (-int(x.get("delta_abs") or 0), int(x.get("delta") or 0) < 0))

    artist_items: List[Tuple[int, bool, str]] = []
    artist_chart_rows: List[Dict[str, Any]] = []
    for artist_name, values in artist_deltas.items():
        delta_value = int(values.get("delta") or 0)
        artist_items.append(
            (
                abs(delta_value),
                delta_value < 0,
                "{}(粉丝{:+d}->{})".format(
                    artist_name,
                    delta_value,
                    values.get("new") if values.get("new") is not None else "-",
                ),
            )
        )
        artist_chart_rows.append(
            {
                "name": artist_name,
                "delta": delta_value,
                "delta_abs": abs(delta_value),
                "new_value": values.get("new"),
            }
        )
    artist_items.sort(key=lambda x: (-x[0], x[1]))
    artist_chart_rows.sort(key=lambda x: (-int(x.get("delta_abs") or 0), int(x.get("delta") or 0) < 0))

    return {
        "ok": True,
        "label": label,
        "value": value_clean,
        "mode": mode_clean,
        "song_summary": {
            "affected_songs": len(affected_song_mids),
            "comment_delta": comment_delta,
            "favorite_delta": favorite_delta,
        },
        "artist_summary": {
            "affected_artists": len(affected_artist_mids),
            "fans_delta": fans_delta,
        },
        "song_names_comment": [x[2] for x in comment_items[:song_display_limit]],
        "song_names_favorite": [x[2] for x in favorite_items[:song_display_limit]],
        "artist_names": [x[2] for x in artist_items],
        "comment_chart_rows": comment_chart_rows[:song_display_limit],
        "favorite_chart_rows": favorite_chart_rows[:song_display_limit],
        "artist_chart_rows": artist_chart_rows[:song_display_limit],
    }



def get_report_chart_data(
    platform: str,
    mode: str,
    value: str,
    artist_mid: str = "",
    base_dir: Optional[Path] = None,
    song_name: str = "",
    use_absolute_favorite: bool = False,
) -> Dict[str, Any]:
    """
    获取变化折线图数据：年按月份聚合、月按日聚合、日按当天每次 run_at 聚合。
    返回 labels 与 series（comment / favorite / fans）。
    use_absolute_favorite=True 时 series.favorite 为各时段「最新收藏数」new_value，否则为增量 SUM(delta)。
    若提供 song_name 则仅统计该歌曲的 metric 变化（用于新歌页单曲曲线）。
    """
    mode_clean = (mode or "").strip()
    value_clean = (value or "").strip()
    if mode_clean not in ("year", "month", "day"):
        return {"ok": False, "error": "报告粒度必须是 year/month/day。"}
    if not value_clean:
        if mode_clean == "year":
            value_clean = str(datetime.now().year)
        elif mode_clean == "month":
            value_clean = datetime.now().strftime("%Y-%m")
        elif mode_clean == "day":
            value_clean = datetime.now().strftime("%Y-%m-%d")
    if not value_clean:
        return {"ok": False, "error": "请输入报告日期。"}

    date_str = None
    month_str = None
    year_str = None
    if mode_clean == "year":
        try:
            year_str = "{:04d}".format(int(value_clean))
        except ValueError:
            return {"ok": False, "error": "年份输入不合法。"}
    elif mode_clean == "month":
        try:
            month_str = datetime.strptime(value_clean, "%Y-%m").strftime("%Y-%m")
        except ValueError:
            return {"ok": False, "error": "月份输入不合法。"}
    else:
        try:
            date_str = datetime.strptime(value_clean, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": "日期输入不合法。"}

    db_path = _resolve_changes_db_path(platform, base_dir)
    if not db_path.is_file():
        empty = {"labels": [], "datasets": []}
        return {"ok": True, "labels": [], "series": {"comment": [], "favorite": [], "fans": []}, "song_comment": empty, "song_favorite": empty, "song_fans": empty}

    conn = connect_sqlite(db_path, row_factory=sqlite3.Row)
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

        def _from_clause(base: str, keys: List[str]) -> str:
            if len(keys) == 1:
                return _table_name(base, keys[0])
            parts = [
                "SELECT * FROM {}".format(_table_name(base, mk)) for mk in keys
            ]
            return "({}) AS {}".format(" UNION ALL ".join(parts), base)

        metric_from = _from_clause("metric_changes", month_keys_metric)
        artist_from = _from_clause("artist_metric_changes", month_keys_artist)

        if year_str:
            where_sql = "substr(run_at, 1, 4) = ?"
            group_sql = "substr(run_at, 1, 7)"
            base_params: List[object] = [year_str]
        elif month_str:
            where_sql = "substr(run_at, 1, 7) = ?"
            group_sql = "date(run_at)"
            base_params = [month_str]
        else:
            where_sql = "date(run_at) = ?"
            group_sql = "run_at"
            base_params = [date_str or datetime.now().strftime("%Y-%m-%d")]

        params = list(base_params)
        if (artist_mid or "").strip():
            params.append((artist_mid or "").strip())
        if (song_name or "").strip():
            params.append((song_name or "").strip())

        artist_filter = " AND artist_mid = ?" if (artist_mid or "").strip() else ""
        song_filter = " AND song_name = ?" if (song_name or "").strip() else ""

        # 首页按日折线图：不传 song_name 时，只取「至少有一条非春雨里」的 run_at，避免新歌页 1 分钟任务产生的纯春雨里 run_at（多为 0）拉满横轴
        if mode_clean == "day" and not (song_name or "").strip():
            run_at_with_other_songs_sql = (
                "SELECT DISTINCT run_at FROM {} WHERE {} {} AND song_name != ? ORDER BY run_at".format(
                    metric_from, where_sql, artist_filter
                )
            )
            params_main = list(base_params) + ([(artist_mid or "").strip()] if (artist_mid or "").strip() else []) + [NEW_SONG_NAME]
            labels_rows = conn.execute(run_at_with_other_songs_sql, params_main).fetchall()
            labels = [str(r[0]) for r in labels_rows]
        else:
            labels_sql = (
                "SELECT {} AS period FROM {} WHERE {} {} {} GROUP BY period ORDER BY period".format(
                    group_sql, metric_from, where_sql, artist_filter, song_filter
                )
            )
            labels_rows = conn.execute(labels_sql, params).fetchall()
            labels = [str(r[0]) for r in labels_rows]

        if not labels:
            empty = {"labels": [], "datasets": []}
            return {"ok": True, "labels": [], "series": {"comment": [], "favorite": [], "fans": []}, "song_comment": empty, "song_favorite": empty, "song_fans": empty}

        series_comment: List[int] = []
        series_favorite: List[int] = []
        series_fans: List[int] = []

        for period in labels:
            if mode_clean == "day":
                period_where = "run_at = ?"
                period_params = [period]
            else:
                period_where = "substr(run_at, 1, {}) = ?".format(len(period))
                period_params = [period]
            if (artist_mid or "").strip():
                period_params = period_params + [(artist_mid or "").strip()]
            if (song_name or "").strip():
                period_params = period_params + [(song_name or "").strip()]

            comment_row = conn.execute(
                """
                SELECT COALESCE(SUM(delta), 0) AS s
                FROM {}
                WHERE {} {} {} AND metric = 'comment_count'
                """.format(metric_from, period_where, artist_filter, song_filter),
                period_params,
            ).fetchone()
            series_comment.append(int(comment_row[0] or 0))

            if use_absolute_favorite:
                fav_row = conn.execute(
                    """
                    SELECT new_value FROM {}
                    WHERE {} {} {} AND metric = 'favorite_count_text'
                    ORDER BY run_at DESC LIMIT 1
                    """.format(metric_from, period_where, artist_filter, song_filter),
                    period_params,
                ).fetchone()
                series_favorite.append(int(fav_row[0] or 0) if fav_row else 0)
            else:
                fav_row = conn.execute(
                    """
                    SELECT COALESCE(SUM(delta), 0) AS s
                    FROM {}
                    WHERE {} {} {} AND metric = 'favorite_count_text'
                    """.format(metric_from, period_where, artist_filter, song_filter),
                    period_params,
                ).fetchone()
                series_favorite.append(int(fav_row[0] or 0))

            period_params_artist = [period] + ([(artist_mid or "").strip()] if (artist_mid or "").strip() else [])
            fans_row = conn.execute(
                """
                SELECT COALESCE(SUM(delta), 0) AS s
                FROM {}
                WHERE {} {} AND metric = 'fans'
                """.format(artist_from, period_where, artist_filter),
                period_params_artist,
            ).fetchone()
            series_fans.append(int(fans_row[0] or 0))

        # 按歌曲维度的评论/收藏：每期按 song_mid 聚合，取总变化量最大的 top_songs 首
        top_songs = 10
        comment_by_song: Dict[str, List[int]] = {}  # song_mid -> [delta per period]
        favorite_by_song: Dict[str, List[int]] = {}
        song_names: Dict[str, str] = {}

        for period_idx, period in enumerate(labels):
            if mode_clean == "day":
                period_where = "run_at = ?"
                period_params = [period]
            else:
                period_where = "substr(run_at, 1, {}) = ?".format(len(period))
                period_params = [period]
            if (artist_mid or "").strip():
                period_params = period_params + [(artist_mid or "").strip()]
            if (song_name or "").strip():
                period_params = period_params + [(song_name or "").strip()]

            for row in conn.execute(
                """
                SELECT song_mid, song_name, COALESCE(SUM(delta), 0) AS s
                FROM {}
                WHERE {} {} {} AND metric = 'comment_count'
                GROUP BY song_mid
                """.format(metric_from, period_where, artist_filter, song_filter),
                period_params,
            ).fetchall():
                mid = str(row[0] or "").strip()
                if not mid:
                    continue
                name = str(row[1] or mid).strip() or mid
                delta = int(row[2] or 0)
                if mid not in song_names:
                    song_names[mid] = name
                if mid not in comment_by_song:
                    comment_by_song[mid] = [0] * len(labels)
                comment_by_song[mid][period_idx] = delta

            for row in conn.execute(
                """
                SELECT song_mid, song_name, COALESCE(SUM(delta), 0) AS s
                FROM {}
                WHERE {} {} {} AND metric = 'favorite_count_text'
                GROUP BY song_mid
                """.format(metric_from, period_where, artist_filter, song_filter),
                period_params,
            ).fetchall():
                mid = str(row[0] or "").strip()
                if not mid:
                    continue
                name = str(row[1] or mid).strip() or mid
                delta = int(row[2] or 0)
                if mid not in song_names:
                    song_names[mid] = name
                if mid not in favorite_by_song:
                    favorite_by_song[mid] = [0] * len(labels)
                favorite_by_song[mid][period_idx] = delta

        def _top_datasets(
            by_song: Dict[str, List[int]],
            names: Dict[str, str],
            limit: int,
        ) -> List[Dict[str, Any]]:
            total_abs = [(mid, sum(abs(v) for v in vals)) for mid, vals in by_song.items()]
            total_abs.sort(key=lambda x: -x[1])
            datasets = []
            for mid, _ in total_abs[:limit]:
                name = names.get(mid, mid)
                if len(name) > 20:
                    name = name[:17] + "..."
                datasets.append({"name": name, "data": by_song[mid]})
            return datasets

        song_comment_datasets = _top_datasets(comment_by_song, song_names, top_songs)
        song_favorite_datasets = _top_datasets(favorite_by_song, song_names, top_songs)

        # 首页「按日」折线图：同一天可能因新歌页 1 分钟任务产生大量 run_at，限制最多点数避免图过密（与新歌页 mode=range 无关）
        MAX_DAY_CHART_POINTS = 24
        if mode_clean == "day" and len(labels) > MAX_DAY_CHART_POINTS:
            n = len(labels)
            indices = [0]
            for i in range(1, MAX_DAY_CHART_POINTS - 1):
                indices.append(i * (n - 1) // (MAX_DAY_CHART_POINTS - 1))
            indices.append(n - 1)
            labels = [labels[i] for i in indices]
            series_comment = [series_comment[i] for i in indices]
            series_favorite = [series_favorite[i] for i in indices]
            series_fans = [series_fans[i] for i in indices]
            for ds in song_comment_datasets:
                ds["data"] = [ds["data"][i] for i in indices]
            for ds in song_favorite_datasets:
                ds["data"] = [ds["data"][i] for i in indices]

        if use_absolute_favorite and base_dir is not None:
            from .new_song import get_new_song_current_metrics

            current = get_new_song_current_metrics(base_dir=base_dir)
            plat = current.get("platforms", {}).get(platform, {})
            if plat.get("ok") and plat.get("favorite_count") is not None:
                cur_fav = int(plat["favorite_count"])
                last_val = series_favorite[-1] if series_favorite else None
                if last_val is None or last_val != cur_fav:
                    labels = list(labels) + ["当前"]
                    series_favorite = list(series_favorite) + [cur_fav]

        return {
            "ok": True,
            "labels": labels,
            "series": {
                "comment": series_comment,
                "favorite": series_favorite,
                "fans": series_fans,
            },
            "song_comment": {"labels": labels, "datasets": song_comment_datasets},
            "song_favorite": {"labels": labels, "datasets": song_favorite_datasets},
            "song_fans": {"labels": labels, "datasets": [{"name": "粉丝", "data": series_fans}]},
        }
    except (sqlite3.Error, ValueError, OSError) as e:
        return {"ok": False, "error": "数据查询异常: {}".format(str(e))}
    finally:
        conn.close()

