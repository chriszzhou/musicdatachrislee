#!/usr/bin/env python3
"""
用 mixsongid 拉酷狗单曲热度（指数、听众、收藏、全站排名、上榜记录）。

用法：
  1) 浏览器打开热度页，F12 -> Network，找到 ranking 请求，复制完整 URL。
  2) python scripts/fetch_kugou_song_heat.py --url "https://gateway.kugou.com/grow/v1/song_ranking/global/v2/ranking?..."

  或只传 mixsongid（需自己实现 signature 后才可用，目前会 403）：
  python scripts/fetch_kugou_song_heat.py --mixsongid 843131884
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.request import Request, urlopen

GATEWAY = "https://gateway.kugou.com"


def fetch_json(url: str) -> dict:
    req = Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def print_heat_summary(data: dict) -> None:
    base = (data.get("data") or {}).get("base_data") or {}
    exponent = base.get("exponent") or base.get("exponent_num")
    listener = base.get("listener_num")
    collect = base.get("collect_count")
    rank = base.get("rank")
    exponent_diff = base.get("exponent_diff")

    print("--- 热度摘要 ---")
    if exponent is not None:
        print(f"  当前指数: {exponent}")
    if exponent_diff is not None:
        print(f"  较昨日: {exponent_diff:+}")
    if listener is not None:
        print(f"  累计听众: {listener}")
    if collect is not None:
        print(f"  收藏量: {collect}")
    if rank is not None:
        print(f"  全站排名: {rank}")

    kugou_exp = (data.get("data") or {}).get("kugou_exponent") or {}
    days7 = kugou_exp.get("days7") or []
    if days7:
        print("  近7天趋势: ", end="")
        print(", ".join(f"{d.get('date','')}:{d.get('exponent','')}" for d in days7[-5:]))

    rank_list = (data.get("data") or {}).get("global_rank_list") or []
    if rank_list:
        print("--- 上榜记录（最近几条）---")
        for r in rank_list[:10]:
            print(f"  {r.get('date')} | {r.get('title', '')} | {r.get('platform', '')}")


def main() -> int:
    ap = argparse.ArgumentParser(description="拉取酷狗单曲热度（需抓包 URL 或后续实现 signature）")
    ap.add_argument("--url", help="抓包得到的 ranking 接口完整 URL")
    ap.add_argument("--mixsongid", type=int, help="歌曲 mixsongid（当前无 sign 会 403）")
    args = ap.parse_args()

    if args.url:
        url = args.url.strip()
        if "gateway.kugou.com" not in url:
            print("请使用 gateway.kugou.com 的 ranking 接口 URL", file=sys.stderr)
            return 1
        try:
            raw = fetch_json(url)
        except Exception as e:
            print(f"请求失败: {e}", file=sys.stderr)
            return 1
        print_heat_summary(raw)
        return 0

    if args.mixsongid:
        # 无 signature 会 403，仅作占位
        url = (
            f"{GATEWAY}/grow/v1/song_ranking/global/v2/ranking"
            f"?srcappid=2919&clientver=1000&album_audio_id={args.mixsongid}"
        )
        try:
            raw = fetch_json(url)
        except Exception as e:
            print(f"请求失败（预期：无 signature 会 403）: {e}", file=sys.stderr)
            return 1
        print_heat_summary(raw)
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
