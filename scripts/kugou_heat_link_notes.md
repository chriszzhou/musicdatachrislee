# 酷狗热度页与接口说明

你提供的热度链接格式：

```
https://activity.kugou.com/chart/v-6540f41e/index.html?mixsongid=843131884&u=462010307&h1=42a61dc6004137aab569ab0792240d5fd0c7ff7d&us=NDYyMDEwMzA3&usm=2c346c2d83ecb80ef2d300ca872dd383
```

---

## 1. 和 QQ 的对比：有没有像 ag-1 那样的加密？

**结论：酷狗热度数据接口是明文 JSON，没有 QQ 那种 ag-1 编码。**

实际抓包确认，热度页会请求 **gateway.kugou.com** 的两个接口，响应均为**明文 JSON**：

- **song_info_stat**：`/grow/v1/song_ranking/unlock/v2/song_info_stat` — 歌曲基础信息、解锁状态。
- **ranking**：`/grow/v1/song_ranking/global/v2/ranking` — **热度数据**（当前指数、听众、收藏、全站排名、近7/30天趋势）与**全球上榜记录**（global_rank_list）。

URL 上带 `signature` 参数，**没有**请求体/响应体的 ag-1 式编码。要程序化调用需从页面 JS 逆向出 signature 算法，或短期重放浏览器抓到的带 sign 的 URL。详见下方「如何自己请求」与脚本 `scripts/fetch_kugou_song_heat.py`。

---

## 2. 热度接口 URL 与主要返回字段（抓包结果）

- **歌曲基础信息**  
  `GET https://gateway.kugou.com/grow/v1/song_ranking/unlock/v2/song_info_stat`  
  Query：`srcappid=2919&clientver=1000&clienttime=<ms>&mid=<设备id>&uuid=<同mid>&dfid=-&appid=1058&album_audio_id=<mixsongid>&token=&userid=0&signature=<sign>`  
  返回：`data.song_name`, `data.author_names`, `data.album_name`, `data.album_audio_id`, `data.ranking_num`, `data.platform_info` 等。

- **热度 + 上榜（主要用这个）**  
  `GET https://gateway.kugou.com/grow/v1/song_ranking/global/v2/ranking`  
  Query：同上，`album_audio_id=<mixsongid>`。  
  返回：
  - `data.base_data`：`exponent`（当前指数）、`exponent_diff`（较前一天）、`listener_num`（累计听众）、`collect_count`（收藏量）、`rank`（全站排名）。
  - `data.kugou_exponent.days7`：近 7 天 `{date, exponent}` 趋势。
  - `data.global_rank_list`：全球上榜记录，每项 `date`、`title`（如「酷狗音乐飙升榜 第5名 历史在榜1期 最高排名第5」）、`platform`。

**signature** 由页面 JS 计算，需从 activity 页引用的 `get-base-info` / `request` 等 JS 里逆向；短期可浏览器抓包复制完整 URL 用脚本重放。

**如何自己请求**：F12 → Network → 找到 `ranking` 请求 → 右键 Copy → Copy URL，然后执行：
`python scripts/fetch_kugou_song_heat.py --url '<粘贴的 URL>'`，即可在本地看到热度摘要与上榜记录。

---

## 3. 链接里参数含义（推测）

| 参数       | 值示例        | 推测含义 |
|------------|---------------|----------|
| mixsongid  | 843131884     | 歌曲 ID，项目已存到 `songs.mixsongid`，用于跳转热度页。 |
| u          | 462010307     | 用户 ID（登录态）。 |
| h1         | 42a61dc6...   | 可能是请求签名或 token（如 MD5）。 |
| us         | NDYyMDEwMzA3  | Base64 解码为 `462010307`，即 u 的编码。 |
| usm        | 2c346c2d...   | 可能是另一段签名/设备或会话标识。 |

不带 u/h1/us/usm 只带 `mixsongid=843131884` 也能打开页面（部分能力可能受限），所以**程序里拼热度链接只用了 mixsongid**。

---

## 4. 如何确认「热度数据」能不能直接拿到

热度页和 QQ 一样，一定是**自己调接口**取热度/榜单数据。要判断是「明文接口」还是「加密接口」：

1. 浏览器打开上述链接（或任意一首歌的酷狗热度页）。
2. F12 → **Network**，勾选 **Preserve log**，筛选 **XHR** 或 **Fetch**。
3. 刷新或等待页面加载完，看有没有请求域名像：
   - `activity.kugou.com`、`gateway.kugou.com`、`*.kugou.com` 等。
4. 点开疑似「热度/榜单数据」的请求：
   - 看 **Response**：若是 **JSON 文本**（能直接看到 `play_count`、`rank` 等字段），且 URL 上只有常见 sign/token，则**有可能**用脚本直接请求拿到热度，和 QQ 的 ag-1 不同。
   - 若是**二进制/乱码**或 Response 明显是编码后的，就可能是类似 QQ 的**自定义编码或加密**，需要逆向页面 JS 才能解析。

若你抓到一个返回 JSON 的热度接口，记下：**请求 URL、方法、Query/Body 参数、必要 Header（如 Cookie、sign）**，就可以在项目里加一个「酷狗单曲热度」的请求方法（类似现有 `_fetch_song_favorite_count`），用 mixsongid 去拉热度。

---

## 5. 项目里已实现的酷狗数据

当前我们**已经能直接拿到**的（无 ag-1 类解密）：

- **收藏量**：`gateway.kugou.com` → `/count/v1/audio/mget_collect`，传 `mixsongids`，返回 JSON，已用于快照里的 `favorite_count_text`。
- **评论数**：通过 hash 调评论数接口，返回 JSON。
- **排行榜列表/榜单详情**：`m.kugou.com` 的 `/rank/list`、`/rank/info/` 等，返回 JSON。

**还没有**的：

- **activity.kugou.com 热度页里的「热度值/趋势图」等**：需按上面步骤抓包确认接口是否明文、能否直接请求。

---

**总结**：酷狗热度页调用的两个接口均为**明文 JSON**，无 ag-1；需在 URL 上带 **signature** 才能长期程序化调用，可从页面 JS 逆向 sign 或短期用浏览器抓到的完整 URL 重放。脚本 `scripts/fetch_kugou_song_heat.py` 支持传入抓包得到的 ranking 接口 URL，解析并打印热度与上榜摘要。
