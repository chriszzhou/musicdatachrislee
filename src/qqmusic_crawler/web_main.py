from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from .web_service import (
    SUPPORTED_PLATFORMS,
    check_artist_toplist,
    crawl_track,
    find_artists,
    get_platform_meta,
    get_report,
    get_report_chart_data,
    get_top_songs,
    normalize_platform,
)

app = FastAPI(title="Music Crawler Web")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))
CRAWL_JOBS: Dict[str, Dict[str, Any]] = {}
CRAWL_JOBS_LOCK = threading.Lock()


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
    }


def _set_job_state(job_id: str, payload: Dict[str, Any]) -> None:
    with CRAWL_JOBS_LOCK:
        old = CRAWL_JOBS.get(job_id, {})
        merged = dict(old)
        merged.update(payload)
        CRAWL_JOBS[job_id] = merged


def _get_job_state(job_id: str) -> Dict[str, Any]:
    with CRAWL_JOBS_LOCK:
        return dict(CRAWL_JOBS.get(job_id, {}))


def _run_crawl_job(job_id: str, platform: str, artist_name: str, song_limit: Optional[int]) -> None:
    def _progress_cb(payload: Dict[str, Any]) -> None:
        _set_job_state(
            job_id,
            {
                "status": "running",
                "progress_pct": int(payload.get("progress_pct") or 0),
                "message": str(payload.get("message") or "处理中"),
            },
        )

    try:
        _set_job_state(
            job_id,
            {"status": "running", "progress_pct": 1, "message": "任务已开始"},
        )
        result = crawl_track(
            platform=platform,
            artist_name=artist_name,
            song_limit=song_limit,
            progress_callback=_progress_cb,
        )
        if result.get("ok"):
            _set_job_state(
                job_id,
                {
                    "status": "done",
                    "progress_pct": 100,
                    "message": "获取歌曲列表完成",
                    "result": result,
                },
            )
        else:
            _set_job_state(
                job_id,
                {
                    "status": "error",
                    "progress_pct": 100,
                    "message": str(result.get("error") or "获取失败"),
                    "error": str(result.get("error") or "获取失败"),
                },
            )
    except Exception as exc:
        _set_job_state(
            job_id,
            {
                "status": "error",
                "progress_pct": 100,
                "message": "操作失败",
                "error": str(exc),
            },
        )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, platform: str = "qq") -> HTMLResponse:
    context = _base_context(platform)
    context["request"] = request
    return templates.TemplateResponse("index.html", context)


@app.post("/action/{action}", response_class=HTMLResponse)
async def run_action(action: str, request: Request) -> HTMLResponse:
    form = await request.form()
    platform = normalize_platform(str(form.get("platform") or "qq"))
    context = _base_context(platform)
    context["request"] = request
    context["form"] = {k: str(v) for k, v in form.items()}

    try:
        if action == "search-artist":
            keyword = str(form.get("artist_keyword") or "").strip()
            data = find_artists(platform=platform, keyword=keyword, max_items=30)
            context["result_type"] = "search-artist"
            context["result"] = data
            context["message"] = "已完成歌手搜索。"
        elif action == "crawl-track":
            artist_name = str(form.get("artist_name") or "").strip()
            song_count_raw = str(form.get("song_count") or "").strip()
            song_limit = None
            if song_count_raw:
                try:
                    parsed = int(song_count_raw)
                    if parsed > 0:
                        song_limit = parsed
                except ValueError:
                    song_limit = None
            data = crawl_track(platform=platform, artist_name=artist_name, song_limit=song_limit)
            context["result_type"] = "crawl-track"
            context["result"] = data
            if data.get("ok"):
                context["message"] = "获取歌曲列表完成。"
            else:
                context["error"] = str(data.get("error") or "获取失败。")
        elif action == "report-changes":
            mode = str(form.get("report_mode") or "").strip()
            value = str(form.get("report_value") or "").strip()
            artist_mid = str(form.get("report_artist_mid") or "").strip()
            data = get_report(
                platform=platform,
                mode=mode,
                value=value,
                artist_mid=artist_mid,
                song_display_limit=15,
            )
            context["result_type"] = "report-changes"
            context["result"] = data
            if data.get("ok"):
                context["message"] = "变化报告生成完成。"
            else:
                context["error"] = str(data.get("error") or "变化报告生成失败。")
        elif action == "check-toplist":
            artist_name = str(form.get("toplist_artist_name") or "").strip()
            top_n_raw = str(form.get("toplist_top_n") or "").strip()
            try:
                top_n = int(top_n_raw) if top_n_raw else 300
            except ValueError:
                top_n = 300
            data = check_artist_toplist(
                platform=platform,
                artist_name=artist_name,
                top_n=top_n,
            )
            context["result_type"] = "check-toplist"
            context["result"] = data
            if data.get("ok"):
                context["message"] = "歌手上榜检查完成。"
            else:
                context["error"] = str(data.get("error") or "歌手上榜检查失败。")
        elif action == "top-songs":
            artist_name = str(form.get("topsongs_artist_name") or "").strip()
            top_n_raw = str(form.get("topsongs_n") or "").strip()
            try:
                top_n = int(top_n_raw) if top_n_raw else 15
            except ValueError:
                top_n = 15
            data = get_top_songs(
                platform=platform,
                artist_name=artist_name,
                top_n=top_n,
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

    return templates.TemplateResponse("index.html", context)


@app.post("/api/crawl-track/start")
async def api_crawl_track_start(request: Request) -> JSONResponse:
    payload = await request.json()
    platform = normalize_platform(str(payload.get("platform") or "qq"))
    artist_name = str(payload.get("artist_name") or "").strip()
    song_limit_raw = payload.get("song_limit")
    song_limit: Optional[int] = None
    if song_limit_raw not in (None, ""):
        try:
            parsed = int(song_limit_raw)
            if parsed > 0:
                song_limit = parsed
        except (TypeError, ValueError):
            song_limit = None

    if not artist_name:
        return JSONResponse({"ok": False, "error": "请输入歌手名。"}, status_code=400)

    job_id = str(uuid.uuid4())
    _set_job_state(
        job_id,
        {
            "status": "queued",
            "progress_pct": 0,
            "message": "任务排队中",
            "platform": platform,
            "artist_name": artist_name,
        },
    )
    thread = threading.Thread(
        target=_run_crawl_job,
        args=(job_id, platform, artist_name, song_limit),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"ok": True, "job_id": job_id})


@app.get("/api/report-chart")
async def api_report_chart(
    platform: str = "qq",
    report_mode: str = "",
    report_value: str = "",
    report_artist_mid: str = "",
) -> JSONResponse:
    """获取变化折线图数据：年按月、月按日、日按当天各次 run 聚合。"""
    p = normalize_platform(platform)
    data = get_report_chart_data(
        platform=p,
        mode=report_mode or "year",
        value=report_value,
        artist_mid=(report_artist_mid or "").strip(),
    )
    return JSONResponse(data)


@app.get("/api/crawl-track/progress/{job_id}")
async def api_crawl_track_progress(job_id: str) -> JSONResponse:
    state = _get_job_state(job_id)
    if not state:
        return JSONResponse({"ok": False, "error": "任务不存在。"}, status_code=404)
    return JSONResponse({"ok": True, **state})
