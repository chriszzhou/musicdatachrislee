from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..crawler import CrawlerService
from ..toplist_freshness import filter_toplist_rows_for_today, infer_chart_asof_date
from ..toplist_storage import (
    get_artist_mid_from_toplist_db,
    query_all_toplist_hits_since,
    query_artist_toplist_hits,
    query_artist_toplist_hits_since,
    upsert_artist_toplist_hits,
)

from .clients import build_client, _resolve_artist
from .paths import (
    SUPPORTED_PLATFORMS,
    _resolve_toplist_db_path,
    get_platform_meta,
)

def check_artist_toplist(
    platform: str,
    artist_name: str,
    top_n: int = 300,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    top_n_safe = top_n if top_n and top_n > 0 else 300
    meta = get_platform_meta(platform)
    client = build_client(platform)
    service = CrawlerService(client=client)
    try:
        resolved = _resolve_artist(service, artist_name)
        if not resolved:
            return {"ok": False, "error": "未找到歌手，请重试。"}
        artist_mid, resolved_name = resolved

        hits = service.find_artist_toplist_hits(artist_mid=artist_mid, top_n=top_n_safe)
        db_file = _resolve_toplist_db_path(platform, base_dir)
        upserted = upsert_artist_toplist_hits(
            db_file=db_file,
            artist_mid=artist_mid,
            artist_name=resolved_name,
            hits=hits,
        )
        rows = query_artist_toplist_hits(db_file=db_file, artist_mid=artist_mid, limit=300)
        return {
            "ok": True,
            "artist_mid": artist_mid,
            "artist_name": resolved_name,
            "hits_count": len(hits),
            "upserted": upserted,
            "rows_count": len(rows),
            "rows": rows,
        }
    finally:
        client.close()


def _dedupe_netease_toplist_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """网易云榜单去重：同一榜单同一歌曲只保留一条（按 last_seen_at 取最新）。"""
    if not rows:
        return rows
    seen: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        key = (r.get("top_id"), r.get("top_name"), (r.get("song_mid") or "").strip())
        existing = seen.get(key)
        if existing is None or (r.get("last_seen_at") or "") > (existing.get("last_seen_at") or ""):
            seen[key] = r
    return list(seen.values())


def get_today_toplist_from_platform_dbs(
    artist_name: Optional[str] = None,
    base_dir: Optional[Path] = None,
    last_seen_since: str = "",
    all_songs: bool = True,
) -> List[Dict[str, Any]]:
    """从三平台现有榜单库读今日上榜数据。all_songs=True 时返回所有歌曲（不按歌手过滤）；否则按 artist_name 过滤。网易云平台会对结果去重。

    会按 top_update_time / top_period 解析出的「榜单声明更新日」过滤：非周榜需声明日 >= 今日（北京）；
    周榜不做「今日 / ISO 周」截断，同一榜单（top_id + top_name）仅保留最新一期。
    """
    beijing_now = datetime.now(timezone(timedelta(hours=8)))
    today_bj = beijing_now.date()
    if not last_seen_since or len(last_seen_since) < 10:
        last_seen_since = beijing_now.strftime("%Y-%m-%d 00:00:00")
    results: List[Dict[str, Any]] = []
    for platform in SUPPORTED_PLATFORMS:
        meta = get_platform_meta(platform)
        db_file = _resolve_toplist_db_path(platform, base_dir)
        if all_songs:
            rows = query_all_toplist_hits_since(db_file, last_seen_since, limit=1000)
        else:
            artist_mid = get_artist_mid_from_toplist_db(db_file, artist_name or "")
            if not artist_mid:
                client = build_client(platform)
                service = CrawlerService(client=client)
                try:
                    resolved = _resolve_artist(service, artist_name or "")
                    if resolved:
                        artist_mid, _ = resolved
                finally:
                    client.close()
            if not artist_mid:
                results.append({
                    "platform": platform,
                    "platform_name": meta["name"],
                    "ok": False,
                    "hits_count": 0,
                    "error": "未找到该歌手榜单数据，请先执行一次榜单拉取。",
                    "rows": [],
                })
                continue
            rows = query_artist_toplist_hits_since(db_file, artist_mid, last_seen_since, limit=500)
        if platform == "netease":
            rows = _dedupe_netease_toplist_rows(rows)

        rows_fresh, finfo = filter_toplist_rows_for_today(list(rows), today_bj)
        for r in rows_fresh:
            d = infer_chart_asof_date(r)
            if d:
                r["chart_asof_date"] = d.isoformat()

        freshness_note = ""
        dc = int(finfo.get("rows_dropped_calendar") or 0)
        dw = int(finfo.get("rows_dropped_weekly_old_period") or 0)
        if dc > 0 and dw > 0:
            freshness_note = "已过滤 {} 条（日榜声明更新早于今日 {}；周榜同榜仅保留最新一期 {}）。".format(
                finfo["rows_dropped_stale"],
                dc,
                dw,
            )
        elif dc > 0:
            freshness_note = "已过滤 {} 条「日榜声明更新早于今日」的记录。".format(finfo["rows_dropped_stale"])
        elif dw > 0:
            freshness_note = "已合并 {} 条周榜旧期记录（同榜仅展示最新一期）。".format(dw)
        if finfo.get("warn_all_unknown"):
            extra = " 该平台未能从榜单接口字段解析出更新日期，已暂时全部展示（若仍不准请检查 top_update_time 格式）。"
            freshness_note = (freshness_note + extra).strip()

        results.append({
            "platform": platform,
            "platform_name": meta["name"],
            "ok": True,
            "hits_count": len(rows_fresh),
            "error": None,
            "rows": sorted(
                rows_fresh, key=lambda x: (str(x.get("top_name") or ""), int(x.get("rank") or 0))
            ),
            "toplist_freshness": finfo,
            "freshness_note": freshness_note,
        })
    run_at = last_seen_since[:10] if len(last_seen_since) >= 10 else ""
    return [
        {
            "run_at": run_at,
            "calendar_today_bj": today_bj.isoformat(),
            "results": results,
        }
    ]

