#!/usr/bin/env python3
"""
尝试 u6 musics.fcg 的不同 encoding 参数，看返回差异。

结论（当前测试）：
- encoding=ag-1 + 正确加密 body → 200，返回 ag-1 二进制（榜单/热度数据）
- encoding=json / utf-8 / 0 / 无 / raw / plain / text + 空 body → 200，返回 JSON：{"code":500001,...}（疑似「请求无效」）
- encoding=json + 明文 JSON body → 200，返回 {"code":2000,...}（无数据，2000 可能表示需登录或 sign/body 不匹配）

用法：
  python scripts/try_u6_encoding.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

BASE = "https://u6.y.qq.com/cgi-bin/musics.fcg"
QUERY = "_webcgikey=GetPlayTopData_HasPlayTopData&_=1772432794960&sign=zzc9e830d3vrdogwu1nxe9d73yvbjcojvdrsw04ba38db"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Referer": "https://y.qq.com/",
    "Origin": "https://y.qq.com",
    "Content-Type": "application/x-www-form-urlencoded",
}


def main():
    encodings = ["ag-1", "json", "utf-8", "0", "raw", "plain", "text"]
    print("========== 空 body ==========")
    for enc in encodings:
        url = f"{BASE}?encoding={enc}&{QUERY}"
        try:
            r = httpx.post(url, content=b"", headers=HEADERS, timeout=10)
            text = r.content.decode("utf-8", errors="replace")
            if text.strip().startswith("{"):
                d = json.loads(text)
                print("  encoding=%s -> code=%s" % (enc, d.get("code")))
            else:
                print("  encoding=%s -> binary len=%s" % (enc, len(r.content)))
        except Exception as e:
            print("  encoding=%s -> %s" % (enc, e))

    print("\n========== encoding=json + JSON body ==========")
    url_json = f"{BASE}?encoding=json&{QUERY}"
    body = {"songMidList": ["003SqJTl4fnhRC"], "lastDays": 7}
    headers_json = {**HEADERS, "Content-Type": "application/json"}
    try:
        r = httpx.post(url_json, json=body, headers=headers_json, timeout=10)
        print("  status=%s body=%s" % (r.status_code, r.text[:200]))
    except Exception as e:
        print("  error:", e)

    print("\n========== 小结 ==========")
    print("仅 encoding=ag-1 时返回二进制数据；其他 encoding 返回 JSON 且 code=500001 或 2000，无榜单内容。")
    print("若要在 encoding=json 下拿数据，可能需服务端支持或正确 sign/cookie。")


if __name__ == "__main__":
    main()
