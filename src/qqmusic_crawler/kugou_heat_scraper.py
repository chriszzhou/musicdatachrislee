"""酷狗音乐热度数据获取（纯 API 方式）。

通过 gateway.kugou.com 直接调用接口，签名为 MD5。
无需 Playwright，响应速度 ~200ms。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

_SIGN_SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
_BASE_URL = "https://gateway.kugou.com/grow/v1/song_ranking"
_DEFAULT_MID = "7bab44fc52cc20ebaef15c632b2f62a1"
_HEADERS = {
    "Referer": "https://activity.kugou.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _compute_signature(params: Dict[str, str]) -> str:
    """酷狗 H5 签名：参数按 key 排序，前后加 salt，拼接后 MD5。"""
    sorted_keys = sorted(params.keys())
    parts = [_SIGN_SALT] + [f"{k}={params[k]}" for k in sorted_keys] + [_SIGN_SALT]
    return hashlib.md5("".join(parts).encode()).hexdigest()


def _build_base_params(mixsongid: str) -> Dict[str, str]:
    return {
        "srcappid": "2919",
        "clientver": "1000",
        "clienttime": str(int(time.time() * 1000)),
        "mid": _DEFAULT_MID,
        "uuid": _DEFAULT_MID,
        "dfid": "-",
        "appid": "1058",
        "album_audio_id": mixsongid,
        "token": "",
    }


def _fetch_song_info(client: httpx.Client, mixsongid: str) -> Optional[Dict[str, Any]]:
    """获取歌曲基本信息（歌名、歌手、封面等）。"""
    params = _build_base_params(mixsongid)
    params["userid"] = "0"
    params["signature"] = _compute_signature(params)
    resp = client.get(f"{_BASE_URL}/unlock/v2/song_info_stat", params=params, headers=_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode") != 0:
        return None
    return data.get("data")


def _fetch_ranking(client: httpx.Client, mixsongid: str) -> Optional[Dict[str, Any]]:
    """获取排行数据（指数、排名、收听人数、历史曲线、榜单记录）。"""
    params = _build_base_params(mixsongid)
    params["userid"] = ""
    params["signature"] = _compute_signature(params)
    resp = client.get(f"{_BASE_URL}/global/v2/ranking", params=params, headers=_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode") != 0:
        return None
    return data.get("data")


async def close_kg_browser() -> None:
    """兼容接口，纯 API 方式无需关闭浏览器。"""
    pass


async def get_kugou_song_heat(mixsongid: str, timeout: int = 15) -> Dict[str, Any]:
    """获取酷狗歌曲热度数据（纯 API，~200ms）。"""
    result: Dict[str, Any] = {"ok": False, "mixsongid": mixsongid}

    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            # 并行概念上两个请求，但 httpx 同步客户端逐个调用也很快
            info = _fetch_song_info(client, mixsongid)
            ranking = _fetch_ranking(client, mixsongid)

        # 歌曲信息
        if info:
            result["song_name"] = info.get("song_name")
            result["album_name"] = info.get("album_name")
            result["cover"] = info.get("cover")
            result["author_names"] = info.get("author_names", [])

        # 排行数据
        if ranking:
            bd = ranking.get("base_data", {})
            if bd:
                result["collect_count"] = bd.get("collect_count")
                result["collect_count_text"] = bd.get("collect_count_text")
                result["listener_num"] = bd.get("listener_num")
                result["listener_num_incr"] = bd.get("listener_num_incr")
                result["rank"] = bd.get("rank")
                result["rank_diff"] = bd.get("rank_diff")
                result["exponent"] = bd.get("exponent")
                result["exponent_diff"] = bd.get("exponent_diff")
                result["comment_num"] = bd.get("comment_num")

            kg = ranking.get("kugou_exponent", {})
            if kg:
                days7 = kg.get("days7", {}).get("lists", [])
                if days7:
                    result["chart"] = {
                        "date_list": [d["date"] for d in days7],
                        "score_list": [d["exponent"] for d in days7],
                    }
                    non_zero = [d for d in days7 if d.get("exponent", 0) > 0]
                    if non_zero:
                        result["kugou_exponent_today"] = non_zero[-1]["exponent"]

                days30 = kg.get("days30", {}).get("lists", [])
                if days30:
                    result["chart_30d"] = {
                        "date_list": [d["date"] for d in days30],
                        "score_list": [d["exponent"] for d in days30],
                    }

                # 半年数据（183天）
                days183 = kg.get("days183", {}).get("lists", [])
                if days183:
                    result["chart_183d"] = {
                        "date_list": [d["date"] for d in days183],
                        "score_list": [d["exponent"] for d in days183],
                    }

            rank_list = ranking.get("global_rank_list")
            if rank_list:
                result["rank_list"] = [
                    {
                        "date": r.get("date"),
                        "title": r.get("title", "").replace("<em>", "").replace("</em>", ""),
                        "platform": r.get("platform"),
                    }
                    for r in rank_list
                ]

        result["ok"] = True

    except Exception as exc:
        logger.error(f"Kugou heat API failed for mixsongid={mixsongid}: {exc}")
        result["error"] = str(exc)

    return result


def _fetch_one_ranking(mixsongid: str, timeout: int = 10) -> Dict[str, Any]:
    """获取单首歌的排行数据（供批量并发用）。"""
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            ranking = _fetch_ranking(client, str(mixsongid))
        if not ranking:
            return {}
        bd = ranking.get("base_data", {})
        return {
            "exponent": bd.get("exponent"),
            "listener_num": bd.get("listener_num"),
            "collect_count": bd.get("collect_count"),
        }
    except Exception:
        return {}


def fetch_batch_kugou_heat(mixsongids: List[str], timeout: int = 10) -> Dict[str, Dict[str, Any]]:
    """批量获取酷狗热度数据（线程池并发）。"""
    if not mixsongids:
        return {}
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(len(mixsongids), 5)) as pool:
        futures = {pool.submit(_fetch_one_ranking, mid, timeout): mid for mid in mixsongids}
        for future in as_completed(futures):
            mid = futures[future]
            try:
                data = future.result()
                if data:
                    results[mid] = data
            except Exception:
                pass
    return results
