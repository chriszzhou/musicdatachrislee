#!/usr/bin/env python3
"""
从 music_index 页面反推「歌曲成就」数据（榜单名、当期排名、历史在榜期数、最高排名、日期）。

不依赖 ag-1 解码：用 Playwright 打开 H5 页面，等「歌曲成就」区块渲染完成后，
从 DOM 文本中用正则解析出每条成就并输出为 JSON。

依赖（可选）：
  pip install playwright
  playwright install chromium

用法：
  python scripts/fetch_song_achievements_by_mid.py 003SqJTl4fnhRC
  python scripts/fetch_song_achievements_by_mid.py "https://y.qq.com/m/client/music_index/index.html?mid=003SqJTl4fnhRC"
  python scripts/fetch_song_achievements_by_mid.py 003SqJTl4fnhRC --out data/my_achievements.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 可选依赖
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

MUSIC_INDEX_URL = "https://y.qq.com/m/client/music_index/index.html"
DATA_DIR = ROOT / "data"


def parse_mid_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
        qs = parse_qs(parsed.query)
        mids = qs.get("mid") or qs.get("songMid") or []
        if mids:
            return mids[0].strip() or None
    except Exception:
        pass
    return None


def parse_achievements_from_html_text(html_or_text: str) -> list[dict]:
    """从包含「歌曲成就」区块的 HTML 或纯文本中解析成就列表。"""
    # 匹配：日期 YYYY/MM/DD（可选） + 榜单名 + 当期排名N + 历史在榜M期 + 最高排名P
    line_re = re.compile(
        r"(?:(?P<date>\d{4}/\d{2}/\d{2})\s*)?"
        r"(?P<chart>.+?)\s+"
        r"当期排名(?P<current>\d+)\s+"
        r"历史在榜(?P<periods>\d+)期\s+"
        r"最高排名(?P<peak>\d+)"
    )
    results = []
    seen = set()
    last_date = ""
    for line in html_or_text.replace(">", ">\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d{4}/\d{2}/\d{2}$", line):
            last_date = line
            continue
        m = line_re.search(line)
        if not m:
            continue
        g = m.groupdict()
        key = (g["chart"], g["current"], g["periods"], g["peak"])
        if key in seen:
            continue
        seen.add(key)
        date = g["date"] or last_date
        results.append({
            "date": date,
            "chart_name": g["chart"].strip(),
            "current_rank": int(g["current"]),
            "history_periods": int(g["periods"]),
            "peak_rank": int(g["peak"]),
        })
    return results


def extract_achievements_js() -> str:
    """返回在页面内执行的 JS：从 DOM 中取出「歌曲成就」相关文本并拼成一大段，供 Python 正则解析。"""
    return r"""
() => {
  const p = document.evaluate("//p[contains(text(),'歌曲成就')]", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
  if (!p) return '';
  const parent = p.closest('article');
  if (!parent) return '';
  return parent.innerText || parent.textContent || '';
}
"""


def fetch_achievements_with_playwright(mid: str, timeout_ms: int = 15000) -> list[dict]:
    """用 Playwright 打开 music_index 页面，等待歌曲成就区块出现并解析。"""
    if sync_playwright is None:
        raise RuntimeError("请先安装: pip install playwright && playwright install chromium")
    url = f"{MUSIC_INDEX_URL}?mid={mid}&type={mid}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            # 等待「歌曲成就」标题出现
            page.wait_for_selector("text=歌曲成就", timeout=timeout_ms)
            text = page.evaluate(extract_achievements_js())
            return parse_achievements_from_html_text(text or "")
        finally:
            browser.close()


def main():
    args = [a for a in sys.argv[1:]]
    out_path = None
    if "--out" in args:
        i = args.index("--out")
        if i + 1 < len(args):
            out_path = Path(args[i + 1])
        args = args[:i] + args[i + 2:]
    raw = " ".join(args).strip()

    if not raw:
        print("用法: python scripts/fetch_song_achievements_by_mid.py <mid 或 music_index 链接> [--out 输出.json]", file=sys.stderr)
        sys.exit(1)

    if raw.startswith("http://") or raw.startswith("https://"):
        mid = parse_mid_from_url(raw)
        if not mid:
            print("无法从链接解析 mid", file=sys.stderr)
            sys.exit(1)
        print("从链接解析 mid:", mid)
    else:
        mid = raw.strip()
        if not re.match(r"^[0-9A-Za-z]+$", mid):
            print("mid 格式异常:", mid[:50], file=sys.stderr)
            sys.exit(1)

    try:
        achievements = fetch_achievements_with_playwright(mid)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        err = str(e)
        if "Executable doesn't exist" in err or "playwright install" in err:
            print("Playwright 未安装浏览器，请执行: playwright install", file=sys.stderr)
        else:
            print("打开页面或解析失败:", e, file=sys.stderr)
        sys.exit(1)

    if not achievements:
        print("未解析到任何歌曲成就（页面可能未加载完或该歌曲无成就）", file=sys.stderr)
        sys.exit(1)

    out = {"mid": mid, "achievements": achievements}
    print("歌曲成就（反推自 music_index 页面）:")
    for a in achievements:
        print("  ", a["date"], a["chart_name"], "当期", a["current_rank"], "历史", a["history_periods"], "期 最高", a["peak_rank"])

    if out_path is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DATA_DIR / f"song_achievements_{mid}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("已保存:", out_path)


if __name__ == "__main__":
    main()
