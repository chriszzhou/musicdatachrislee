#!/usr/bin/env python3
"""
探测「更多歌曲数据」来源：基于抓包中的 u6.y.qq.com 与 c.y.qq.com 接口。

- u6.y.qq.com/cgi-bin/musics.fcg：H5 端明文 JSON，协议与 u.y.qq.com musicu.fcg 类似，
  可尝试歌曲详情、歌单等 module/method。
- c.y.qq.com/vipdown/fcgi-bin/fcg_3g_song_list_rover.fcg：抓包中出现的歌单/歌曲列表接口，
  直接 POST 看响应是否含歌曲列表。

用法：
  python scripts/probe_more_song_sources.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

# 与抓包一致的 H5 comm（可改为你的 uin 或保持 0）
COMM_H5 = {
    "g_tk": 2138638467,
    "uin": 2452271625,
    "format": "json",
    "inCharset": "utf-8",
    "outCharset": "utf-8",
    "notice": 0,
    "platform": "h5",
    "needNewCode": 1,
    "ct": 23,
    "cv": 0,
}

HEADERS_H5 = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 26_3 like Mac OS X) AppleWebKit/600.1.4 "
        "(KHTML, like Gecko) Mobile/12A365 QQMusic/20.1.5"
    ),
    "Referer": "https://y.qq.com/",
    "Origin": "https://y.qq.com",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# 抓包里歌曲页的 mid（112555 中 music_index/index.html）
SONG_MID_EXAMPLE = "001Qkqsw0oUIKX"
SONG_ID_EXAMPLE = 517385504  # 大梦归离，与 probe_qq_song_heat 一致


def _deep_keys(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield prefix + k
            yield from _deep_keys(v, prefix + k + ".")
    elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
        for i, item in enumerate(obj[:2]):
            yield from _deep_keys(item, prefix + f"[{i}].")


def probe_u6_song_detail():
    """u6.y.qq.com musics.fcg：尝试歌曲详情（与网页端 musicu 同协议）。"""
    url = "https://u6.y.qq.com/cgi-bin/musics.fcg"
    payload = {
        "comm": COMM_H5,
        "req_0": {
            "module": "music.pf_song_detail_svr",
            "method": "get_song_detail_yqq",
            "param": {"song_mid": SONG_MID_EXAMPLE},
        },
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    try:
        r = httpx.post(
            url,
            content=body,
            headers={**HEADERS_H5, "Content-Type": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  u6 请求失败: {e}")
        return

    for key in ("req_0", "songDetail", "req"):
        node = data.get(key)
        if not isinstance(node, dict):
            continue
        code = node.get("code")
        if code not in (None, 0):
            print(f"  [{key}] code={code}, subcode={node.get('subcode')}")
            # 打印部分响应便于判断（如 2000 可能表示需登录/签名）
            if key == "req_0" and "data" in node:
                print(f"  [{key}] data 片段: {str(node.get('data'))[:300]}")
            continue
        inner = node.get("data", node)
        if isinstance(inner, dict):
            keys = list(_deep_keys(inner))[:40]
            print(f"  [{key}] data 部分 key 示例: {keys[:25]}...")
            ti = inner.get("track_info") or inner.get("trackInfo") or {}
            if ti:
                for k in ["id", "mid", "name", "play_num", "playNum", "heat", "album"]:
                    if k in ti:
                        print(f"    track_info.{k}: {ti[k]}")
            return
        if isinstance(inner, list) and inner:
            print(f"  [{key}] data 为列表，长度={len(inner)}，首项 keys: {list(inner[0].keys())[:15]}")
            return
    print("  u6 响应未识别到歌曲数据，顶层 keys:", list(data.keys()))


def probe_u6_setting():
    """u6：与抓包一致的 Setting 请求，验证 H5 接口可通。"""
    url = "https://u6.y.qq.com/cgi-bin/musics.fcg"
    payload = {
        "comm": COMM_H5,
        "req_0": {
            "module": "music.musicPet.HomeSvr",
            "method": "Setting",
            "param": {"action": 1, "simplify": 0},
        },
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    try:
        r = httpx.post(
            url,
            content=body,
            headers={**HEADERS_H5, "Content-Type": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  u6 Setting 请求失败: {e}")
        return

    for key in ("req_0", "req"):
        node = data.get(key)
        if isinstance(node, dict) and node.get("code") == 0:
            print("  u6 Setting 成功，说明 u6 + JSON body 可通；可在 Fiddler 抓「歌单/歌曲列表」页看 module/method。")
            return
    print("  u6 响应 keys:", list(data.keys()))


def probe_c_yqq_song_list():
    """c.y.qq.com 歌单接口：POST fcg_3g_song_list_rover.fcg。"""
    url = "https://c.y.qq.com/vipdown/fcgi-bin/fcg_3g_song_list_rover.fcg"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 26_3 like Mac OS X) AppleWebKit/600.1.4 (KHTML, like Gecko) Mobile/12A365 QQMusic/20.1.5",
        "Referer": "https://y.qq.com/",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        r = httpx.post(url, content=b"", headers=headers, timeout=15)
        r.raise_for_status()
        text = r.text
    except Exception as e:
        print(f"  c.y.qq.com 请求失败: {e}")
        return

    if text.strip().startswith("{"):
        try:
            data = json.loads(text)
            keys = list(_deep_keys(data))[:30]
            print(f"  响应为 JSON，顶层 keys: {list(data.keys())}")
            print(f"  部分路径: {keys[:20]}")
            for path in [
                ("songlist",),
                ("data", "songlist"),
                ("data", "list"),
                ("list",),
                ("song_list",),
            ]:
                cur = data
                for k in path:
                    cur = cur.get(k) if isinstance(cur, dict) else None
                    if cur is None:
                        break
                if isinstance(cur, list) and cur:
                    print(f"  找到列表 data{path} 长度={len(cur)}，首项 keys: {list(cur[0].keys())[:12]}")
                    return
        except json.JSONDecodeError:
            pass
    if "callback(" in text or "jsonp" in text.lower():
        print("  响应为 JSONP，可截取括号内 JSON 再解析。")
    print("  原始响应前 500 字符:", repr(text[:500]))


def main():
    print("========== 1) u6.y.qq.com musics.fcg 歌曲详情 ==========")
    probe_u6_song_detail()

    print("\n========== 2) u6.y.qq.com 连通性（Setting） ==========")
    probe_u6_setting()

    print("\n========== 3) c.y.qq.com fcg_3g_song_list_rover 歌单 ==========")
    probe_c_yqq_song_list()

    print("\n========== 小结 ==========")
    print("若 u6 歌曲详情返回 track_info，可将 base 改为 u6 复用现有 musicu 逻辑拿更多字段；")
    print("若 c.y.qq.com 返回歌曲列表，可解析后作为歌单/推荐等额外歌曲来源。")
    print("更多 module/method 需在 Fiddler 里打开歌单页/歌曲列表页抓 u6 的 musics.fcg 请求查看。")


if __name__ == "__main__":
    main()
