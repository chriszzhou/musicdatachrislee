"""解析网易云音乐分享短链与长链，提取路径与查询参数中的业务字段。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, unquote, urlparse

import httpx


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class NeteaseShareInfo:
    """短链解析后的结构化信息（长链同理，可将 follow_redirects=False）。"""

    original_url: str
    final_url: str
    scheme: str
    netloc: str
    path: str
    # 原始 query 键值（字符串）；重复键后者覆盖前者
    query: Dict[str, str] = field(default_factory=dict)
    # 常见分享页字段（解析失败则为 None）
    artist_id: Optional[int] = None
    artist_name: Optional[str] = None
    userid: Optional[int] = None
    app_version: Optional[str] = None
    from_rn: Optional[str] = None
    dlt: Optional[str] = None


def _flatten_query(query_string: str) -> Dict[str, str]:
    if not query_string:
        return {}
    # parse_qsl 保留 + 为空格等；分享链接用标准百分号编码即可
    pairs = parse_qsl(query_string, keep_blank_values=True)
    out: Dict[str, str] = {}
    for k, v in pairs:
        out[k] = v
    return out


def _optional_int(s: Optional[str]) -> Optional[int]:
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_music_163_url(url: str) -> NeteaseShareInfo:
    """
    仅从 URL 字符串解析，不发起网络请求。
    适用于已是 music.163.com 的长链，或任意含 query 的 URL。
    """
    parsed = urlparse(url.strip())
    q = _flatten_query(parsed.query)
    artist_name = q.get("artistName")
    if artist_name is not None:
        artist_name = unquote(artist_name)

    return NeteaseShareInfo(
        original_url=url.strip(),
        final_url=url.strip(),
        scheme=parsed.scheme or "",
        netloc=parsed.netloc or "",
        path=parsed.path or "",
        query=dict(q),
        artist_id=_optional_int(q.get("artistId")),
        artist_name=artist_name,
        userid=_optional_int(q.get("userid")),
        app_version=q.get("app_version"),
        from_rn=q.get("fromRN"),
        dlt=q.get("dlt"),
    )


def resolve_final_url(url: str, timeout: float = 15.0) -> str:
    """跟随重定向，返回最终 URL（GET，与浏览器打开短链行为一致）。"""
    with httpx.Client(
        timeout=timeout,
        headers={"User-Agent": DEFAULT_UA},
        follow_redirects=True,
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return str(resp.url)


def analyze_netease_share_url(
    url: str,
    *,
    follow_redirects: bool = True,
    timeout: float = 15.0,
) -> NeteaseShareInfo:
    """
    解析网易云分享链接：默认识别 163cn.tv 等短链并跟随到长链，再解析 query。

    若已知长链且不想联网，可设 follow_redirects=False，仅对 url 做 parse_music_163_url。
    """
    original = url.strip()
    if follow_redirects:
        final = resolve_final_url(original, timeout=timeout)
    else:
        final = original
    info = parse_music_163_url(final)
    info.original_url = original
    info.final_url = final
    return info


def share_info_to_dict(info: NeteaseShareInfo) -> Dict[str, Any]:
    """便于 JSON 输出或日志。"""
    return {
        "original_url": info.original_url,
        "final_url": info.final_url,
        "scheme": info.scheme,
        "netloc": info.netloc,
        "path": info.path,
        "query": info.query,
        "artist_id": info.artist_id,
        "artist_name": info.artist_name,
        "userid": info.userid,
        "app_version": info.app_version,
        "from_rn": info.from_rn,
        "dlt": info.dlt,
    }


if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser(description="解析网易云短链/长链并输出 JSON")
    p.add_argument("url", help="https://163cn.tv/... 或 music.163.com 长链")
    p.add_argument(
        "--no-fetch",
        action="store_true",
        help="不跟随重定向，仅解析当前 URL 字符串（离线）",
    )
    p.add_argument("--timeout", type=float, default=15.0, help="HTTP 超时秒数")
    args = p.parse_args()
    info = analyze_netease_share_url(
        args.url,
        follow_redirects=not args.no_fetch,
        timeout=args.timeout,
    )
    print(json.dumps(share_info_to_dict(info), ensure_ascii=False, indent=2))
