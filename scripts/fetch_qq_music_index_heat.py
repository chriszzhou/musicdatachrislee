#!/usr/bin/env python3
"""
用 ag-1 解密 + zzc 签名请求 QQ 音乐 u6 热度接口，解密响应并列出可读的热度相关字段。

用法：
  python scripts/fetch_qq_music_index_heat.py <mid>
  python scripts/fetch_qq_music_index_heat.py 003SqJTl4fnhRC --cookie "uin=o2452271625; ..."
  或设置环境变量 QQ_COOKIE 后直接：python scripts/fetch_qq_music_index_heat.py 003SqJTl4fnhRC

测试另一个接口（歌曲成就，需 Cookie）：
  python scripts/fetch_qq_music_index_heat.py 003SqJTl4fnhRC --webcgikey GetPlayTopData_HasPlayTopData --cookie "$QQ_COOKIE"

或只解密本地已抓的响应（不发请求）：
  python scripts/fetch_qq_music_index_heat.py --decrypt-only data/replay_u6_response.bin

查看浏览器里抓到的「请求体」明文（便于对齐参数）：
  python scripts/fetch_qq_music_index_heat.py --decrypt-body "粘贴POST body那串base64"

依赖：pip install cryptography httpx
可选：pip install brotli（若响应为 br 且 httpx 未自动解压）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from ag1_cipher import ag1_request_encrypt, ag1_request_decrypt, ag1_response_decrypt
    from zzc_sign import zzc_sign
except ImportError as e:
    print("请确保 scripts/ag1_cipher.py、zzc_sign.py 存在且已安装 cryptography:", e)
    sys.exit(1)

import httpx


# u6 热度相关接口（music_index 页面会调）
U6_BASE = "https://u6.y.qq.com/cgi-bin/musics.fcg"
WEB_CGI_KEYS = ("GetPlayTopData_HasPlayTopData", "GetPlayTopIndexChart")


def _build_payload(mid: str, last_days: int = 7, webcgikey: str = "") -> dict:
    """构造 u6 请求的明文 JSON。GetPlayTopData_HasPlayTopData 可能要求与 GetPlayTopIndexChart 不同形状，此处先统一用同一结构；若仍 2000 可用 --decrypt-body 看浏览器实际发的参数。"""
    return {
        "songMidList": [mid],
        "lastDays": last_days,
        "comm": {"cv": 202201, "ct": 23},
    }


def _request_u6(
    mid: str,
    webcgikey: str = "GetPlayTopIndexChart",
    cookie: str = "",
    last_days: int = 7,
) -> bytes:
    """构造 ag-1 加密 body + zzc sign，POST 到 u6，返回响应 body 原始字节。"""
    payload = _build_payload(mid, last_days, webcgikey)
    body_plain = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    body_enc = ag1_request_encrypt(body_plain)
    sign = zzc_sign(body_enc)
    url = f"{U6_BASE}?encoding=ag-1&_webcgikey={webcgikey}&_=0&sign={sign}"
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "accept": "application/octet-stream",
        "origin": "https://y.qq.com",
        "referer": "https://y.qq.com/",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    if cookie:
        headers["cookie"] = cookie.strip()
    r = httpx.post(url, content=body_enc.encode("utf-8"), headers=headers, timeout=15)
    r.raise_for_status()
    return r.content


def _decrypt_response(raw: bytes) -> dict:
    """Brotli 解压（若需要）+ ag-1 解密，返回解析后的 JSON。"""
    data = raw
    try:
        import brotli
        data = brotli.decompress(raw)
    except ImportError:
        pass
    except Exception:
        pass
    text = ag1_response_decrypt(data)
    return json.loads(text)


def _extract_heat_info(obj, prefix: str = "") -> list:
    """递归收集疑似热度/榜单的字段（key 含 rank/top/play/index/period 等）。"""
    out = []
    heat_keywords = ("rank", "top", "play", "index", "period", "chart", "list", "data", "achievement", "trend")
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if any(w in k.lower() for w in heat_keywords):
                if isinstance(v, (dict, list)):
                    out.extend(_extract_heat_info(v, path))
                else:
                    out.append((path, str(v)[:200]))
            else:
                out.extend(_extract_heat_info(v, path))
    elif isinstance(obj, list) and obj:
        for i, item in enumerate(obj[:5]):
            out.extend(_extract_heat_info(item, f"{prefix}[{i}]"))
    return out


def main():
    ap = argparse.ArgumentParser(description="请求/解密 QQ 音乐 u6 热度接口并列出热度字段")
    ap.add_argument("mid", nargs="?", default="", help="歌曲 mid，例如 003SqJTl4fnhRC")
    ap.add_argument("--cookie", default="", help="可选 Cookie（含 uin/qm_keyst 等以通过校验）")
    ap.add_argument("--decrypt-only", metavar="FILE", help="仅解密本地 bin 文件，不发请求")
    ap.add_argument("--decrypt-body", metavar="BASE64", help="解密浏览器抓到的 POST body（base64），查看明文参数")
    ap.add_argument("--webcgikey", default="GetPlayTopIndexChart", choices=list(WEB_CGI_KEYS))
    ap.add_argument("--last-days", type=int, default=7, help="lastDays 参数")
    ap.add_argument("--out", metavar="JSON", help="解密结果写入 JSON 文件")
    args = ap.parse_args()
    # 支持从环境变量读取 Cookie，便于带登录态测 GetPlayTopData_HasPlayTopData
    import os
    if not args.cookie and os.environ.get("QQ_COOKIE"):
        args.cookie = os.environ.get("QQ_COOKIE", "")

    if args.decrypt_body:
        try:
            plain = ag1_request_decrypt(args.decrypt_body.strip())
            print("========== 请求体明文 ===========")
            try:
                obj = json.loads(plain)
                print(json.dumps(obj, indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print(plain)
        except Exception as e:
            print("解密 body 失败:", e)
            sys.exit(1)
        return

    if args.decrypt_only:
        path = Path(args.decrypt_only)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            print("文件不存在:", path)
            sys.exit(1)
        raw = path.read_bytes()
        try:
            data = _decrypt_response(raw)
        except Exception as e:
            print("解密失败:", e)
            sys.exit(1)
        print("========== 解密结果（顶层 keys）===========")
        print(json.dumps(list(data.keys()), indent=2, ensure_ascii=False))
        print()
        heat_fields = _extract_heat_info(data)
        if heat_fields:
            print("========== 疑似热度/榜单相关字段 ===========")
            for path, val in heat_fields[:80]:
                print(f"  {path}: {val}")
        else:
            print("========== 完整 JSON（前 4000 字符）===========")
            print(json.dumps(data, indent=2, ensure_ascii=False)[:4000])
        if args.out:
            out_path = Path(args.out)
            if not out_path.is_absolute():
                out_path = ROOT / out_path
            out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            print()
            print("已写入:", out_path)
        return

    mid = (args.mid or "").strip()
    if not mid:
        ap.print_help()
        print("\n示例: python scripts/fetch_qq_music_index_heat.py 003SqJTl4fnhRC")
        sys.exit(1)

    print("请求 u6 接口:", args.webcgikey, "mid=", mid)
    try:
        raw = _request_u6(mid, webcgikey=args.webcgikey, cookie=args.cookie, last_days=args.last_days)
    except Exception as e:
        print("请求失败:", e)
        sys.exit(1)
    print("响应长度:", len(raw), "字节")

    try:
        data = _decrypt_response(raw)
    except Exception as e:
        print("解密失败（可能需登录 Cookie 或接口返回非 ag-1）:", e)
        if args.out:
            Path(args.out).write_bytes(raw)
            print("原始 body 已写入:", args.out)
        sys.exit(1)

    print()
    print("========== 解密成功，顶层 keys ===========")
    print(json.dumps(list(data.keys()), indent=2, ensure_ascii=False))
    code = data.get("code")
    if code is not None and code != 0:
        print("接口 code:", code, "msg:", data.get("message", data.get("msg", "")))
    heat_fields = _extract_heat_info(data)
    if heat_fields:
        print()
        print("========== 疑似热度/榜单相关字段 ===========")
        for path, val in heat_fields[:80]:
            print(f"  {path}: {val}")
    if "resDataList" in data:
        print()
        print("========== resDataList 结构 ===========")
        lst = data["resDataList"]
        print("长度:", len(lst) if isinstance(lst, list) else "N/A")
        if isinstance(lst, list) and lst:
            print("首项 keys:", list(lst[0].keys()) if isinstance(lst[0], dict) else type(lst[0]))
            print(json.dumps(lst[0], indent=2, ensure_ascii=False)[:1500])
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print()
        print("已写入:", out_path)


if __name__ == "__main__":
    main()
