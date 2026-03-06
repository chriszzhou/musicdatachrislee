#!/usr/bin/env python3
"""
解析 Fiddler 导出的「curl 的 .sh」文件（多行 curl 命令），列出 URL 并按域名过滤。
Fiddler Everywhere 导出时可能没有 HAR，而是 Export as cURL 得到 .sh，用本脚本分析。

用法:
  python scripts/analyze_curl_sh.py 你的导出.sh
  python scripts/analyze_curl_sh.py 你的导出.sh --host y.qq.com
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def extract_curls(content: str) -> List[Tuple[str, str, str]]:
    """
    从 .sh 内容里拆出多条 curl，返回 [(method, url, host), ...]。
    兼容单行 curl 和反斜杠续行。
    """
    # 先合并续行（行尾 \ 接到下一行）
    text = re.sub(r"\\\s*\n\s*", " ", content)
    # 按独立 curl 拆：每段以 curl 开头（可能前面有空白）
    chunks = re.split(r"\s+(?=curl\s+)", text)
    result = []
    for block in chunks:
        block = block.strip()
        if not block.startswith("curl"):
            continue
        # 方法：-X POST / -X GET 等
        method = "GET"
        x_match = re.search(r"-X\s+(\w+)", block, re.I)
        if x_match:
            method = x_match.group(1).upper()
        elif "--data" in block or "--data-raw" in block or "--data-binary" in block:
            method = "POST"
        # URL：第一个引号串（'...' 或 "..."）且像 URL
        url = ""
        for pattern in [r"'([^']+)'", r'"([^"]+)"']:
            for m in re.finditer(pattern, block):
                s = m.group(1).strip()
                if s.startswith("http://") or s.startswith("https://"):
                    url = s
                    break
            if url:
                break
        if not url:
            continue
        parsed = urlparse(url)
        host = (parsed.netloc or "").split(":")[0].lower()
        result.append((method, url, host))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="解析 Fiddler 导出的 curl .sh 文件，列出请求并可按域名过滤"
    )
    parser.add_argument("sh_file", type=Path, help=".sh 文件路径（内含多条 curl）")
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

    if not args.sh_file.is_file():
        print("文件不存在:", args.sh_file, file=sys.stderr)
        return 1

    raw = args.sh_file.read_text(encoding="utf-8", errors="replace")
    rows = extract_curls(raw)
    if not rows:
        print("未在文件中识别到 curl 命令，请确认是 Fiddler 导出的 cURL .sh 文件。", file=sys.stderr)
        return 1

    host_filter = (args.host or "").strip().lower()
    if host_filter:
        if not host_filter.startswith("."):
            host_filter_dot = "." + host_filter
        else:
            host_filter_dot = host_filter
        rows = [
            r
            for r in rows
            if r[2] == host_filter or (host_filter_dot and r[2].endswith(host_filter_dot))
        ]

    if not rows:
        print("没有匹配的请求。")
        if args.host:
            print("尝试去掉 --host 查看全部，或换一个 host 关键词。")
        return 0

    by_host: dict[str, int] = {}
    for _, _, host in rows:
        by_host[host] = by_host.get(host, 0) + 1

    print("共 {} 条请求{}\n".format(len(rows), "（已按 host 过滤）" if host_filter else ""))
    print(f"{'方法':<8} {'Host':<32} URL")
    print("-" * 100)
    for method, url, host in rows[: args.limit]:
        host_show = (host[:30] + "..") if len(host) > 32 else host
        print(f"{method:<8} {host_show:<32} {url[:70]}")
    if len(rows) > args.limit:
        print("... 仅显示前 {} 条，共 {} 条。可用 --limit 调整。".format(args.limit, len(rows)))

    print("\n按 Host 统计（请求数）:")
    for h, count in sorted(by_host.items(), key=lambda x: -x[1]):
        print("  {} : {}".format(h, count))

    return 0


if __name__ == "__main__":
    sys.exit(main())
