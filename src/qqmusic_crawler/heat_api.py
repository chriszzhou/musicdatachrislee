"""QQ 音乐热度数据获取（纯 API 方式）。

通过 u.y.qq.com/cgi-bin/musicu.fcg 直接调用接口获取热度数据。
可用接口:
- HasPlayTopData: 热度指数 score
- GetPlayTopIndexChart: 今日热度曲线
- get_song_detail_yqq: 歌曲基本信息

注: GetPlayTopData（含排名、上榜记录）需要加密，目前无法直接调用。
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx
from loguru import logger

_API_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://y.qq.com/",
    "Origin": "https://y.qq.com",
    "Content-Type": "application/json",
}
_COMM = {"cv": 202201, "ct": 23, "format": "json", "platform": "h5", "needNewCode": 1}


def get_song_heat_api(song_mid: str, timeout: int = 15) -> Dict[str, Any]:
    """通过 API 获取歌曲热度数据（毫秒级响应）。

    返回结构:
    {
        "ok": True,
        "song_mid": "001DIsVq3sSjGV",
        "heat_index": 15592,           # 热度指数
        "listen_cnt": "40w+",          # 收听量文本
        "song_name": "下个，路口，见",
        "singer_name": "李宇春",
        "album_name": "Chris Lee 同名专辑",
        "chart": {"date_list": [...], "score_list": [...]},
    }
    """
    result: Dict[str, Any] = {"ok": False, "song_mid": song_mid}

    payload = {
        "comm": _COMM,
        "req_0": {
            "module": "music.musicToplist.PlayToplist",
            "method": "HasPlayTopData",
            "param": {"songMidList": [song_mid]},
        },
        "req_1": {
            "module": "music.musicToplist.PlayToplist",
            "method": "GetPlayTopIndexChart",
            "param": {"songMidList": [song_mid], "lastDays": 1},
        },
        "req_2": {
            "module": "music.pf_song_detail_svr",
            "method": "get_song_detail_yqq",
            "param": {"song_mid": song_mid, "song_type": 0},
        },
    }

    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            resp = client.post(_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            result["error"] = f"API 返回 code={data.get('code')}"
            return result

        # HasPlayTopData
        req0 = data.get("req_0", {})
        if req0.get("code") == 0:
            song_data = req0.get("data", {}).get("data", {}).get(song_mid, {})
            result["heat_index"] = song_data.get("score")
            result["listen_cnt"] = song_data.get("listenCnt")
            result["has_heat_data"] = song_data.get("result") == 1

        # GetPlayTopIndexChart
        req1 = data.get("req_1", {})
        if req1.get("code") == 0:
            chart_data = req1.get("data", {}).get("data", {}).get(song_mid, {})
            if chart_data.get("dateList") and chart_data.get("scoreList"):
                result["chart"] = {
                    "date_list": chart_data["dateList"],
                    "score_list": chart_data["scoreList"],
                }

        # Song detail
        req2 = data.get("req_2", {})
        if req2.get("code") == 0:
            track = req2.get("data", {}).get("track_info", {})
            if track.get("name"):
                result["song_name"] = track["name"]
                singers = track.get("singer", [])
                result["singer_name"] = singers[0]["name"] if singers else None
                result["album_name"] = track.get("album", {}).get("name")

        result["ok"] = True

    except Exception as exc:
        logger.error(f"Failed to fetch heat API for {song_mid}: {exc}")
        result["error"] = str(exc)

    return result


def get_songs_heat_batch(song_mids: List[str], timeout: int = 15) -> Dict[str, Any]:
    """批量获取多首歌曲的热度指数（仅 score，不含曲线和详情）。"""
    if not song_mids:
        return {"ok": True, "songs": {}}

    payload = {
        "comm": _COMM,
        "req_0": {
            "module": "music.musicToplist.PlayToplist",
            "method": "HasPlayTopData",
            "param": {"songMidList": song_mids},
        },
    }

    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            resp = client.post(_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

        req0 = data.get("req_0", {})
        if req0.get("code") == 0:
            songs = req0.get("data", {}).get("data", {})
            return {"ok": True, "songs": songs}
        return {"ok": False, "error": f"code={req0.get('code')}"}

    except Exception as exc:
        logger.error(f"Batch heat API failed: {exc}")
        return {"ok": False, "error": str(exc)}
