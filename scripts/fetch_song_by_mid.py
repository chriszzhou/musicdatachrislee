#!/usr/bin/env python3
"""
用「歌曲详情 H5 分享链接」里的 mid 拉取歌曲数据。

你发现的链接格式：
  https://y.qq.com/m/client/music_index/index.html?mid=03SqJTl4fnhRC&...

页面会通过 JS 用 mid 请求接口；我们直接调 u.y.qq.com 的 musicu.fcg 接口，
传 song_mid 即可拿到同一份歌曲详情（名称、id、专辑、歌手等），无需打开浏览器。

用法：
  python scripts/fetch_song_by_mid.py 03SqJTl4fnhRC
  python scripts/fetch_song_by_mid.py "https://y.qq.com/m/client/music_index/index.html?mid=03SqJTl4fnhRC&..."
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

BASE = "https://u.y.qq.com/cgi-bin/musicu.fcg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:91.0) Gecko/20100101 Firefox/91.0",
    "Referer": "https://y.qq.com/",
    "Origin": "https://y.qq.com",
    "Content-Type": "application/json",
}


def parse_mid_from_url(url: str) -> str | None:
    """从 music_index 分享链接里解析 mid。"""
    try:
        parsed = urlparse(url.strip())
        qs = parse_qs(parsed.query)
        mids = qs.get("mid") or qs.get("songMid") or []
        if mids:
            return mids[0].strip() or None
    except Exception:
        pass
    return None


def fetch_song_detail_by_mid(mid: str) -> dict | None:
    """用 song_mid 调 musicu.fcg 取歌曲详情。"""
    payload = {
        "comm": {"cv": 0, "ct": 24, "format": "json"},
        "songDetail": {
            "module": "music.pf_song_detail_svr",
            "method": "get_song_detail_yqq",
            "param": {"song_mid": mid},
        },
    }
    try:
        r = httpx.post(BASE, json=payload, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print("请求失败:", e, file=sys.stderr)
        return None

    node = data.get("songDetail")
    if not isinstance(node, dict):
        return None
    if node.get("code") not in (None, 0):
        print("接口返回 code=%s" % node.get("code"), file=sys.stderr)
        return None
    return node.get("data")


def fetch_comment_and_favorite(song_id: int) -> tuple[int | None, int | None]:
    """评论数、收藏数（当前接口无「播放量/热度」，仅有这两项可作热度参考）。"""
    comment_cnt, fav_cnt = None, None
    try:
        r = httpx.post(
            BASE,
            json={
                "comm": {"cv": 0, "ct": 24, "format": "json"},
                "songComment": {
                    "module": "GlobalComment.GlobalCommentReadServer",
                    "method": "GetCommentCount",
                    "param": {"request_list": [{"biz_id": str(song_id), "biz_type": 1}]},
                },
            },
            headers=HEADERS,
            timeout=15,
        )
        data = r.json()
        lst = (data.get("songComment") or {}).get("data", {}).get("response_list") or []
        if lst and isinstance(lst[0], dict):
            comment_cnt = int(lst[0].get("count") or 0)
    except Exception:
        pass
    try:
        r = httpx.post(
            BASE,
            json={
                "comm": {"cv": 0, "ct": 24, "format": "json"},
                "result": {
                    "module": "music.musicasset.SongFavRead",
                    "method": "GetSongFansNumberById",
                    "param": {"v_songId": [song_id]},
                },
            },
            headers=HEADERS,
            timeout=15,
        )
        data = r.json()
        m_show = (data.get("result") or {}).get("data", {}).get("m_show") or {}
        if isinstance(m_show, dict) and str(song_id) in m_show:
            raw = m_show[str(song_id)]
            if isinstance(raw, (int, float)):
                fav_cnt = int(raw)
            elif isinstance(raw, str):
                raw = raw.strip().lower().replace("+", "")
                mult = 1
                if raw.endswith("万"):
                    mult, raw = 10_000, raw[:-1]
                elif raw.endswith("亿"):
                    mult, raw = 100_000_000, raw[:-1]
                elif raw.endswith("k"):
                    mult, raw = 1000, raw[:-1]
                try:
                    fav_cnt = int(float(raw) * mult)
                except ValueError:
                    pass
    except Exception:
        pass
    return comment_cnt, fav_cnt


def main():
    raw = " ".join(sys.argv[1:]).strip()
    if not raw:
        print("用法: python scripts/fetch_song_by_mid.py <mid 或 music_index 链接>", file=sys.stderr)
        sys.exit(1)

    # 若是 URL 则解析 mid
    if raw.startswith("http://") or raw.startswith("https://"):
        mid = parse_mid_from_url(raw)
        if not mid:
            print("无法从链接中解析 mid，请直接传入 mid 参数", file=sys.stderr)
            sys.exit(1)
        print("从链接解析 mid:", mid)
    else:
        mid = raw.strip()
        if not re.match(r"^[0-9A-Za-z]+$", mid):
            print("mid 格式异常:", mid[:50], file=sys.stderr)
            sys.exit(1)

    detail = fetch_song_detail_by_mid(mid)
    if not detail:
        sys.exit(1)

    ti = detail.get("track_info") or detail.get("trackInfo") or {}
    name = ti.get("name") or ti.get("title") or ""
    song_id = ti.get("id")
    album = ti.get("album")
    album_name = ""
    if isinstance(album, dict):
        album_name = album.get("name") or ""
    elif isinstance(album, str):
        album_name = album
    singers = ti.get("singer") or []
    singer_names = [s.get("name") for s in singers if isinstance(s, dict) and s.get("name")]

    print("歌曲名:", name)
    print("song_id:", song_id)
    print("mid:", ti.get("mid") or mid)
    print("专辑:", album_name)
    print("歌手:", singer_names)

    # 热度相关：当前接口无「播放量/热度」字段，仅有评论数、收藏数
    if song_id is not None:
        comment_cnt, fav_cnt = fetch_comment_and_favorite(song_id)
        print("评论数:", comment_cnt if comment_cnt is not None else "(未拿到)")
        print("收藏数:", fav_cnt if fav_cnt is not None else "(未拿到)")
        print("(说明: 歌曲详情接口不返回「播放量/热度」，仅能拿到评论数、收藏数作参考；若要播放量需抓手机端热度页请求。)")
        if "--json" in sys.argv:
            detail["_heat_related"] = {"comment_count": comment_cnt, "favorite_count": fav_cnt}

    if "--json" in sys.argv:
        print(json.dumps(detail, ensure_ascii=False, indent=2)[:8000])
    else:
        print("\n(加 --json 可打印完整 data)")


if __name__ == "__main__":
    main()
