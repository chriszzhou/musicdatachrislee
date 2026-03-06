#!/usr/bin/env python3
"""
用你从 Fiddler 复制的 u6 请求原样重放，看服务器返回什么。

你贴的这条 cURL 是：
- URL 带 encoding=ag-1 和 sign=zzc...（sign 在「问号后面的查询参数」里，不是单独 Header）
- Body 是一串加密内容（ag-1 编码），不是明文 JSON
- Cookie 在 Header 里，已按你抓到的填好

运行后会把响应保存到 data/replay_u6_response.bin，并尝试当 JSON 解析；若是二进制则打印前几百字节。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

# 你提供的 cURL 解析结果
URL = (
    "https://u6.y.qq.com/cgi-bin/musics.fcg"
    "?encoding=ag-1"
    "&_webcgikey=GetPlayTopIndexChart"
    "&_=1772421220879"
    "&sign=zzcfe09941ycytkq03jxaqde2ku8l6dy746xk98a1127e"
)

BODY_RAW = (
    "3nXZaJbKJW34ZYSMY0iRYqCWgJhwoDDqUz41kwvmFtol46Iu4OBZauXe+gugr80pFSQw9ANd4bc81foiiyU2H3bkbP7ZtMtzhoW4duOkGH7jJxzv9FtZw0ayBwpgQ0ugkP38Z95Hw3/f51AFHYWvAzeK1B67OF4ZsSr/S244DOEcSM5PC7STqJHfy8Du8Q3gj8bQZxoPNrmAeU+fTb4Vj5sTijvEQ97Uv8BIijpg3rsykWpeohgDzUBDvF1CSpv8UO07efcQRjJtpAFqnicC5FPg7ara/8HTpfiLFzuAJB3xxSfqbYvWOxirm25t1jCgtuJ0oXz68vMrVqlgbmnPP5NhoXZv4zMHXHbhWlxwmm6Lia2cx6chKRfK5AxwbL3xk9k4UmbYKImA8xp4V5lkjNjeVYnw6zYkFZPLQLTsy9IQVv9qNtFv6Q=="
)

HEADERS = {
    "host": "u6.y.qq.com",
    "content-type": "application/x-www-form-urlencoded",
    "accept": "application/octet-stream",
    "sec-fetch-site": "same-site",
    "priority": "u=3, i",
    "accept-language": "zh-CN,zh-Hans;q=0.9",
    "accept-encoding": "gzip, deflate, br",
    "sec-fetch-mode": "cors",
    "origin": "https://y.qq.com",
    "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 26_3 like Mac OS X) AppleWebKit/600.1.4 (KHTML, like Gecko) Mobile/12A365 QQMusic/20.1.5 Mskin/black Mcolor/00cc6cff Bcolor/00000000 skinid[901] NetType/WIFI WebView/WKWebView Released[1] zh-Hans-US DeviceModel/iPhone17,2 skin_css/skin2_1_901 Pixel/1320 FreeFlow/0 teenMode/0 nft_released/[1] ui_mode/1 model130200/1 FontMode/0]  IAP[0]  topBar/62  bottomBar/34  H5/1  TMEPay/1",
    "referer": "https://y.qq.com/",
    "cookie": (
        "pgv_pvid=1679725205; ts_last=y.qq.com/m/client/music_index/index.html; ts_uid=8782721432; "
        "QIMEI36=391277f3ee0928cd97aea541000012a18a05; acctype=qc; ct=1; cv=200105; euin=owvkow-loKCA7v**; "
        "fPersonality=0; fqm_pvqid=1231e770-bf12-4e8a-9992-138141a6c199; fqm_sessionid=be1607bc-c381-4442-b678-53b4cf2f9103; "
        "guid=9B043DC351C240CAA69C2D53862D4C98; isp=7fffffff; login_type=1; "
        "p_lskey=Q_H_L_63k3NuNJfKvLHA67eN_xGtuI3cZq0jXWD2McqgciOp9RTFH0WysP7gZdbsY-M2jnLpeR_jC8UlirPI1_xN5LkiLK0ddfOs0cO7NfgP1OztxucCdGf3WN8dOM1fq12O2naZabSBhwlqRp_tXiDIqMZC2_gIKTncQ; "
        "patch=106; pgv_info=ssid=s4777208158; psrf_access_token_expiresAt=1776670526; "
        "psrf_qqaccess_token=54B01E1C9F9C102884417EA6E1CAF168; psrf_qqopenid=1E340F8CD93A5C0ABACE8EC57BECF487; "
        "qm_keyst=Q_H_L_63k3NuNJfKvLHA67eN_xGtuI3cZq0jXWD2McqgciOp9RTFH0WysP7gZdbsY-M2jnLpeR_jC8UlirPI1_xN5LkiLK0ddfOs0cO7NfgP1OztxucCdGf3WN8dOM1fq12O2naZabSBhwlqRp_tXiDIqMZC2_gIKTncQ; "
        "qqmusic_key=Q_H_L_63k3NuNJfKvLHA67eN_xGtuI3cZq0jXWD2McqgciOp9RTFH0WysP7gZdbsY-M2jnLpeR_jC8UlirPI1_xN5LkiLK0ddfOs0cO7NfgP1OztxucCdGf3WN8dOM1fq12O2naZabSBhwlqRp_tXiDIqMZC2_gIKTncQ; "
        "refresh_key=64aN2VfSeIa2StAJLIDMBzIrPlUOlJaw-Jn4znqsalFkclZT-O_o7KCIkk_SQiIxoO1_YTWU2iSObzV6-llE8nUTqCQuaxnwT6pj9Q_NESMG0VwvNR0SprbPOTY2Jw2PgPUb4eXo_NHUjvnrzmtLqp00-_GHrYqA; "
        "skey=; tmeLoginMethod=2; tmeLoginType=2; uid=5634265652; uin=o2452271625; wxopenid=; wxrefresh_token=; wxuin="
    ),
}


def main():
    out_file = ROOT / "data" / "replay_u6_response.bin"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    print("请求 URL:", URL[:80], "...")
    print("Body 长度:", len(BODY_RAW), "字符（ag-1 加密，非 JSON）")
    print("Cookie 已带", "uin=" in HEADERS["cookie"] and "qm_keyst=" in HEADERS["cookie"])
    print()

    try:
        r = httpx.post(
            URL,
            content=BODY_RAW.encode("utf-8"),
            headers=HEADERS,
            timeout=15,
        )
    except Exception as e:
        print("请求失败:", e)
        sys.exit(1)

    print("状态码:", r.status_code)
    print("Content-Type:", r.headers.get("content-type"))
    body = r.content
    print("响应体长度:", len(body), "字节")
    out_file.write_bytes(body)
    print("已保存到:", out_file)

    # 尝试当 JSON
    try:
        text = body.decode("utf-8")
        data = json.loads(text)
        print("\n响应是 JSON，顶层 keys:", list(data.keys())[:20])
        print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])
    except Exception:
        # 可能是 ag-1 编码的二进制
        print("\n响应不是 UTF-8 JSON，前 200 字节（hex）:", body[:200].hex())
        print("前 100 字节（repr）:", repr(body[:100]))


if __name__ == "__main__":
    main()
