#!/usr/bin/env python3
"""
尝试对 u6 encoding=ag-1 的响应做解密/解码（实验性）。

已接入 ag-1 解密（scripts/ag1_cipher.py）：响应体为 XOR 循环 key 解密。
请求体加密与 URL 的 sign 见 scripts/zzc_sign.py、ag1_cipher.ag1_request_encrypt。
"""
from __future__ import annotations

import base64
import gzip
import json
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN_FILE = ROOT / "data" / "replay_u6_response.bin"

# 接入 ag-1 解密
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from ag1_cipher import ag1_response_decrypt
    HAS_AG1 = True
except Exception:
    HAS_AG1 = False


def try_gzip(raw: bytes) -> None:
    """尝试整体或去首字节后 gzip 解压。"""
    for name, data in [("整体", raw), ("去掉第1字节", raw[1:])]:
        try:
            out = gzip.decompress(data)
            if out.isascii() or b"{" in out or b"[" in out:
                print(f"  [gzip] {name} 解压成功，长度={len(out)}")
                print(f"  前 300 字节: {out[:300]}")
                try:
                    j = json.loads(out.decode("utf-8"))
                    print("  可解析为 JSON，keys:", list(j.keys())[:15])
                except Exception:
                    pass
                return
        except Exception:
            pass
    print("  [gzip] 整体/去1字节 均失败")


def try_zlib(raw: bytes) -> None:
    """尝试 zlib / deflate。"""
    for wbits in (15 + 16, -15, 15):
        try:
            out = zlib.decompress(raw, wbits=wbits)
            if len(out) > 0 and (out.isascii() or b"{" in out):
                print(f"  [zlib wbits={wbits}] 解压成功，前200: {out[:200]}")
                return
        except Exception:
            pass
    print("  [zlib] 失败")


def try_xor_single_byte(raw: bytes) -> None:
    """单字节 XOR：若解密后出现大量可打印字符或 JSON 特征则打印。"""
    for key in range(256):
        out = bytes(b ^ key for b in raw)
        if b"{" in out and b"}" in out:
            try:
                # 找第一个 { 到最后一个 } 的区间
                start = out.index(b"{")
                end = out.rindex(b"}") + 1
                j = json.loads(out[start:end].decode("utf-8"))
                print(f"  [XOR key=0x{key:02x}] 疑似 JSON 片段，keys: {list(j.keys())[:10]}")
                return
            except Exception:
                pass
    print("  [XOR 单字节] 未发现明显 JSON")


def try_base64_then_gzip(raw: bytes) -> None:
    """先 base64 解码再 gzip（有些接口会这样）。"""
    try:
        decoded = base64.b64decode(raw, validate=True)
        out = gzip.decompress(decoded)
        if b"{" in out:
            print("  [base64+gzip] 成功，前200:", out[:200])
            return
    except Exception:
        pass
    print("  [base64+gzip] 失败")


def try_ag1_response_decrypt(raw: bytes) -> bool:
    """使用 ag1_cipher 的响应解密（XOR 循环 key）。若成功则打印 JSON 并返回 True。"""
    if not HAS_AG1:
        print("  [ag-1] 未安装 ag1_cipher（需 cryptography），跳过")
        return False
    try:
        text = ag1_response_decrypt(raw)
    except Exception as e:
        print("  [ag-1] 解密异常:", e)
        return False
    if not text.strip().startswith("{"):
        print("  [ag-1] 解密后非 JSON 开头，前 200 字符:", repr(text[:200]))
        return False
    try:
        data = json.loads(text)
        print("  [ag-1] 解密成功，顶层 keys:", list(data.keys())[:20])
        print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
        if len(json.dumps(data)) > 3000:
            print("  ... (已截断)")
        return True
    except json.JSONDecodeError as e:
        print("  [ag-1] 解密后非合法 JSON:", e)
        print("  前 500 字符:", repr(text[:500]))
        return False


def main():
    if not BIN_FILE.exists():
        print("请先运行: python scripts/replay_curl_u6.py")
        print("会生成", BIN_FILE)
        sys.exit(1)

    raw = BIN_FILE.read_bytes()
    print("读取", BIN_FILE, "长度", len(raw), "字节")
    print("首字节:", hex(raw[0]), "前16字节(hex):", raw[:16].hex())
    print()

    # 优先尝试 ag-1 解密（若已接入）
    if HAS_AG1:
        print("========== 尝试 ag-1 响应解密 ==========")
        if try_ag1_response_decrypt(raw):
            print()
            print("(已用 ag-1 解密成功，下面其它尝试可忽略)")
            return
        # 若响应是 Brotli 压缩后再 ag-1，先解压再试
        try:
            import brotli
            decompressed = brotli.decompress(raw)
            print("  [brotli] 解压成功，长度", len(decompressed))
            if try_ag1_response_decrypt(decompressed):
                return
        except ImportError:
            print("  [brotli] 未安装 brotli，跳过")
        except Exception as e:
            print("  [brotli] 解压失败:", e)
        print()

    print("========== 尝试常见解密 ==========")
    try_gzip(raw)
    try_zlib(raw)
    try_xor_single_byte(raw)
    try_base64_then_gzip(raw)

    print()
    print("========== 若都失败：从 H5 里找 ag-1 解码 ==========")
    print("1. 浏览器打开 https://y.qq.com ，F12 → Sources，Ctrl+Shift+F 全局搜 'ag-1' 或 'encoding'。")
    print("2. 或打开 https://y.qq.com/ryqq/js/ 下 vendor.chunk.*.js，搜索 'ag'、'decode'、'decrypt'。")
    print("3. 在 Network 里找到 musics.fcg 的响应，右键该请求 → Copy → Copy as fetch，在 Console 里执行后对 response 下断点，看后续哪段 JS 处理了 response body（即 ag-1 解码处）。")
    print("4. zzc 签名（URL 的 sign）已有人还原：https://jixun.uk/posts/2024/qqmusic-zzc-sign/")
    print("   ag-1 的 body/响应解码目前公开资料较少，需要从上述 JS 里逆向出算法（或直接调用页面里的解码函数）。")
    print()
    print("详细步骤（断点定位、搜索关键词、复现路线）见：scripts/README_ag1_decode_guide.md")


if __name__ == "__main__":
    main()
