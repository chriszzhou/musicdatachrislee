"""QQ 音乐热度数据获取 — 纯 API 方式。

- 基础数据（热度指数、曲线）通过 u.y.qq.com 明文 API
- 加密数据（排名、在听人数、上榜记录）通过 u6.y.qq.com + AG-1 加密
- 全部为 HTTP 调用，无需 Playwright，响应 ~200ms
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

import httpx
from loguru import logger

from .qq_crypto import ag1_request_encrypt, ag1_response_decrypt, zzc_sign

# ============ 公共配置 ============

_API_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_API_URL_AG1 = "https://u6.y.qq.com/cgi-bin/musics.fcg"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://y.qq.com/",
    "Origin": "https://y.qq.com",
    "Content-Type": "application/json",
}
_HEADERS_AG1 = {
    "Accept": "application/octet-stream",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://y.qq.com/",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}
_COMM = {"cv": 202201, "ct": 23, "format": "json", "platform": "h5", "needNewCode": 1}
_COMM_FULL = {
    "g_tk": 5381,
    "uin": "",
    "format": "json",
    "inCharset": "utf-8",
    "outCharset": "utf-8",
    "notice": 0,
    "platform": "h5",
    "needNewCode": 1,
    "ct": 23,
    "cv": 202201,
    "mesh_devops": "",
}


# ============ 明文 API ============


def _fetch_basic_heat(song_mid: str, last_days: int = 1, timeout: int = 15) -> Dict[str, Any]:
    """明文 API: HasPlayTopData + GetPlayTopIndexChart + 歌曲详情。"""
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
            "param": {"songMidList": [song_mid], "lastDays": last_days},
        },
        "req_2": {
            "module": "music.pf_song_detail_svr",
            "method": "get_song_detail_yqq",
            "param": {"song_mid": song_mid, "song_type": 0},
        },
    }

    with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
        resp = client.post(_API_URL, json=payload)
        resp.raise_for_status()
        return resp.json()


# ============ AG-1 加密 API ============


def _fetch_encrypted_heat(song_mid: str, timeout: int = 15) -> Dict[str, Any]:
    """AG-1 加密 API: GetPlayTopData（排名、在听人数、上榜记录）。"""
    payload_str = json.dumps(
        {
            "comm": _COMM_FULL,
            "req_0": {
                "module": "music.musicToplist.PlayToplist",
                "method": "GetPlayTopData",
                "param": {"songMidList": [song_mid], "requireSongInfo": 1},
            },
        },
        separators=(",", ":"),
    )

    sign = zzc_sign(payload_str)
    encrypted_body = ag1_request_encrypt(payload_str)
    ts = int(time.time() * 1000)
    url = (
        f"{_API_URL_AG1}?encoding=ag-1"
        f"&_webcgikey=GetPlayTopData&_={ts}&sign={sign}"
    )

    with httpx.Client(timeout=timeout, headers=_HEADERS_AG1) as client:
        resp = client.post(url, content=encrypted_body)
        resp.raise_for_status()

    decrypted = ag1_response_decrypt(resp.content)
    data = json.loads(decrypted)

    if data.get("code") != 0:
        return {}
    req0 = data.get("req_0", {})
    if req0.get("code") != 0:
        return {}
    return req0.get("data", {}).get("data", {}).get(song_mid, {})


def _fetch_one_encrypted(mid: str, timeout: int = 15) -> Dict[str, Any]:
    """AG-1 加密 API: 获取单首歌的 GetPlayTopData。"""
    payload_str = json.dumps(
        {
            "comm": _COMM_FULL,
            "req_0": {
                "module": "music.musicToplist.PlayToplist",
                "method": "GetPlayTopData",
                "param": {"songMidList": [mid], "requireSongInfo": 0},
            },
        },
        separators=(",", ":"),
    )
    sign = zzc_sign(payload_str)
    encrypted_body = ag1_request_encrypt(payload_str)
    ts = int(time.time() * 1000)
    url = (
        f"{_API_URL_AG1}?encoding=ag-1"
        f"&_webcgikey=GetPlayTopData&_={ts}&sign={sign}"
    )
    with httpx.Client(timeout=timeout, headers=_HEADERS_AG1) as client:
        resp = client.post(url, content=encrypted_body)
        resp.raise_for_status()
    decrypted = ag1_response_decrypt(resp.content)
    data = json.loads(decrypted)
    if data.get("code") == 0:
        req0 = data.get("req_0", {})
        if req0.get("code") == 0:
            song_data = req0.get("data", {}).get("data", {}).get(mid, {})
            return {
                "score": song_data.get("score"),
                "cnt": (song_data.get("nowListenUsers") or {}).get("cnt"),
            }
    return {}


def _fetch_batch_encrypted(song_mids: List[str], timeout: int = 15) -> Dict[str, Dict[str, Any]]:
    """AG-1 加密 API: 并发获取多首歌的 GetPlayTopData。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}
    with ThreadPoolExecutor(max_workers=min(len(song_mids), 10)) as pool:
        futures = {pool.submit(_fetch_one_encrypted, mid, timeout): mid for mid in song_mids}
        for future in as_completed(futures):
            mid = futures[future]
            try:
                data = future.result()
                if data:
                    results[mid] = data
            except Exception as exc:
                logger.debug(f"Batch encrypted fetch failed for {mid}: {exc}")
    return results


# ============ 兼容接口 ============


async def close_browser() -> None:
    """兼容接口，纯 API 方式无需关闭浏览器。"""
    pass


def fetch_batch_listen_users(song_mids: List[str]) -> Dict[str, Any]:
    """批量获取热度指数 + 在听人数（AG-1 加密，线程池并发）。"""
    if not song_mids:
        return {}
    try:
        return _fetch_batch_encrypted(song_mids)
    except Exception as exc:
        logger.warning(f"Batch listen users fetch failed: {exc}")
        return {}


# ============ 统一接口 ============


async def get_song_heat(song_mid: str, timeout: int = 15, last_days: int = 1) -> Dict[str, Any]:
    """获取歌曲完整热度数据，合并明文 API 和 AG-1 加密数据。"""
    result: Dict[str, Any] = {"ok": False, "song_mid": song_mid}

    try:
        # 1. 明文 API
        api_data = _fetch_basic_heat(song_mid, last_days=last_days, timeout=timeout)

        if api_data.get("code") != 0:
            result["error"] = f"API code={api_data.get('code')}"
            return result

        # HasPlayTopData
        req0 = api_data.get("req_0", {})
        if req0.get("code") == 0:
            song_data = req0.get("data", {}).get("data", {}).get(song_mid, {})
            result["heat_index"] = song_data.get("score")
            result["has_heat_data"] = song_data.get("result") == 1

        # Chart
        req1 = api_data.get("req_1", {})
        if req1.get("code") == 0:
            chart_data = req1.get("data", {}).get("data", {}).get(song_mid, {})
            if chart_data.get("dateList") and chart_data.get("scoreList"):
                result["chart"] = {
                    "date_list": chart_data["dateList"],
                    "score_list": chart_data["scoreList"],
                }

        # Song detail
        req2 = api_data.get("req_2", {})
        if req2.get("code") == 0:
            track = req2.get("data", {}).get("track_info", {})
            if track.get("name"):
                result["song_name"] = track["name"]
                singers = track.get("singer", [])
                result["singer_name"] = singers[0]["name"] if singers else None
                result["album_name"] = track.get("album", {}).get("name")

        # 2. AG-1 加密接口
        try:
            enc_data = _fetch_encrypted_heat(song_mid, timeout=timeout)
        except Exception as exc:
            logger.warning(f"Encrypted heat fetch failed for {song_mid}: {exc}")
            enc_data = {}

        if enc_data:
            rt = enc_data.get("realtimeData", {})
            if rt:
                result["rank"] = rt.get("todayRank")
                result["yesterday_index"] = rt.get("yesterdayIndex")
                result["yesterday_rank"] = rt.get("yesterdayRank")
                result["index_rate"] = rt.get("indexRate")
                result["rank_diff"] = rt.get("rankDiff")

            nlu = enc_data.get("nowListenUsers", {})
            result["now_listen_users"] = nlu.get("cnt")

            record = enc_data.get("record", {})
            result["record"] = record.get("data", []) if isinstance(record, dict) else []
            result["record_detail"] = record.get("newData", []) if isinstance(record, dict) else []

        result["ok"] = True

    except Exception as exc:
        logger.error(f"get_song_heat failed for {song_mid}: {exc}")
        result["error"] = str(exc)

    return result
