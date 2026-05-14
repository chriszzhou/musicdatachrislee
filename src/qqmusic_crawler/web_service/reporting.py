from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..sqlite_util import connect_sqlite
from ..toplist_storage import get_artist_mid_from_toplist_db
from ..tracking import (
    _ensure_changes_tables,
    _ensure_month_table,
    _list_change_month_tables,
    _report_month_keys,
    _table_name,
    report_changes,
)

from .constants import NEW_SONG_NAME
from .paths import SUPPORTED_PLATFORMS, _resolve_changes_db_path, _resolve_toplist_db_path

# 变化报告页：各平台「收藏增长 / 粉丝增长」条形图及汇总仅展示增量 >= 该阈值的项
MIN_CHANGE_REPORT_DISPLAY_DELTA = 0


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

    conn = connect_sqlite(changes_db_path, row_factory=sqlite3.Row)
    try:
        _ensure_changes_tables(conn)
        month_keys_metric = _report_month_keys(conn, "metric_changes", year_str, month_str, date_str)
        month_keys_artist = _report_month_keys(conn, "artist_metric_changes", year_str, month_str, date_str)
        if not month_keys_metric:
            month_keys_metric = [datetime.now().strftime("%Y%m")]
        if not month_keys_artist:
            month_keys_artist = [datetime.now().strftime("%Y%m")]
        for mk in month_keys_metric:
            _ensure_month_table(conn, "metric_changes", mk)
        for mk in month_keys_artist:
            _ensure_month_table(conn, "artist_metric_changes", mk)

        def _from(base: str, keys: List[str]) -> str:
            if len(keys) == 1:
                return _table_name(base, keys[0])
            parts = ["SELECT * FROM {}".format(_table_name(base, mk)) for mk in keys]
            return "({}) AS {}".format(" UNION ALL ".join(parts), base)

        metric_from = _from("metric_changes", month_keys_metric)
        artist_from = _from("artist_metric_changes", month_keys_artist)

        amid = (artist_mid or "").strip()
        artist_filter = " AND artist_mid = ?" if amid else ""
        base_params: List[object] = []
        if year_str:
            where_sql = "substr(run_at, 1, 4) = ?"
            base_params = [year_str]
        elif month_str:
            where_sql = "substr(run_at, 1, 7) = ?"
            base_params = [month_str]
        else:
            where_sql = "date(run_at) = ?"
            base_params = [date_str or datetime.now().strftime("%Y-%m-%d")]
        q_params = list(base_params) + ([amid] if amid else [])

        # 歌曲收藏：SQL 层按 song_mid 聚合 + MAX trick 取最新 new_value
        fav_rows = conn.execute(
            """
            SELECT song_mid, song_name,
                   SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS total_delta,
                   MAX(run_at) AS last_run_at,
                   SUBSTR(
                       MAX(run_at || '|' || CAST(new_value AS TEXT)),
                       INSTR(MAX(run_at || '|' || CAST(new_value AS TEXT)), '|') + 1
                   ) AS latest_new_value
            FROM {tbl}
            WHERE {whr} {af}
              AND metric = 'favorite_count_text'
              AND is_init = 0
            GROUP BY song_mid
            HAVING total_delta > 0
            """.format(tbl=metric_from, whr=where_sql, af=artist_filter),
            q_params,
        ).fetchall()

        song_deltas: Dict[str, Dict[str, Any]] = {}
        for row in fav_rows:
            mid = str(row["song_mid"] or "").strip()
            if not mid:
                continue
            name = str(row["song_name"] or mid).strip() or mid
            delta = int(row["total_delta"] or 0)
            nv = row["latest_new_value"]
            song_deltas[mid] = {
                "song_name": name,
                "favorite": delta,
                "favorite_new": int(nv) if nv is not None else None,
                "favorite_run_at": str(row["last_run_at"] or ""),
            }

        # 粉丝：SQL 层按 artist_name 聚合 + MAX trick 取最新 new_value
        artist_q_params = list(base_params) + ([amid] if amid else [])
        fans_rows = conn.execute(
            """
            SELECT artist_name,
                   SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS total_delta,
                   MAX(run_at) AS last_run_at,
                   SUBSTR(
                       MAX(run_at || '|' || CAST(new_value AS TEXT)),
                       INSTR(MAX(run_at || '|' || CAST(new_value AS TEXT)), '|') + 1
                   ) AS latest_new_value
            FROM {tbl}
            WHERE {whr} {af}
              AND metric = 'fans'
              AND is_init = 0
            GROUP BY artist_name
            HAVING total_delta > 0
            """.format(tbl=artist_from, whr=where_sql, af=artist_filter),
            artist_q_params,
        ).fetchall()

        artist_deltas: Dict[str, Dict[str, Any]] = {}
        for row in fans_rows:
            aname = str(row["artist_name"] or "").strip()
            if not aname:
                continue
            delta = int(row["total_delta"] or 0)
            nv = row["latest_new_value"]
            artist_deltas[aname] = {
                "delta": delta,
                "new": int(nv) if nv is not None else None,
                "run_at": str(row["last_run_at"] or ""),
            }
    finally:
        conn.close()

    favorite_items: List[Tuple[int, bool, str]] = []
    favorite_chart_rows: List[Dict[str, Any]] = []
    for song_mid, values in song_deltas.items():
        favorite_new = values.get("favorite_new")
        favorite_delta_value = int(values.get("favorite") or 0)
        if favorite_delta_value < MIN_CHANGE_REPORT_DISPLAY_DELTA:
            continue
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
    favorite_items.sort(key=lambda x: (-x[0], x[1]))
    favorite_chart_rows.sort(key=lambda x: (-int(x.get("delta_abs") or 0), int(x.get("delta") or 0) < 0))

    artist_items: List[Tuple[int, bool, str]] = []
    artist_chart_rows: List[Dict[str, Any]] = []
    for artist_name, values in artist_deltas.items():
        delta_value = int(values.get("delta") or 0)
        if delta_value < MIN_CHANGE_REPORT_DISPLAY_DELTA:
            continue
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

    favorite_delta_shown = sum(int(r.get("delta") or 0) for r in favorite_chart_rows)
    fans_delta_shown = sum(int(r.get("delta") or 0) for r in artist_chart_rows)

    return {
        "ok": True,
        "label": label,
        "value": value_clean,
        "mode": mode_clean,
        "song_summary": {
            "affected_songs": len(favorite_chart_rows),
            "favorite_delta": favorite_delta_shown,
        },
        "artist_summary": {
            "affected_artists": len(artist_chart_rows),
            "fans_delta": fans_delta_shown,
        },
        "song_names_favorite": [x[2] for x in favorite_items[:song_display_limit]],
        "artist_names": [x[2] for x in artist_items],
        "favorite_chart_rows": favorite_chart_rows[:song_display_limit],
        "artist_chart_rows": artist_chart_rows[:song_display_limit],
    }


def get_reports_all_platforms(
    mode: str,
    value: str,
    artist_name: str,
    base_dir: Optional[Path] = None,
    song_display_limit: int = 15,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """为 QQ / 网易云 / 酷狗 各生成一份变化报告。三平台并行查询。"""
    from concurrent.futures import ThreadPoolExecutor

    name_stub = (artist_name or "").strip()
    mids: Dict[str, str] = {}
    for plat in SUPPORTED_PLATFORMS:
        if name_stub:
            db_file = _resolve_toplist_db_path(plat, base_dir)
            resolved = get_artist_mid_from_toplist_db(db_file, name_stub)
            mids[plat] = (resolved or "").strip()
        else:
            mids[plat] = ""

    def _run_one(plat: str) -> Tuple[str, Dict[str, Any]]:
        return plat, get_report(
            platform=plat,
            mode=mode,
            value=value,
            artist_mid=mids[plat],
            song_display_limit=song_display_limit,
            base_dir=base_dir,
        )

    reports: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for plat, report in pool.map(_run_one, SUPPORTED_PLATFORMS):
            reports[plat] = report
    return reports, mids


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
    返回 labels 与 series（favorite / fans）。
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
        return {"ok": True, "labels": [], "series": {"favorite": [], "fans": []}, "song_favorite": empty, "song_fans": empty}

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

        for mk in month_keys_metric:
            _ensure_month_table(conn, "metric_changes", mk)
        for mk in month_keys_artist:
            _ensure_month_table(conn, "artist_metric_changes", mk)

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
            labels_from_song_query = False
        else:
            labels = []
            labels_from_song_query = True

        amid = (artist_mid or "").strip()
        sname = (song_name or "").strip()
        base_q_params = list(base_params) + ([amid] if amid else []) + ([sname] if sname else [])
        base_q_params_artist = list(base_params) + ([amid] if amid else [])

        # 一次查询完成：按 (period, song_mid) 聚合收藏，从中推导 labels 和 series_favorite
        song_rows = conn.execute(
            """
            SELECT {grp} AS period, song_mid, song_name, COALESCE(SUM(delta), 0) AS sdelta
            FROM {tbl}
            WHERE {whr} {af} {sf} AND metric = 'favorite_count_text' AND is_init = 0
            GROUP BY {grp}, song_mid
            """.format(grp=group_sql, tbl=metric_from, whr=where_sql, af=artist_filter, sf=song_filter),
            base_q_params,
        ).fetchall()

        # 从 song_rows 推导 labels（如果不是 day 模式的特殊处理）
        if labels_from_song_query:
            labels_set: set = set()
            for row in song_rows:
                labels_set.add(str(row[0]))
            labels = sorted(labels_set)

        if not labels:
            empty: Dict[str, Any] = {"labels": [], "datasets": []}
            return {"ok": True, "labels": [], "series": {"favorite": [], "fans": []}, "song_favorite": empty, "song_fans": empty}

        label_index = {p: i for i, p in enumerate(labels)}

        # 从 song_rows 构建 series_favorite 和按歌曲维度数据
        series_favorite: List[int] = [0] * len(labels)
        top_songs = 10
        favorite_by_song: Dict[str, List[int]] = {}
        song_names: Dict[str, str] = {}

        if use_absolute_favorite:
            # 绝对值模式：需要逐 period 取最后 new_value，单独查询
            for period in labels:
                if mode_clean == "day":
                    period_where = "run_at = ?"
                else:
                    period_where = "substr(run_at, 1, {}) = ?".format(len(period))
                pp = [period] + ([amid] if amid else []) + ([sname] if sname else [])
                fav_row = conn.execute(
                    """
                    SELECT new_value FROM {}
                    WHERE {} {} {} AND metric = 'favorite_count_text'
                    ORDER BY run_at DESC LIMIT 1
                    """.format(metric_from, period_where, artist_filter, song_filter),
                    pp,
                ).fetchone()
                idx = label_index[period]
                series_favorite[idx] = int(fav_row[0] or 0) if fav_row else 0

        for row in song_rows:
            period_key = str(row[0])
            mid = str(row[1] or "").strip()
            if not mid:
                continue
            name = str(row[2] or mid).strip() or mid
            delta = int(row[3] or 0)
            idx = label_index.get(period_key)
            if idx is None:
                continue
            if not use_absolute_favorite:
                series_favorite[idx] += delta
            if mid not in song_names:
                song_names[mid] = name
            if mid not in favorite_by_song:
                favorite_by_song[mid] = [0] * len(labels)
            favorite_by_song[mid][idx] = delta

        # 一次 GROUP BY 查出所有 period 的粉丝 delta 合计
        fans_rows = conn.execute(
            """
            SELECT {grp}, COALESCE(SUM(delta), 0)
            FROM {tbl}
            WHERE {whr} {af} AND metric = 'fans' AND is_init = 0
            GROUP BY {grp} ORDER BY {grp}
            """.format(grp=group_sql, tbl=artist_from, whr=where_sql, af=artist_filter),
            base_q_params_artist,
        ).fetchall()
        fans_map = {str(r[0]): int(r[1] or 0) for r in fans_rows}
        series_fans = [fans_map.get(p, 0) for p in labels]

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
            series_favorite = [series_favorite[i] for i in indices]
            series_fans = [series_fans[i] for i in indices]
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
                "favorite": series_favorite,
                "fans": series_fans,
            },
            "song_favorite": {"labels": labels, "datasets": song_favorite_datasets},
            "song_fans": {"labels": labels, "datasets": [{"name": "粉丝", "data": series_fans}]},
        }
    except (sqlite3.Error, ValueError, OSError) as e:
        return {"ok": False, "error": "数据查询异常: {}".format(str(e))}
    finally:
        conn.close()

