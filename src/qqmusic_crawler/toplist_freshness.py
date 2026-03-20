"""
榜单「数据时效」：用各平台落库的 top_update_time / top_period 推断榜单内容对应的日期，
过滤掉「日榜声明更新日早于今日」的记录，避免按 last_seen_at=今日 误把旧榜当今日。

周榜：不做「本周 / 上一 ISO 周」类日历限制；同一榜单（top_id + top_name）仅保留最新一期
（优先按解析出的声明日，否则按周期字段如 2026_12、再否则按 last_seen_at）。
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

BEIJING_TZ = timezone(timedelta(hours=8))

_DATE_YMD_RE = re.compile(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})")
# QQ 周更类榜单常见 top_period：整串为「年_期号」，如 2026_12（与日更的 2026-03-20 区分：后者为三段日期）
_QQ_YEAR_WEEK_PERIOD_FULL_RE = re.compile(r"^20\d{2}_\d{1,2}$")
# QQ 等：从字符串末尾解析年+序号（用于无日历日时的期号比较）
_YEAR_PERIOD_TAIL_RE = re.compile(r"(20\d{2})[_\s\-/年](\d{1,2})\s*$")


def _is_likely_weekly_chart(top_name: str, top_period: str) -> bool:
    """是否按「周榜」做展示策略（不按日榜要求声明日≥今日；同榜只保留最新一期）。"""
    s = "{} {}".format(top_name or "", top_period or "")
    p = str(top_period or "").strip()
    if "每周" in s:
        return True
    if p and _QQ_YEAR_WEEK_PERIOD_FULL_RE.match(p):
        return True
    keys = (
        "周榜",
        "周刊",
        "周报",
        "一周",
        "week",
        "Week",
        "weekly",
        "WEEKLY",
        "7天",
        "七日",
    )
    return any(k in s for k in keys)


def _year_period_tuple_from_row(row: Dict[str, Any]) -> Optional[Tuple[int, int, int]]:
    """从 top_period / top_update_time 末尾解析「年 + 周期序号」，用于无日历日时的周榜期号比较。"""
    for src in (str(row.get("top_period") or ""), str(row.get("top_update_time") or "")):
        t = src.strip()
        if not t:
            continue
        m = _YEAR_PERIOD_TAIL_RE.search(t)
        if m:
            y, n = int(m.group(1)), int(m.group(2))
            if 2000 <= y <= 2100 and 1 <= n <= 53:
                return (y, n, 0)
    return None


def _date_from_timestamp_seconds(n: int) -> Optional[date]:
    if n <= 0:
        return None
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).astimezone(BEIJING_TZ).date()
    except (OSError, OverflowError, ValueError):
        return None


def infer_chart_asof_date(row: Dict[str, Any]) -> Optional[date]:
    """
    从一行榜单命中记录推断「该榜数据所声明的更新/结算日期」（北京时间日历日）。
    无法解析时返回 None（调用方可选择保留或丢弃该行）。
    """
    ut = str(row.get("top_update_time") or "").strip()
    period = str(row.get("top_period") or "").strip()

    if ut:
        if ut.isdigit():
            n = int(ut)
            if n > 1_000_000_000_000:
                n //= 1000
            if n > 1_000_000_000:
                d = _date_from_timestamp_seconds(n)
                if d:
                    return d
        try:
            n2 = int(float(ut))
            if n2 > 1_000_000_000_000:
                n2 //= 1000
            if n2 > 1_000_000_000:
                d = _date_from_timestamp_seconds(n2)
                if d:
                    return d
        except (TypeError, ValueError):
            pass

    for s in (ut, period):
        if not s:
            continue
        m = _DATE_YMD_RE.search(s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
        digits = re.sub(r"\D", "", s)
        if len(digits) >= 8:
            try:
                y, mo, da = int(digits[0:4]), int(digits[4:6]), int(digits[6:8])
                if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= da <= 31:
                    return date(y, mo, da)
            except ValueError:
                pass
    return None


def row_matches_beijing_calendar_day(row: Dict[str, Any], today_bj: date) -> bool:
    """
    非周榜：榜单声明日期 >= 今日（北京时间）。

    周榜：不在此按「今日」截断；新鲜度由「同榜仅保留最新一期」处理。
    """
    if _is_likely_weekly_chart(str(row.get("top_name") or ""), str(row.get("top_period") or "")):
        return True
    d = infer_chart_asof_date(row)
    if d is None:
        return True
    return d >= today_bj


def _dedupe_weekly_charts_to_latest_period(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    对每个周榜（top_id + top_name）只保留「最新一期」行。
    优先：声明日 infer_chart_asof_date 最大；若无任何声明日：年+周期序号最大；再否则 last_seen_at 最大。
    返回 (保留行, 因非最新期丢弃的周榜行数)。
    """
    weekly: List[Dict[str, Any]] = []
    other: List[Dict[str, Any]] = []
    for r in rows:
        if _is_likely_weekly_chart(str(r.get("top_name") or ""), str(r.get("top_period") or "")):
            weekly.append(r)
        else:
            other.append(r)
    if not weekly:
        return rows, 0

    groups: Dict[Tuple[Any, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in weekly:
        key = (r.get("top_id"), str(r.get("top_name") or "").strip())
        groups[key].append(r)

    kept_weekly: List[Dict[str, Any]] = []
    dropped = 0
    for group in groups.values():
        dates = [infer_chart_asof_date(r) for r in group]
        non_none_dates = [d for d in dates if d is not None]
        if non_none_dates:
            best_d = max(non_none_dates)
            chosen = [r for r in group if infer_chart_asof_date(r) == best_d]
        else:
            tuples = [_year_period_tuple_from_row(r) for r in group]
            non_none_t = [t for t in tuples if t is not None]
            if non_none_t:
                best_t = max(non_none_t)
                chosen = [r for r, t in zip(group, tuples) if t == best_t]
            else:
                max_seen = max((str(r.get("last_seen_at") or "") for r in group), default="")
                chosen = [r for r in group if str(r.get("last_seen_at") or "") == max_seen]
        dropped += len(group) - len(chosen)
        kept_weekly.extend(chosen)

    return other + kept_weekly, dropped


def filter_toplist_rows_for_today(
    rows: List[Dict[str, Any]],
    today_bj: date,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    过滤「日榜声明更新日早于今日」；周榜不按今日截断，同一榜单仅保留最新一期。
    返回 (过滤后列表, 统计信息)。
    """
    if not rows:
        return [], {
            "calendar_today": today_bj.isoformat(),
            "rows_before": 0,
            "rows_after": 0,
            "rows_dropped_stale": 0,
            "rows_dropped_calendar": 0,
            "rows_dropped_weekly_old_period": 0,
            "rows_unknown_date": 0,
            "warn_all_unknown": False,
        }
    unknown = sum(1 for r in rows if infer_chart_asof_date(r) is None)
    kept = [r for r in rows if row_matches_beijing_calendar_day(r, today_bj)]
    dropped_calendar = len(rows) - len(kept)

    kept2, dropped_weekly = _dedupe_weekly_charts_to_latest_period(kept)
    dropped_total = len(rows) - len(kept2)

    return kept2, {
        "calendar_today": today_bj.isoformat(),
        "rows_before": len(rows),
        "rows_after": len(kept2),
        "rows_dropped_stale": dropped_total,
        "rows_dropped_calendar": dropped_calendar,
        "rows_dropped_weekly_old_period": dropped_weekly,
        "rows_unknown_date": unknown,
        "warn_all_unknown": unknown == len(rows) and len(rows) > 0,
    }
