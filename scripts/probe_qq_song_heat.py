#!/usr/bin/env python3
"""探测《大梦归离》歌曲在 QQ 各接口中是否有热度/播放量等数据。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

BASE = "https://u.y.qq.com/cgi-bin/musicu.fcg"
# 大梦归离
SONG_ID = 517385504
SONG_MID = "003SqJTl4fnhRC"

COMM = {"cv": 0, "ct": 24, "format": "json"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Referer": "https://y.qq.com/",
    "Origin": "https://y.qq.com",
    "Content-Type": "application/json",
}


def post(payload: dict) -> dict:
    r = httpx.post(BASE, json=payload, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def deep_keys(obj, prefix=""):
    """递归收集所有 key，便于搜热度相关字段。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield prefix + k
            yield from deep_keys(v, prefix + k + ".")
    elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
        for i, item in enumerate(obj[:2]):
            yield from deep_keys(item, prefix + f"[{i}].")


def main():
    print("歌曲: 大梦归离 | id:", SONG_ID, "| mid:", SONG_MID)
    print()

    # 1) 歌曲详情（id）
    print("========== 1) get_song_detail_yqq (song_id) ==========")
    try:
        r1 = post({
            "comm": COMM,
            "songDetail": {
                "module": "music.pf_song_detail_svr",
                "method": "get_song_detail_yqq",
                "param": {"song_id": SONG_ID},
            },
        })
        d1 = r1.get("songDetail", {}).get("data", {})
        keys = list(deep_keys(d1))
        heat_like = [k for k in keys if any(x in k.lower() for x in ["heat", "play", "listen", "count", "hot", "pop", "热度", "播放", "收听"])]
        print("疑似热度相关 key:", heat_like or "(无)")
        if d1:
            print("顶层 key:", list(d1.keys())[:30])
        # 打印 track_info 里我们关心的
        ti = d1.get("track_info") or d1.get("trackInfo") or {}
        if ti:
            for k in ["id", "mid", "name", "play_num", "playNum", "heat", "hot_score", "listener_count"]:
                if k in ti:
                    print(f"  track_info.{k}:", ti[k])
    except Exception as e:
        print("请求失败:", e)

    # 2) 歌曲详情（mid）- 手机端有时用 mid
    print("\n========== 2) get_song_detail_yqq (song_mid) ==========")
    try:
        r2 = post({
            "comm": COMM,
            "songDetail": {
                "module": "music.pf_song_detail_svr",
                "method": "get_song_detail_yqq",
                "param": {"song_mid": SONG_MID},
            },
        })
        d2 = r2.get("songDetail", {}).get("data", {})
        ti2 = d2.get("track_info") or d2.get("trackInfo") or {}
        if ti2:
            for k in ["id", "mid", "name", "play_num", "playNum", "heat", "hot_score"]:
                if k in ti2:
                    print(f"  track_info.{k}:", ti2[k])
        if not any(ti2.get(k) for k in ["play_num", "playNum", "heat"]):
            print("  未发现播放量/热度字段，顶层 key:", list(d2.keys())[:20])
    except Exception as e:
        print("请求失败:", e)

    # 3) 尝试手机端可能用到的模块名（常见变体）
    print("\n========== 3) 尝试 music.srf.song_detail / song_play_* 等 ==========")
    for module, method in [
        ("music.srf.song_detail", "GetSongDetail"),
        ("music.srf.song_detail_svr", "get_song_detail"),
        ("music.song_detail.SongDetailServer", "GetSongDetail"),
    ]:
        try:
            r = post({
                "comm": COMM,
                "req": {
                    "module": module,
                    "method": method,
                    "param": {"song_id": SONG_ID},
                },
            })
            data = r.get("req", r)
            if isinstance(data, dict) and data.get("code") == 0:
                inner = data.get("data", data)
                keys = list(deep_keys(inner))
                heat = [k for k in keys if any(x in k.lower() for x in ["play", "heat", "count", "hot", "热度", "播放"])]
                if heat:
                    print(f"  {module} 疑似热度 key: {heat}")
        except Exception as e:
            pass

    # 4) c.y.qq.com 移动端老接口（部分博客提到）
    print("\n========== 4) c.y.qq.com 歌曲详情 (fcg) ==========")
    try:
        url = "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg"
        params = {"songmid": SONG_MID, "format": "json", "platform": "yqq"}
        r = httpx.get(url, params=params, headers={**HEADERS, "Referer": "https://y.qq.com/"}, timeout=10)
        if r.status_code == 200:
            j = r.json()
            keys = list(deep_keys(j))
            heat = [k for k in keys if any(x in k.lower() for x in ["play", "heat", "listen", "count", "hot", "热度", "播放"])]
            print("  疑似热度 key:", heat or "(无)")
            if "data" in j:
                print("  data 顶层:", list(j["data"].keys())[:15] if isinstance(j["data"], dict) else type(j["data"]))
    except Exception as e:
        print("  请求失败:", e)

    # 5) 收藏 + 评论（确认这条歌能拿到）
    print("\n========== 5) 收藏数 & 评论数（已有接口） ==========")
    try:
        r = post({
            "comm": COMM,
            "result": {
                "module": "music.musicasset.SongFavRead",
                "method": "GetSongFansNumberById",
                "param": {"v_songId": [SONG_ID]},
            },
        })
        fav = (r.get("result") or {}).get("data") or {}
        print("  收藏 m_show:", fav.get("m_show"), "m_numbers:", fav.get("m_numbers"))
    except Exception as e:
        print("  收藏请求失败:", e)
    try:
        r = post({
            "comm": COMM,
            "songComment": {
                "module": "GlobalComment.GlobalCommentReadServer",
                "method": "GetCommentCount",
                "param": {"request_list": [{"biz_id": str(SONG_ID), "biz_type": 1}]},
            },
        })
        lst = (r.get("songComment") or {}).get("data", {}).get("response_list") or []
        if lst:
            print("  评论数 count:", lst[0].get("count"))
    except Exception as e:
        print("  评论请求失败:", e)

    print("\n========== 小结 ==========")
    print("若上面未出现「播放量/热度」字段，则需用 Burp/Charles 抓手机端「歌曲热度」页的真实请求 URL 与参数后再复现。")


if __name__ == "__main__":
    main()
