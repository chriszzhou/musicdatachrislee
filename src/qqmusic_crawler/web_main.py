from __future__ import annotations

import asyncio
import functools
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TypeVar

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
import httpx

from . import schedulers as _sched
from .config import settings
from .web_service import (
    SUPPORTED_PLATFORMS,
    delete_milestone_entry,
    get_artist_snapshot_metrics_all_platforms,
    get_milestone_logs,
    get_new_song_chart_data,
    get_new_song_current_metrics,
    get_new_song_toplist_rows,
    get_platform_meta,
    get_report_chart_data,
    get_reports_all_platforms,
    get_today_toplist_from_platform_dbs,
    get_top_songs,
    get_top_songs_slice,
    normalize_platform,
    search_songs_all_platforms,
    resolve_data_paths_for_debug,
)
from .web_service.milestones import prune_milestone_logs_sub_10k_entries
from .heat_scraper import get_song_heat, close_browser, fetch_batch_listen_users
from .heat_api import get_songs_heat_batch
from .client import QQMusicClient
from .kugou_heat_scraper import get_kugou_song_heat, close_kg_browser, fetch_batch_kugou_heat

app = FastAPI(title="Music Crawler Web")

_T = TypeVar("_T")


async def _run_in_thread(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """在默认线程池执行同步函数，避免阻塞 asyncio 事件循环（其它 /api 请求可并发处理）。"""
    loop = asyncio.get_running_loop()
    call: Callable[[], _T] = functools.partial(fn, *args, **kwargs)
    return await loop.run_in_executor(None, call)


def _detect_project_root() -> Path:
    """检测项目根目录：环境变量 QQMC_DATA_DIR > 代码目录（含 data+src）> cwd 下含 data 的目录。"""
    env_data = os.environ.get("QQMC_DATA_DIR")
    if env_data:
        p = Path(env_data).resolve()
        if (p / "data").is_dir() or p.name == "data":
            return p if p.name != "data" else p.parent
        if p.is_dir():
            return p
    code_root = Path(__file__).resolve().parents[2]
    if (code_root / "data").is_dir() and (code_root / "src").is_dir():
        return code_root
    cwd = Path.cwd().resolve()
    if (cwd / "data").is_dir():
        return cwd
    return code_root


PROJECT_ROOT = _detect_project_root()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))
app.mount("/scripts", StaticFiles(directory=str(PROJECT_ROOT / "scripts"), check_dir=False), name="scripts")

# 与定时任务、新歌 API 共用北京时间（定义见 schedulers）
BEIJING_TZ = _sched.BEIJING_TZ
TOPLIST_ARTIST_NAME = _sched.TOPLIST_ARTIST_NAME


def _base_context(platform: str) -> Dict[str, Any]:
    p = normalize_platform(platform)
    return {
        "platform": p,
        "platforms": list(SUPPORTED_PLATFORMS),
        "platform_name": get_platform_meta(p)["name"],
        "platform_display_names": {x: get_platform_meta(x)["name"] for x in SUPPORTED_PLATFORMS},
        "message": "",
        "error": "",
        "result_type": "",
        "result": {},
        "form": {},
        "new_song_name": (settings.qqmc_new_song_name or "").strip() or "春雨里",
        "default_topsongs_artist": settings.effective_default_topsongs_artist,
        "new_song_update_interval_sec": settings.qqmc_new_song_update_interval_sec,
        "reports_by_platform": {},
        "report_artist_mids": {},
        "report_mode": "day",
        "report_value": "",
        "home_artist_metrics": {"ok": False, "by_platform": {}, "display_name": ""},
        "home_artist_metrics_json": "{}",
    }


def _execute_action_and_build_context(
    action: str,
    platform: str,
    form_dict: Dict[str, str],
) -> Dict[str, Any]:
    """表单动作同步逻辑（在线程池中执行，避免阻塞事件循环）。"""
    context = _base_context(platform)
    context["form"] = form_dict
    try:
        if action == "search-songs":
            song_keyword = str(form_dict.get("song_keyword") or "").strip()
            data = search_songs_all_platforms(
                keyword=song_keyword,
                base_dir=PROJECT_ROOT,
                limit=5,
            )
            context["result_type"] = "search-songs"
            context["result"] = data
            if data.get("ok"):
                context["message"] = "歌曲搜索完成（三平台）。"
            else:
                context["error"] = str(data.get("error") or "歌曲搜索失败。")
        elif action == "report-changes":
            report_mode = str(form_dict.get("report_mode") or "day").strip()
            if report_mode not in ("year", "month", "day"):
                report_mode = "day"
            value = str(form_dict.get("report_value") or "").strip()
            if not value:
                if report_mode == "year":
                    value = str(datetime.now(BEIJING_TZ).year)
                elif report_mode == "month":
                    value = datetime.now(BEIJING_TZ).strftime("%Y-%m")
                else:
                    value = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
            artist_name = settings.effective_default_topsongs_artist
            reports, mids = get_reports_all_platforms(
                mode=report_mode,
                value=value,
                artist_name=artist_name,
                base_dir=PROJECT_ROOT,
                song_display_limit=15,
            )
            context["reports_by_platform"] = reports
            context["report_artist_mids"] = mids
            context["report_mode"] = report_mode
            context["report_value"] = value
            context["result_type"] = ""
            ok_all = all(r.get("ok") for r in reports.values())
            if ok_all:
                context["message"] = "变化报告生成完成。"
            else:
                errs = [str(r.get("error") or "") for r in reports.values() if not r.get("ok")]
                context["error"] = "；".join(e for e in errs if e) or "变化报告生成失败。"
        elif action == "top-songs":
            da = settings.effective_default_topsongs_artist
            artist_name = str(form_dict.get("topsongs_artist_name") or da).strip() or da
            top_n_raw = str(form_dict.get("topsongs_n") or "").strip()
            try:
                top_n = int(top_n_raw) if top_n_raw else 15
            except ValueError:
                top_n = 15
            data = get_top_songs(
                platform=platform,
                artist_name=artist_name,
                top_n=top_n,
                base_dir=PROJECT_ROOT,
            )
            context["result_type"] = "top-songs"
            context["result"] = data
            if data.get("ok"):
                context["message"] = "歌曲TOP N查询完成。"
            else:
                context["error"] = str(data.get("error") or "歌曲TOP N查询失败。")
        else:
            context["error"] = "未知操作: {}".format(action)
    except Exception as exc:
        context["error"] = "操作失败: {}".format(str(exc))
    return context


def _toplist_check_history_payload() -> Dict[str, Any]:
    now = datetime.now(BEIJING_TZ)
    today_str = now.strftime("%Y-%m-%d")
    last_seen_since = today_str + " 00:00:00"
    runs = get_today_toplist_from_platform_dbs(
        TOPLIST_ARTIST_NAME,
        base_dir=PROJECT_ROOT,
        last_seen_since=last_seen_since,
        all_songs=False,
    )
    return {"ok": True, "runs": runs, "date_filter": today_str}


def _debug_paths_payload() -> Dict[str, Any]:
    data = resolve_data_paths_for_debug(PROJECT_ROOT)
    data["project_root"] = str(PROJECT_ROOT)
    return data


def _toplist_run_now_payload() -> Dict[str, Any]:
    _sched.run_scheduled_toplist_check()
    now = datetime.now(BEIJING_TZ)
    last_seen_since = now.strftime("%Y-%m-%d") + " 00:00:00"
    runs = get_today_toplist_from_platform_dbs(
        TOPLIST_ARTIST_NAME,
        base_dir=PROJECT_ROOT,
        last_seen_since=last_seen_since,
        all_songs=False,
    )
    last = runs[0] if runs else None
    return {"ok": True, "run": last}


@app.on_event("startup")
async def _start_schedulers() -> None:
    prune_milestone_logs_sub_10k_entries(PROJECT_ROOT)
    _sched.start_background_schedulers(PROJECT_ROOT)


def _home_metrics_payload() -> Dict[str, Any]:
    """首页首屏：三平台粉丝与收藏合计（饼图数据）。"""
    artist = settings.effective_default_topsongs_artist
    data = get_artist_snapshot_metrics_all_platforms(
        artist_name=artist,
        base_dir=PROJECT_ROOT,
    )
    return {
        "home_artist_metrics": data,
        "home_artist_metrics_json": json.dumps(data, ensure_ascii=False),
    }


def _changereport_payload() -> Dict[str, Any]:
    """变化报告页：默认当前日、固定默认歌手（如李宇春），三平台各一份报告。"""
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    artist = settings.effective_default_topsongs_artist
    reports, mids = get_reports_all_platforms(
        mode="day",
        value=today,
        artist_name=artist,
        base_dir=PROJECT_ROOT,
        song_display_limit=15,
    )
    return {
        "reports_by_platform": reports,
        "report_artist_mids": mids,
        "report_mode": "day",
        "report_value": today,
    }


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, platform: str = "qq") -> HTMLResponse:
    context = _base_context(platform)
    context.update(await _run_in_thread(_home_metrics_payload))
    context["request"] = request
    return templates.TemplateResponse("index.html", context)


@app.get("/changereport", response_class=HTMLResponse)
async def changereport_page(request: Request, platform: str = "qq") -> HTMLResponse:
    """歌手数据变化报告（三平台），独立页面以减轻首页体积。"""
    context = _base_context(platform)
    context.update(await _run_in_thread(_changereport_payload))
    context["request"] = request
    return templates.TemplateResponse("changereport.html", context)


@app.get("/new-song", response_class=HTMLResponse)
async def new_song_page(request: Request) -> HTMLResponse:
    """新歌页：当前三平台收藏量、收藏曲线、榜单数据（歌名见配置 QQMC_NEW_SONG_NAME）。"""
    context = _base_context("qq")
    context["request"] = request
    return templates.TemplateResponse("new_song.html", context)


@app.get("/api/new-song/current")
async def api_new_song_current() -> JSONResponse:
    """新歌页用：当前歌曲三平台收藏量。"""
    data = await _run_in_thread(get_new_song_current_metrics, base_dir=PROJECT_ROOT)
    return JSONResponse(data)


@app.get("/api/new-song/chart")
async def api_new_song_chart(
    platform: str = "qq",
    mode: str = "day",
    value: str = "",
) -> JSONResponse:
    """新歌页用：单平台收藏量变化曲线（song_name 由 QQMC_NEW_SONG_NAME 配置）。"""
    if not value:
        value = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    data = await _run_in_thread(
        get_new_song_chart_data,
        platform=normalize_platform(platform),
        mode=mode or "day",
        value=value,
        base_dir=PROJECT_ROOT,
    )
    return JSONResponse(data)


@app.get("/api/new-song/toplist")
async def api_new_song_toplist() -> JSONResponse:
    """新歌页用：三平台榜单中配置的新歌名的上榜记录。"""
    data = await _run_in_thread(get_new_song_toplist_rows, base_dir=PROJECT_ROOT)
    return JSONResponse({"ok": True, "items": data})


@app.get("/api/new-song/last-update")
async def api_new_song_last_update() -> JSONResponse:
    """新歌页用：上次定时拉取更新时间（北京时间）、以及服务端「今日」日期（供折线图默认用）。"""
    with _sched.NEW_SONG_LAST_UPDATE_LOCK:
        at = _sched.NEW_SONG_LAST_UPDATE_AT
    date_today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    return JSONResponse({"ok": True, "last_update_at": at or "", "date_today": date_today})


@app.get("/api/home-metrics")
async def api_home_metrics() -> JSONResponse:
    """首页三平台概览数据（供前端按任务完成后刷新）。"""
    data = await _run_in_thread(
        get_artist_snapshot_metrics_all_platforms,
        artist_name=settings.effective_default_topsongs_artist,
        base_dir=PROJECT_ROOT,
    )
    return JSONResponse(data)


@app.get("/api/crawl-track/status")
async def api_crawl_track_status() -> JSONResponse:
    """返回 crawl_track 最近一轮完成时间（写库/写日志完成后更新）。"""
    with _sched.CRAWL_TRACK_LAST_FINISHED_LOCK:
        at = _sched.CRAWL_TRACK_LAST_FINISHED_AT
    return JSONResponse({"ok": True, "last_finished_at": at or ""})


@app.get("/api/top-songs")
async def api_top_songs(
    platform: str,
    offset: int = 0,
    limit: int = 10,
    artist: str = "",
) -> JSONResponse:
    """分页：歌手「收藏」TOP 排行（快照）。"""
    name = (artist or "").strip() or settings.effective_default_topsongs_artist
    data = await _run_in_thread(
        get_top_songs_slice,
        normalize_platform(platform),
        name,
        offset,
        limit,
        PROJECT_ROOT,
    )
    return JSONResponse(data)


@app.get("/api/search-songs")
async def api_search_songs(song_keyword: str = "", limit: int = 5) -> JSONResponse:
    """首页歌曲搜索：三平台最新快照异步查询（不切页）。"""
    keyword = (song_keyword or "").strip()
    limit_safe = min(max(1, int(limit)), 50)
    data = await _run_in_thread(
        search_songs_all_platforms,
        keyword=keyword,
        base_dir=PROJECT_ROOT,
        limit=limit_safe,
    )
    return JSONResponse(data)


@app.post("/action/{action}", response_class=HTMLResponse)
async def run_action(action: str, request: Request) -> HTMLResponse:
    form = await request.form()
    platform = normalize_platform(str(form.get("platform") or "qq"))
    form_dict = {k: str(v) for k, v in form.items()}
    context = await _run_in_thread(_execute_action_and_build_context, action, platform, form_dict)
    context["request"] = request
    template = "changereport.html" if action == "report-changes" else "index.html"
    if template == "index.html":
        context.update(await _run_in_thread(_home_metrics_payload))
    return templates.TemplateResponse(template, context)


@app.get("/api/toplist-check-history")
async def api_toplist_check_history(limit: int = 100) -> JSONResponse:
    """榜单数据：从三平台现有库读今日上榜（仅 QQMC_TOPLIST_ARTIST_NAME），网易云已去重。"""
    _ = limit  # 保留查询参数兼容前端，当前实现不按条数截断 runs
    payload = await _run_in_thread(_toplist_check_history_payload)
    response = JSONResponse(payload)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@app.post("/api/toplist-check/run-now")
async def api_toplist_check_run_now() -> JSONResponse:
    """立即执行一次三平台榜单拉取（歌手见 QQMC_TOPLIST_ARTIST_NAME），并返回该歌手今日上榜数据（网易云已去重）。"""
    payload = await _run_in_thread(_toplist_run_now_payload)
    return JSONResponse(payload)


@app.post("/api/crawl-track/run-now")
async def api_crawl_track_run_now() -> JSONResponse:
    """立即执行一次三平台快照抓取。"""
    def _run():
        _sched._run_crawl_track_round()
        return {"ok": True}
    payload = await _run_in_thread(_run)
    return JSONResponse(payload)


@app.get("/api/milestone-logs")
async def api_milestone_logs(limit: int = 500) -> JSONResponse:
    """里程碑日志：三平台收藏量节点，按时间倒序。"""
    data = await _run_in_thread(get_milestone_logs, base_dir=PROJECT_ROOT, limit=limit)
    return JSONResponse(data)


@app.get("/api/debug-paths")
async def api_debug_paths() -> JSONResponse:
    """返回当前解析出的 data 路径，便于排查「找不到数据」问题。"""
    data = await _run_in_thread(_debug_paths_payload)
    return JSONResponse(data)


@app.post("/api/milestone-delete")
async def api_milestone_delete(request: Request) -> JSONResponse:
    """删除单条里程碑记录（按平台、时间、歌曲名、收藏量精确匹配）。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    platform = normalize_platform(str(body.get("platform") or ""))
    if not platform:
        return JSONResponse({"ok": False, "error": "缺少 platform"})
    time_str = str(body.get("time") or "").strip()
    song_name = str(body.get("song_name") or "").strip()
    try:
        favorite_count = int(body.get("favorite_count"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "缺少或无效的 favorite_count"})
    data = await _run_in_thread(
        delete_milestone_entry,
        platform=platform,
        time_str=time_str,
        song_name=song_name,
        favorite_count=favorite_count,
        base_dir=PROJECT_ROOT,
    )
    return JSONResponse(data)


@app.get("/api/report-chart")
async def api_report_chart(
    platform: str = "qq",
    report_mode: str = "",
    report_value: str = "",
    report_artist_mid: str = "",
) -> JSONResponse:
    """获取变化折线图数据：年按月、月按日、日按当天各次 run 聚合。"""
    p = normalize_platform(platform)
    data = await _run_in_thread(
        get_report_chart_data,
        platform=p,
        mode=report_mode or "year",
        value=report_value,
        artist_mid=(report_artist_mid or "").strip(),
        base_dir=PROJECT_ROOT,
    )
    return JSONResponse(data)


@app.get("/song-heat-kugou", response_class=HTMLResponse)
async def song_heat_kugou_page(request: Request, mixsongid: str = "") -> HTMLResponse:
    """酷狗歌曲热度详情页面。"""
    context = _base_context("kugou")
    context["request"] = request
    context["mixsongid"] = (mixsongid or "").strip()
    return templates.TemplateResponse("song_heat_kugou.html", context)


@app.get("/api/song-heat-kugou")
async def api_song_heat_kugou(mixsongid: str = "") -> JSONResponse:
    """获取酷狗歌曲热度数据。"""
    mid = (mixsongid or "").strip()
    if not mid:
        return JSONResponse({"ok": False, "error": "缺少 mixsongid 参数"})
    data = await get_kugou_song_heat(mid)
    return JSONResponse(data)


@app.get("/song-heat", response_class=HTMLResponse)
async def song_heat_page(request: Request, mid: str = "") -> HTMLResponse:
    """歌曲热度详情页面。"""
    context = _base_context("qq")
    context["request"] = request
    context["song_mid"] = (mid or "").strip()
    return templates.TemplateResponse("song_heat.html", context)


@app.get("/api/search-artist")
async def api_search_artist(name: str = "") -> JSONResponse:
    """通过歌手名搜索 artist_mid（QQ音乐 SmartBox 接口）。"""
    keyword = (name or "").strip()
    if not keyword:
        return JSONResponse({"ok": False, "error": "缺少歌手名"})

    def _search():
        with httpx.Client(timeout=10, headers={
            "Referer": "https://y.qq.com/",
            "User-Agent": "Mozilla/5.0",
        }) as client:
            resp = client.get(
                "https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg",
                params={"key": keyword, "format": "json", "inCharset": "utf-8", "outCharset": "utf-8"},
            )
            resp.raise_for_status()
            return resp.json()

    data = await _run_in_thread(_search)
    singers = data.get("data", {}).get("singer", {}).get("itemlist", [])
    if not singers:
        return JSONResponse({"ok": False, "error": f"未找到歌手「{keyword}」"})
    first = singers[0]
    return JSONResponse({
        "ok": True,
        "artist_mid": first.get("mid"),
        "artist_name": first.get("name"),
        "artist_id": first.get("id"),
    })


@app.get("/api/artist-songs-heat")
async def api_artist_songs_heat(
    artist_mid: str = "",
    offset: int = 0,
    limit: int = 10,
) -> JSONResponse:
    """获取歌手的歌曲列表 + 收藏量 + 热度指数 + 在听人数。"""
    mid = (artist_mid or "").strip()
    if not mid:
        return JSONResponse({"ok": False, "error": "缺少 artist_mid"})
    limit = min(max(1, limit), 20)

    def _fetch_songs():
        client = QQMusicClient(base_url="https://u.y.qq.com/cgi-bin/musicu.fcg")
        try:
            page_num = offset // limit + 1
            songs = client.fetch_songs_by_artist(mid, page_num, limit)
            return songs
        finally:
            client.close()

    songs = await _run_in_thread(_fetch_songs)

    # 收藏量和热度数据并发获取
    song_ids = [s.get("id") for s in songs if s.get("id")]
    song_mids = [s.get("mid") for s in songs if s.get("mid")]

    import asyncio as _aio

    async def _get_favs():
        if not song_ids:
            return {}
        def _f():
            c = QQMusicClient(base_url="https://u.y.qq.com/cgi-bin/musicu.fcg")
            try:
                return c.fetch_song_favorite_counts(song_ids)
            finally:
                c.close()
        return await _run_in_thread(_f)

    async def _get_heat():
        if not song_mids:
            return {}
        return await _run_in_thread(fetch_batch_listen_users, song_mids)

    fav_map, heat_listen_map = await _aio.gather(_get_favs(), _get_heat())

    # 组装结果
    result_songs = []
    for i, s in enumerate(songs):
        sid = s.get("id")
        smid = s.get("mid", "")
        fav = fav_map.get(sid)
        hl = heat_listen_map.get(smid, {})
        result_songs.append({
            "index": offset + i + 1,
            "song_name": s.get("name") or s.get("title") or "",
            "song_mid": smid,
            "favorite_count": fav,
            "heat_index": hl.get("score") if isinstance(hl, dict) else None,
            "now_listen_users": hl.get("cnt") if isinstance(hl, dict) else None,
        })

    return JSONResponse({
        "ok": True,
        "songs": result_songs,
        "has_more": len(songs) >= limit,
    })


@app.get("/api/song-heat")
async def api_song_heat(song_mid: str = "", last_days: int = 1) -> JSONResponse:
    """获取歌曲热度数据（纯 API + 加密接口混合）。"""
    mid = (song_mid or "").strip()
    if not mid:
        return JSONResponse({"ok": False, "error": "缺少 song_mid 参数"})
    last_days = max(1, min(last_days, 365))
    data = await get_song_heat(mid, last_days=last_days)
    return JSONResponse(data)


@app.get("/api/search-artist-netease")
async def api_search_artist_netease(name: str = "") -> JSONResponse:
    """通过歌手名搜索网易云歌手 ID。"""
    keyword = (name or "").strip()
    if not keyword:
        return JSONResponse({"ok": False, "error": "缺少歌手名"})

    def _search():
        from .netease_client import NeteaseMusicClient
        c = NeteaseMusicClient()
        try:
            return c.search_artists_by_name(keyword, limit=5)
        finally:
            c.close()

    artists = await _run_in_thread(_search)
    if not artists:
        return JSONResponse({"ok": False, "error": f"未找到歌手「{keyword}」"})
    first = artists[0]
    return JSONResponse({
        "ok": True,
        "artist_id": first.get("singer_mid"),
        "artist_name": first.get("singer_name"),
    })


@app.get("/api/artist-songs-heat-netease")
async def api_artist_songs_heat_netease(
    artist_id: str = "",
    offset: int = 0,
    limit: int = 10,
) -> JSONResponse:
    """获取网易云歌手的歌曲列表 + 收藏量 + 热度。"""
    aid = (artist_id or "").strip()
    if not aid:
        return JSONResponse({"ok": False, "error": "缺少 artist_id"})
    limit = min(max(1, limit), 20)

    def _fetch():
        from .netease_client import NeteaseMusicClient
        from concurrent.futures import ThreadPoolExecutor, as_completed
        c = NeteaseMusicClient(rate_limit_qps=100)
        try:
            page_num = offset // limit + 1
            songs = c.fetch_songs_by_artist(aid, page_num, limit)
            # 并发获取收藏量
            fav_map = {}
            song_ids = [int(s["id"]) for s in songs if s.get("id")]
            if song_ids:
                with ThreadPoolExecutor(max_workers=min(len(song_ids), 10)) as pool:
                    futures = {pool.submit(c._fetch_song_red_count, sid): sid for sid in song_ids}
                    for f in as_completed(futures):
                        sid = futures[f]
                        try:
                            fav_map[sid] = f.result()
                        except Exception:
                            pass
            return songs, fav_map
        finally:
            c.close()

    songs, fav_map = await _run_in_thread(_fetch)

    result_songs = []
    for i, s in enumerate(songs):
        sid = s.get("id")
        fav = fav_map.get(sid) if sid else None
        result_songs.append({
            "index": offset + i + 1,
            "song_name": s.get("name") or "",
            "song_id": sid,
            "favorite_count": fav if fav else None,
        })

    return JSONResponse({
        "ok": True,
        "songs": result_songs,
        "has_more": len(songs) >= limit,
    })


@app.get("/api/search-artist-kugou")
async def api_search_artist_kugou(name: str = "") -> JSONResponse:
    """通过歌手名搜索酷狗歌手 ID。"""
    keyword = (name or "").strip()
    if not keyword:
        return JSONResponse({"ok": False, "error": "缺少歌手名"})

    def _search():
        from .kugou_client import KugouMusicClient
        c = KugouMusicClient()
        try:
            return c.search_artists_by_name(keyword, limit=5)
        finally:
            c.close()

    artists = await _run_in_thread(_search)
    if not artists:
        return JSONResponse({"ok": False, "error": f"未找到歌手「{keyword}」"})
    first = artists[0]
    return JSONResponse({
        "ok": True,
        "singerid": first.get("singer_mid"),
        "singername": first.get("singer_name"),
    })


@app.get("/api/artist-songs-heat-kugou")
async def api_artist_songs_heat_kugou(
    singerid: str = "",
    offset: int = 0,
    limit: int = 10,
) -> JSONResponse:
    """获取酷狗歌手的歌曲列表 + 收藏量 + 热度指数 + 收听人数。"""
    sid = (singerid or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "缺少 singerid"})
    limit = min(max(1, limit), 20)

    def _fetch_songs():
        from .kugou_client import KugouMusicClient
        c = KugouMusicClient()
        try:
            page_num = offset // limit + 1
            return c.fetch_songs_by_artist(sid, page_num, limit)
        finally:
            c.close()

    songs = await _run_in_thread(_fetch_songs)

    # 热度 API 已包含 collect_count，无需单独调收藏量接口
    mixsongids = [str(s.get("mixsongid")) for s in songs if s.get("mixsongid")]
    heat_map = await _run_in_thread(fetch_batch_kugou_heat, mixsongids) if mixsongids else {}

    result_songs = []
    for i, s in enumerate(songs):
        mixid = s.get("mixsongid")
        mixid_str = str(mixid) if mixid is not None else ""
        ht = heat_map.get(mixid_str, {})
        result_songs.append({
            "index": offset + i + 1,
            "song_name": s.get("name") or "",
            "mixsongid": mixid,
            "favorite_count": ht.get("collect_count"),
            "exponent": ht.get("exponent"),
            "listener_num": ht.get("listener_num"),
        })

    return JSONResponse({
        "ok": True,
        "songs": result_songs,
        "has_more": len(songs) >= limit,
    })


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_browser()
    await close_kg_browser()

