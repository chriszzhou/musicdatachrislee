#!/usr/bin/env python3
"""
用 Playwright 打开 music_index 页面，自动拦截 u6 的 musics.fcg 响应，
保存为 .bin 后解密并打印两个接口的返回内容。

用法：
  python scripts/capture_u6_responses.py
  python scripts/capture_u6_responses.py "https://y.qq.com/m/client/music_index/index.html?mid=003SqJTl4fnhRC"

注意：无登录态时 u6 可能只返回 code 2000；且 Playwright 返回的 body 有时会被当作 UTF-8 处理导致
无法解密。若解密失败，可在浏览器 F12 → Network → 该请求 → 右键 Response → Save as… 存为 .bin，
再执行：python scripts/fetch_qq_music_index_heat.py --decrypt-only data/xxx.bin

依赖：pip install playwright && playwright install chromium
       pip install cryptography brotli
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

try:
    from ag1_cipher import ag1_response_decrypt
except ImportError:
    ag1_response_decrypt = None


def _decrypt_u6_response(raw: bytes) -> dict:
    # 浏览器返回的 body 可能是 br 压缩的，先尝试解压再 ag-1 解密
    candidates = [raw]
    try:
        import brotli
        candidates.insert(0, brotli.decompress(raw))
    except Exception:
        pass
    last_err = None
    for data in candidates:
        try:
            text = ag1_response_decrypt(data)
            return json.loads(text)
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("解密失败")


def _webcgikey_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        keys = qs.get("_webcgikey") or []
        return keys[0].strip() if keys else None
    except Exception:
        return None


def capture_and_decrypt(url: str = "https://y.qq.com/m/client/music_index/index.html?mid=003SqJTl4fnhRC"):
    if not sync_playwright:
        print("请安装: pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)
    if not ag1_response_decrypt:
        print("请确保 scripts/ag1_cipher.py 存在且已安装 cryptography", file=sys.stderr)
        sys.exit(1)

    saved = {}  # webcgikey -> (path, raw_bytes)

    def on_response(response):
        req_url = response.request.url
        if "u6.y.qq.com" not in req_url or "musics.fcg" not in req_url:
            return
        key = _webcgikey_from_url(req_url)
        if not key:
            return
        try:
            body = response.body()
            if body:
                saved[key] = (DATA_DIR / f"u6_{key}.bin", body)
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.on("response", on_response)
        page.goto(url, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(2000)
        browser.close()

    if not saved:
        print("未捕获到任何 u6 响应，请检查页面是否正常加载或 URL 是否正确。")
        return

    print("捕获到的 u6 接口：", list(saved.keys()))
    print()

    for webcgikey, (path, raw) in saved.items():
        path.write_bytes(raw)
        print(f"-------- {webcgikey} --------")
        print(f"  原始长度: {len(raw)} 字节，已保存: {path}")
        try:
            data = _decrypt_u6_response(raw)
            code = data.get("code")
            print(f"  解密成功，顶层 keys: {list(data.keys())}")
            if code is not None and code != 0:
                print(f"  接口 code: {code}, msg: {data.get('message', data.get('msg', ''))}")
            out_json = path.with_suffix(".json")
            out_json.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"  已写入: {out_json}")
            if "req_0" in data:
                req0 = data["req_0"]
                if isinstance(req0, dict) and "data" in req0:
                    print("  req_0.data 摘要:")
                    d = req0["data"]
                    if isinstance(d, dict):
                        for k, v in list(d.items())[:5]:
                            if isinstance(v, (list, dict)):
                                print(f"    {k}: type={type(v).__name__}, len={len(v)}")
                            else:
                                print(f"    {k}: {str(v)[:80]}")
            print()
        except Exception as e:
            print(f"  解密失败: {e}")
            print("  → 若为 UTF-8 解码错误，多为浏览器对 body 做了文本替换。可从 F12 将该请求的 Response 另存为 .bin，再执行：")
            print(f"     python scripts/fetch_qq_music_index_heat.py --decrypt-only {path}")
            print()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://y.qq.com/m/client/music_index/index.html?mid=003SqJTl4fnhRC"
    capture_and_decrypt(url)
