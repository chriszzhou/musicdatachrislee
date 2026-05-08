"""QQ 音乐 AG-1 加密/解密 + ZZC 签名。

AG-1 请求加密: AES-GCM (128-bit key, 12-byte nonce)
AG-1 响应解密: XOR with repeating key
ZZC 签名: SHA1 + 索引提取 + scramble
"""

from __future__ import annotations

import re
import secrets
from base64 import b64decode, b64encode
from hashlib import sha1
from itertools import cycle

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ============ AG-1 加解密 ============

_REQUEST_KEY = b"\xbd\x30\x5f\x10\xd0\xff\x74\xb6\xef\x54\xda\xb8\x35\xb5\xe1\xcf"
_RESPONSE_KEY = (
    b"\x7a\x3f\x8c\x1d\x5e\x9b\x2f\x0a\x6c\x4d"
    b"\x7e\x8b\x1f\x3a\x5c\x9d\x0e\x2b\x6f\x4a\x81"
)


def ag1_request_encrypt(data: str) -> str:
    """AES-GCM 加密请求体，返回 base64。"""
    aes_gcm = AESGCM(_REQUEST_KEY)
    nonce = secrets.token_bytes(12)
    ciphertext = aes_gcm.encrypt(nonce, data.encode(), None)
    return b64encode(nonce + ciphertext).decode()


def ag1_response_decrypt(data: bytes) -> str:
    """XOR 解密响应体。"""
    return bytes(a ^ b for a, b in zip(data, cycle(_RESPONSE_KEY))).decode()


# ============ ZZC 签名 ============

_PART_1_INDEXES = [23, 14, 6, 36, 16, 40, 7, 19]
_PART_2_INDEXES = [16, 1, 32, 12, 19, 27, 8, 5]
_SCRAMBLE_VALUES = [
    89, 39, 179, 150, 218, 82, 58, 252, 177,
    52, 186, 123, 120, 64, 242, 133, 143, 161, 121, 179,
]
_PART_1_INDEXES = [i for i in _PART_1_INDEXES if i < 40]


def zzc_sign(payload: str) -> str:
    """生成 ZZC 签名。"""
    hash_hex = sha1(payload.encode("utf-8")).hexdigest().upper()

    part1 = "".join(hash_hex[i] for i in _PART_1_INDEXES)
    part2 = "".join(hash_hex[i] for i in _PART_2_INDEXES)

    part3 = bytearray(20)
    for i, v in enumerate(_SCRAMBLE_VALUES):
        part3[i] = v ^ int(hash_hex[i * 2 : i * 2 + 2], 16)
    b64_part = re.sub(rb"[\\/+=]", b"", b64encode(part3)).decode("utf-8")
    return f"zzc{part1}{b64_part}{part2}".lower()
