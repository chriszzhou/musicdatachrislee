# music_index 页面打开后调用的接口分析

针对链接：  
`https://y.qq.com/m/client/music_index/index.html?ADTAG=cbshare&channelId=10036163&mid=003SqJTl4fnhRC&...`

通过分析页面入口 JS（`https://y.qq.com/m/client/music_index/index.4c4e63969.js`）得到以下结论。

---

## 1. 页面如何拿到 mid

- 从 URL 读取：`e = getParam("mid") || "004AxmlM3YihD2"`，并赋给 `window.songMid`。
- 分享链接里的 `mid`、`type` 会用来拼链接和请求参数。

---

## 2. 打开后实际请求的接口（都是 u6 + ag-1）

**所有数据请求都发往同一域名，且请求体/响应体为 ag-1 编码（非明文 JSON）：**

- **URL**：`//u6.y.qq.com/cgi-bin/musics.fcg?encoding=ag-1`
- **方法**：POST，body 为 ag-1 编码，无法直接看到明文参数/响应。

---

## 3. 两批「批量请求」大致内容

### 批次 A（带 lastDays，疑似榜单/趋势）

- **参数大致形状**：`{ songMidList: [mid], lastDays: Number(n) }`（mid 来自 `getParam("mid")` 或默认 `"004AxmlM3YihD2"`）。
- **用途**：与「最近 N 天」相关，JS 里还有 `GetPlayTopData`、`HasPlayTopData`、`GetPlayTopIndexChart`、`music.musicToplist.PlayToplist` 等，推断为榜单/播放趋势数据，**热度/播放量若存在，多半在这一批的响应里**。
- **响应**：ag-1 二进制，需解码才能看到是否有热度值。

### 批次 B（歌曲信息 + songInfo）

- **参数大致形状**（一次发 2 个子请求）：
  1. `{ songMidList: [window.songMid], requireSongInfo: 1 }`
  2. `{ songMidList: [window.songMid] }`
- **公共 comm**：`{ cv: 202201, ct: 23, mesh_devops: "..." }`。
- **响应使用方式**（解密后）：
  - 第一个子请求：`resDataList[0].data[window.songMid]`
  - 第二个子请求：`resDataList[1].data.songInfo[window.songMid]`（含 `name`、`singer` 等，用于页面展示）。

即：**同一页面既用 u6+ag-1 拿「歌曲详情/ songInfo」，也用 u6+ag-1 拿「榜单/趋势（lastDays）」**；我们当前用 `u.y.qq.com musicu.fcg` 的 `get_song_detail_yqq` 拿到的详情，与批次 B 在业务上等价，但**热度/播放量不在该接口**，而在批次 A 对应的 ag-1 响应里。

---

## 4. 小结表

| 批次 | 大致参数 | 用途 | 是否含热度/播放量 |
|------|-----------|------|-------------------|
| A    | songMidList + lastDays | 榜单/趋势（GetPlayTopData、GetPlayTopIndexChart 等） | **可能有**（需解 ag-1 确认） |
| B    | songMidList + requireSongInfo；再一条 songMidList | 歌曲详情、songInfo（名称、歌手等） | 无，仅基础信息 |

---

## 5. 如何自己再确认

1. **Fiddler / 开发者工具**  
   用电脑或手机打开上述 music_index 链接，在 Network 里筛选 `u6.y.qq.com`、`musics.fcg`，看：
   - 哪条请求的 query 或 body 里带 `lastDays` / `GetPlayTopData` / `GetPlayTopIndexChart`（对应批次 A）；
   - 哪条带 `requireSongInfo` 或两条连发的 songMidList（对应批次 B）。  
   响应体都是 ag-1，需解密才能看到具体字段。

2. **解密 ag-1**  
   项目已接入 **ag-1 解密**（`scripts/ag1_cipher.py`）与 **zzc 签名**（`scripts/zzc_sign.py`）。对 u6 响应运行 `python scripts/try_decrypt_ag1_response.py`（需先 `replay_curl_u6.py` 得到 `data/replay_u6_response.bin`）即可得到明文 JSON。详见本文档 **第 10 节**。

3. **当前项目里的替代**  
   - 歌曲详情（含名称、歌手、专辑等）：已用 `u.y.qq.com` 的 `get_song_detail_yqq` + mid 实现，见 `scripts/fetch_song_by_mid.py`。  
   - 热度：该接口**没有**播放量/热度；仅有**评论数、收藏数**（同样通过 musicu.fcg 的其它接口）可作参考，见 `fetch_song_by_mid.py` 的「评论数、收藏数」输出。

---

## 6. 入口 JS 引用

- 页面入口脚本：`https://y.qq.com/m/client/music_index/index.4c4e63969.js`  
- 其中出现的接口：`//u6.y.qq.com/cgi-bin/musics.fcg?encoding=ag-1`（所有 musics.fcg 请求都走这里，ag-1 编码）。

---

## 7. 请求启动器链（谁触发了 u6 请求）

打开 music_index 页面时，请求的发起顺序大致如下（F12 → Network → 看 Initiator 可复现）：

1. **启动页**  
   `https://y.qq.com/m/client/music_index/index.html?ADTAG=cbshare&channelId=10036163&hosteuin=owvkow-loKCA7v%2A%2A&mid=003SqJTl4fnhRC&openinqqmusic=0&type=003SqJTl4fnhRC`

2. **页面加载的 React 运行时**  
   `https://y.qq.com/lib/commercial/h5/music-react-2.3.0.min.js?max_age=604800&app=qqmusic&version=20250616&md5=13da3f2162`

3. **由页面逻辑触发的 u6 接口（批次 A 之一）**  
   `https://u6.y.qq.com/cgi-bin/musics.fcg?encoding=ag-1&_webcgikey=GetPlayTopData_HasPlayTopData&_=1772432794960&sign=zzc9e830d3vrdogwu1nxe9d73yvbjcojvdrsw04ba38db`

即：**HTML → music-react-2.3.0.min.js → u6 musics.fcg（GetPlayTopData_HasPlayTopData）**。  
同一页面可能还会再请求 **GetPlayTopIndexChart**（你之前抓的那条），两者都是批次 A 的榜单/趋势类接口，区别在 `_webcgikey`。要找 ag-1 编解码，可优先在 **music_index 的入口 JS**（如 `index.4c4e63969.js`）或 **music-react** 依赖里搜 `encoding`、`ag-1`、`decode`。

---

## 7.1 尝试其他 encoding 参数

把 URL 里的 `encoding=ag-1` 改成别的（如 `encoding=json`、`encoding=utf-8`、`encoding=0` 或去掉该参数），同一接口的返回会变化：

| encoding     | 请求体   | 返回 |
|-------------|----------|------|
| **ag-1**    | 加密 body | 200，**二进制**（ag-1 编码的榜单数据） |
| **json** / utf-8 / 0 / raw / plain / text | 空 body | 200，**JSON** `{"code":500001,...}`（请求无效） |
| **json**    | 明文 JSON body（如 `{"songMidList":["003SqJTl4fnhRC"],"lastDays":7}`） | 200，**JSON** `{"code":2000,...}`（无数据，可能需登录或 sign） |

结论：**只有 encoding=ag-1 且带上正确加密 body 时，接口才返回榜单/热度二进制**；其他 encoding 要么 500001 要么 2000，拿不到数据。脚本 `scripts/try_u6_encoding.py` 可本地复现上述请求。

---

## 7.2 「歌曲成就」数据反推

你在页面上看到的**歌曲成就**（例如：飙升榜 当期排名75 历史在榜16期 最高排名3；听歌识曲榜 当期排名58…）就是**榜单/趋势类接口返回并解密后的数据**，对应我们前面说的**批次 A**（u6 musics.fcg，encoding=ag-1，GetPlayTopData / GetPlayTopIndexChart 等）。

- **数据来源**：打开 music_index 后，页面请求 `u6.y.qq.com/cgi-bin/musics.fcg?encoding=ag-1&_webcgikey=GetPlayTopData_HasPlayTopData` 或 `GetPlayTopIndexChart`，拿到 **ag-1 编码的二进制** → 页面用 H5 里的 **ag-1 解码器** 解成 JSON → 再渲染成「xx榜 当期排名x 历史在榜x期 最高排名x」。
- **反推结论**：能反推。这些展示字段（榜单名、当期排名、历史在榜期数、最高排名、日期）一定在 ag-1 解密后的 JSON 里，可能形如：`榜单id/名`、`rank`、`history_count`、`peak_rank`、`date` 等（具体 key 需解密后看）。
- **如何程序化拿到**：  
  1. **解密 ag-1**：从 y.qq.com 的 JS（如 vendor.chunk 或 music_index 入口）里找到 ag-1 解码逻辑，用 Python/Node 复现；然后请求 u6 的上述 URL（带正确 Cookie、sign、body），对响应先解 Brotli（若有）再解 ag-1，即可得到和页面一致的 JSON，从中解析各榜排名。  
  2. **浏览器断点**：在 F12 里对 u6 的该请求的**响应**下 XHR/fetch 断点，等页面执行完 ag-1 解码、把结果赋给某变量或传入 React 组件时，在 Console 里打印该变量，即可看到完整 JSON 结构，再据此写解析脚本（仍需本地能解密 ag-1 才能脱离浏览器）。  
  3. **页面反推（已实现，无需 ag-1）**：用 Playwright 打开 music_index 页面，等「歌曲成就」区块渲染完成后，从 DOM 文本中用正则解析出榜单名、当期排名、历史在榜期数、最高排名、日期，保存为 JSON。脚本：`scripts/fetch_song_achievements_by_mid.py`。依赖：`pip install playwright` 后执行 `playwright install`。用法：`python scripts/fetch_song_achievements_by_mid.py <mid 或 music_index 链接> [--out 输出.json]`。结果会写入 `data/song_achievements_<mid>.json`。

当前项目**已实现 ag-1 解密**（`scripts/ag1_cipher.py` + `scripts/zzc_sign.py`），可直接对 u6 响应解密。**GetPlayTopIndexChart** 解密后得到的是**热度/指数时间序列**（`dateList` + `scoreList`）；**歌曲成就**（榜单名、当期排名等）需抓 **GetPlayTopData_HasPlayTopData** 的响应并用同一方式解密查看。详见本文档 **第 10 节**。在此之前仍可用上述脚本 3 通过浏览器反推页面已展示的成就。

---

## 8. GetPlayTopIndexChart 真实请求示例（便于重放）

- **请求网址**  
  `https://u6.y.qq.com/cgi-bin/musics.fcg?encoding=ag-1&_webcgikey=GetPlayTopIndexChart&_=1772432495175&sign=zzc52616eej1znkfyltduel8uirr8vmez9c6e61e47e`
- **请求方法**：POST  
- **Content-Type**：`application/x-www-form-urlencoded`  
- **请求体**：约 420 字节，为 **ag-1 编码**（非明文 JSON），浏览器里看不到明文。  
- **响应**  
  - 状态 200，`content-type: text/plain; charset=utf-8`  
  - **content-encoding: br**（Brotli 压缩），实际内容先经 br 解压后再是 **ag-1 编码**的二进制，需再解 ag-1 才能得到榜单/热度等 JSON。  
  - 你这次响应约 986 字节（br 压缩后）。

重放时注意：

1. **Cookie** 需带登录态（如 `uin`、`qm_keyst`、`psrf_qqaccess_token` 等），否则可能 200 但数据为空或 code 异常。  
2. **sign** 在 URL 的 query 里（`sign=zzc...`），与请求体一起由前端 zzc 算法生成；重放可直接用抓到的 URL，若要自己算 sign 见 [Jixun 的 zzc 逆向](https://jixun.uk/posts/2024/qqmusic-zzc-sign/)。  
3. **时间戳**：`_=1772432495175` 为毫秒时间戳，重放可沿用或改成当前时间。  
4. 若用 Python 重放，响应需先 `Accept-Encoding: br` 时 httpx 可能自动解 br，得到的是 ag-1 二进制；再对这段二进制做 ag-1 解码才能看到榜单/热度字段（ag-1 解码需从 H5 JS 里逆向）。

已按你之前提供的 cURL 写好重放脚本 `scripts/replay_curl_u6.py`，可把本次请求的 Cookie / URL / body 更新进去后运行；响应若为 br，可在脚本里先解 Brotli 再尝试 ag-1 或保存为 bin 供后续分析。

---

## 9. 关于你贴出的「乱码」响应体

你贴出的那段（`�r:�`、`0\a\�l`、`�mN` 等混有少量汉字）就是 **GetPlayTopIndexChart 的响应体**，本质是 **ag-1 编码的二进制**，不是 UTF-8 文本。

- **为什么会乱码**：把二进制用文本方式打开或粘贴时，编辑器会按 UTF-8/GBK 等解释字节，于是出现 `�` 和偶然的汉字（如 `ʅ`、`ި`），**字节本身没有损坏，但这样无法直接解码**。
- **正确保存方式**：用脚本或 F12 的「Copy response」保存为 **.bin 文件**（不要粘贴到聊天/记事本），再用 Python 以 `rb` 读入，才能做后续解 Brotli、ag-1 等。
- **当前结论**：项目已接入 **ag-1 解密**（见下方第 10 节），可直接对 u6 响应做 XOR 解密得到 JSON；无需再从 JS 逆向。
---

## 10. ag-1 解密已接入：能读到的热度信息

项目内已包含 **ag-1 编解码** 与 **zzc 签名** 实现（来自你提供的实现）：

- **scripts/ag1_cipher.py**：请求体 AES-GCM 加密/解密，**响应体 XOR 循环 key 解密**（`ag1_response_decrypt`）。
- **scripts/zzc_sign.py**：URL 的 `sign=zzc...` 生成（对请求 body 字符串做 SHA1 后按规则拼接）。

**解密本地已抓响应：**

```bash
python scripts/try_decrypt_ag1_response.py   # 读 data/replay_u6_response.bin，解密并打印
python scripts/fetch_qq_music_index_heat.py --decrypt-only data/replay_u6_response.bin --out data/u6_decrypted.json
```

**从 GetPlayTopIndexChart 解密后能读到的热度信息（实测）：**

- **顶层**：`code`、`ts`、`start_ts`、`traceid`、`req_0`。
- **req_0.data.data**：以**维度 id**（如 `001Qkqsw0oUIKX`，可能表示「播放指数」或某榜单维度）为 key，每个 key 下：
  - **dateList**：Unix 时间戳数组（约 5 分钟间隔），表示采样时间点。
  - **scoreList**：与 dateList 一一对应的**数值数组**，表示该时间点的热度/指数分数（单调递增或波动，可视为趋势曲线）。

即：**热度/指数随时间的变化曲线**（时间序列），可直接用来画「近 N 天趋势图」或算当前/峰值。  
**榜单成就**（如「飙升榜 当期排名75 历史在榜16期 最高排名3」）更可能来自 **GetPlayTopData_HasPlayTopData** 的响应，需抓该接口的响应并用同一 ag-1 解密查看结构；解密方式相同。

**自建请求并解密（需 Cookie）：**

```bash
python scripts/fetch_qq_music_index_heat.py 003SqJTl4fnhRC --cookie "uin=xxx; qm_keyst=xxx; ..." --out data/heat.json
```

依赖：`pip install cryptography httpx`。

---

## 11. 「多少人正在听」与「音乐指数」对应哪个接口？

页面上有两个醒目数据：**多少人正在听**、**音乐指数**。根据目前能解密到的内容推断如下。

| 展示项           | 较可能对应的接口                 | 解密后字段位置说明 |
|------------------|----------------------------------|--------------------|
| **音乐指数**     | **GetPlayTopIndexChart**（已确认） | `req_0.data.data` 下以维度 id（如 `001Qkqsw0oUIKX`）为 key，内有 `dateList`（时间戳）+ `scoreList`（指数值）。**当前音乐指数**一般对应 `scoreList` 的**最后一个值**（或与最新时间点对应）。 |
| **多少人正在听** | **GetPlayTopData_HasPlayTopData** 或其它 u6 接口 | 无 Cookie 时 GetPlayTopData_HasPlayTopData 只返回 code 2000，无数据，无法确认。可能在该接口解密后的 `req_0` 里含有「正在听人数」或类似字段；也可能由页面另外请求的第三个 `_webcgikey` 提供。 |

**如何自己确认：**

1. **看页面实际发了哪些 u6 请求**  
   打开  
   `https://y.qq.com/m/client/music_index/index.html?ADTAG=cbshare&channelId=10036163&hosteuin=owvkow-loKCA7v%2A%2A&mid=003SqJTl4fnhRC&openinqqmusic=0&type=003SqJTl4fnhRC`  
   → F12 → **Network** → 筛选 **musics.fcg** 或 **u6.y.qq.com**，列表里每条请求的 URL 都带有 `_webcgikey=xxx`。记下**所有**不同的 `_webcgikey`（例如 GetPlayTopData_HasPlayTopData、GetPlayTopIndexChart，以及是否还有别的如 GetRealTimeListenNum、GetListeningCount 等）。

2. **用 Cookie 解密 GetPlayTopData_HasPlayTopData**  
   带登录态请求并保存解密结果：  
   `python scripts/fetch_qq_music_index_heat.py 003SqJTl4fnhRC --webcgikey GetPlayTopData_HasPlayTopData --cookie "你的Cookie" --out data/GetPlayTopData_decrypted.json`  
   打开 `data/GetPlayTopData_decrypted.json`，搜索 `听`、`listener`、`playNum`、`online`、`count` 等，若找到与「多少人正在听」数值一致的字段，即可确认该接口负责「多少人正在听」。

3. **若不止两个请求**  
   对每个不同的 `_webcgikey` 各抓一条响应（Copy response → 存为 .bin），用  
   `python scripts/fetch_qq_music_index_heat.py --decrypt-only 该文件.bin --out 对应名.json`  
   查看解密后的 JSON 里是否出现「正在听」或「指数」相关字段，从而对号入座。

**当前结论**：**音乐指数**可确定来自 **GetPlayTopIndexChart**（scoreList 末位即当前指数）。**多少人正在听**仍需按上面步骤抓包 + 带 Cookie 解密 **GetPlayTopData_HasPlayTopData**（或其它 u6 接口）后，在 JSON 里对字段确认。

**实测：页面只请求了这两个 u6 接口**  
打开 music_index 链接后，Network 里筛选 u6 / musics.fcg，实际只会看到两种 `_webcgikey`：
- **GetPlayTopData_HasPlayTopData**（会多次请求，时间戳不同）
- **GetPlayTopIndexChart**（一次）

没有第三个 u6 接口。因此「多少人正在听」和「音乐指数」一定分别来自上述两个接口之一；结合已知解密结果，**音乐指数**来自 GetPlayTopIndexChart，**多少人正在听**应来自 **GetPlayTopData_HasPlayTopData** 的响应，需带 Cookie 请求并解密该接口后，在 JSON 中搜索 `听`、`listener`、`playNum`、`online` 等字段即可确认。

**带 Cookie 仍返回 code 2000、无 req_0 时**  
通常表示服务端认为请求参数不合法（例如请求体形状与 GetPlayTopIndexChart 不同）。可对比浏览器实际发送的请求体：

1. F12 → Network → 点开一条 **GetPlayTopData_HasPlayTopData** 请求 → **Payload**（或 Request）里有一串 **base64**（ag-1 加密的 body）。
2. 复制该 base64 字符串，在项目根目录执行：  
   `python scripts/fetch_qq_music_index_heat.py --decrypt-body "粘贴的base64"`  
   会输出**明文 JSON**，即页面真实发送的参数（如是否含 `lastDays`、`requireSongInfo`、不同 `comm` 等）。
3. 若与脚本当前构造的 `{ "songMidList": [mid], "lastDays": 7, "comm": {...} }` 不一致，可在 `fetch_qq_music_index_heat.py` 里为 GetPlayTopData_HasPlayTopData 单独构造相同形状的 payload，或提 issue 附上解密后的 JSON 以便对齐。
