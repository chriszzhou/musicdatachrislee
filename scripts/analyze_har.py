#!/usr/bin/env python3
"""
解析 Fiddler/浏览器导出的 HAR 文件，列出请求并可按域名过滤。
用于抓包后快速找到目标 API（如 QQ 音乐、酷狗）的 URL 和参数。

用法:
  python scripts/analyze_har.py capture.har
  python scripts/analyze_har.py capture.har --host y.qq.com
  python scripts/analyze_har.py capture.har --host kugou.com
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_har(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("log", {}).get("entries", [])
    if not entries:
        print("HAR 中无 entries，请确认是有效的 HAR 文件。", file=sys.stderr)
    return entries


def get_header(headers: list, name: str) -> str:
    name_lower = name.lower()
    for h in headers or []:
        if (h.get("name") or "").lower() == name_lower:
            return (h.get("value") or "").strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="解析 HAR 文件，列出请求并可按域名过滤")
    parser.add_argument("har_file", type=Path, help="HAR 文件路径")
    parser.add_argument(
        "--host",
        type=str,
        default="",
        help="只显示该 host 的请求，如 y.qq.com 或 kugou.com",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="最多显示条数，默认 500",
    )
    args = parser.parse_args()

    if not args.har_file.is_file():
        print("文件不存在:", args.har_file, file=sys.stderr)
        return 1

    entries = load_har(args.har_file)
    if not entries:
        return 1

    host_filter = (args.host or "").strip().lower()
    if host_filter and not host_filter.startswith("."):
        host_filter_dot = "." + host_filter
    else:
        host_filter_dot = host_filter

    rows = []
    by_host = {}
    for ent in entries:
        req = ent.get("request", {})
        res = ent.get("response", {})
        url = (req.get("url") or "").strip()
        if not url:
            continue
        parsed = urlparse(url)
        host = (parsed.netloc or "").split(":")[0].lower()
        if host_filter:
            if host != host_filter and host_filter_dot and not host.endswith(host_filter_dot):
                continue
        method = (req.get("method") or "GET").strip()
        status = res.get("status") or 0
        ct = get_header(res.get("headers", []), "content-type")
        content = res.get("content", {}) or {}
        size = content.get("size") or 0
        if size == -1:
            size = "-"
        by_host[host] = by_host.get(host, 0) + 1
        rows.append((method, url, status, host, ct, size))

    if not rows:
        print("没有匹配的请求。")
        if host_filter:
            print("尝试去掉 --host 查看全部，或换一个 host 关键词。")
        return 0

    print("共 {} 条请求{}\n".format(len(rows), "（已按 host 过滤）" if host_filter else ""))
    print(f"{'方法':<8} {'状态':<6} {'Host':<28} {'大小':<10} URL")
    print("-" * 100)
    for method, url, status, host, ct, size in rows[: args.limit]:
        host_show = (host[:26] + "..") if len(host) > 28 else host
        size_show = str(size) if size != "-" else "-"
        print(f"{method:<8} {status:<6} {host_show:<28} {size_show:<10} {url[:80]}")
    if len(rows) > args.limit:
        print("... 仅显示前 {} 条，共 {} 条。可用 --limit 调整。".format(args.limit, len(rows)))

    print("\n按 Host 统计（请求数）:")
    for h, count in sorted(by_host.items(), key=lambda x: -x[1]):
        print("  {} : {}".format(h, count))

    return 0


if __name__ == "__main__":
    sys.exit(main())
