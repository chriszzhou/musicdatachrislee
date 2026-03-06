# 抓包工具备选（手机 HTTPS 抓包）

若 Burp 安装/运行有问题，可改用下面任一工具。思路相同：**电脑开代理 → 手机连同一 WiFi 并设置代理 → 安装对应 CA 证书**。

---

## Fiddler Everywhere 抓手机包（一步步）

按下面顺序做，就能用电脑上的 Fiddler 抓到手机 App 的 HTTPS 请求。

### 第一步：电脑上打开 Fiddler 并允许手机连接

1. 启动 **Fiddler Everywhere**（已下载好的 AppImage 直接运行）。
2. 点击左下角 **Settings（齿轮）** → 左侧选 **Connections**。
3. 确认：
   - **Fiddler listens on port** 为 **8866**（默认就是）。
   - 勾选 **Allow remote computers to connect**（允许手机等设备通过代理连接）。
4. 如有弹窗提示“重启生效”，点确定并**重启 Fiddler**。
5. 回到主界面，点击 **Live Traffic** 或 **Capture**，确保正在捕获（开关是开启状态）。

### 第二步：查电脑在本局域网的 IP

手机要填的“代理主机”就是电脑的 IP。在电脑终端执行：

```bash
# 任选一条，能看到 192.168.x.x 或 10.x.x.x 即可
ip addr show | grep "inet " | grep -v 127.0.0.1
# 或
hostname -I
```

记下其中一个 **192.168.x.x** 或 **10.x.x.x**，例如 `192.168.1.100`。

### 第三步：手机连同一 WiFi 并设置代理

- **Android**：设置 → WLAN → 长按当前连接的 WiFi → 修改网络 / 高级选项 → 代理选「手动」→ 主机名填电脑 IP（如 `192.168.1.100`），端口填 **8866** → 保存。
- **iPhone**：设置 → 无线局域网 → 当前 WiFi 右侧 (i) → 配置代理选「手动」→ 服务器填电脑 IP，端口 **8866** → 存储。

此时手机的上网流量会先经过电脑上的 Fiddler，但 **HTTPS 还没解密**，需要下一步装证书。

### 第四步：在手机上安装 Fiddler 证书（必须，否则看不到 HTTPS 内容）

1. 确保手机代理已填好（第三步），且 Fiddler 在电脑上正在运行。
2. 用手机**浏览器**（不要用微信/QQ 内置浏览器）打开：
   ```
   http://<电脑IP>:8866
   ```
   例如：`http://192.168.1.100:8866`
3. 页面会提示下载 Fiddler 根证书（或显示 Fiddler 的证书说明页，上有下载链接）。
4. **Android**：下载后到 设置 → 安全 → 加密与凭据 → 安装证书 / 从存储设备安装，选刚下的证书安装。部分机型在 设置 → 更多安全设置 里。
5. **iPhone**：下载描述文件后，到 设置 → 已下载描述文件 → 安装；再到 设置 → 通用 → 关于本机 → 证书信任设置，**对 Fiddler 的证书启用“完全信任”**。

### 第五步：抓包并查看

1. 手机代理和证书都设好后，在手机上打开 **QQ 音乐、酷狗** 等 App，随便点几个页面（歌曲、歌手、评论等）。
2. 回到电脑上的 Fiddler，在 **Live Traffic** 列表里会看到一条条请求；点某一条可查看 URL、请求头、请求体、响应内容。
3. 需要复现某条请求时：在 Fiddler 里右键该请求 → **Copy → Copy as cURL**，即可在脚本里用或贴到终端测试。

### 抓完后记得关代理

不用抓包时，把手机 WiFi 的代理改回「无」或「关闭」，否则手机上网会一直走电脑，关电脑就断网。

---

## 抓包数据导出与分析

抓到的请求可以在 Fiddler 里直接看，也可以导出后用脚本统计、过滤、复现。

### 一、在 Fiddler Everywhere 里导出

1. **单条请求 → 复制为 cURL（最常用）**  
   在 Live Traffic 里**右键某条请求** → **Copy → Copy as cURL**。  
   得到一整行 `curl 'https://...' -H '...'`，可以贴到终端执行，或在脚本里用 `requests`/`httpx` 复现。

2. **导出全部/选中为「多条 cURL」→ 得到 .sh 文件（常见）**  
   Fiddler Everywhere 的导出里可能**没有 HAR**，而是 **Export / Copy as cURL** 时选“多条请求”，保存成一个 **.sh 文件**（里面每行或每段一条 `curl ...`）。  
   这种文件可以直接用项目里的 **`scripts/analyze_curl_sh.py`** 分析，见下面「三、用脚本分析 .sh（curl 导出）」。

3. **若有「Export as HAR」**  
   选中请求 → 菜单里找 **Export → Export as HAR**，保存为 `.har`。  
   可用 **`scripts/analyze_har.py`** 解析；若没有该选项，就用 .sh + `analyze_curl_sh.py`。

4. **导出为 SAZ（Fiddler 专用）**  
   会话存成 `.saz` 可用 Fiddler 再次打开；要用 Python 统计/过滤时，优先用 .sh 或 HAR。

### 二、在 Fiddler 里先做简单分析

- **按域名过滤**：在过滤框输入主机名，如 `y.qq.com`、`kugou.com`，只显示该域名的请求。  
- **按状态码**：如 `200`、`404`，快速找失败请求。  
- **搜索**：在请求/响应内容里搜关键词（如接口名、参数名）。  
- **看请求/响应**：点开某条，看 **Inspectors** 里的 Request Body、Response JSON，确认是不是你要的 API。

### 三、用 Python 脚本分析导出文件

- **你导出的是 .sh（里面是多条 curl）**  
  用 **`scripts/analyze_curl_sh.py`**：

  ```bash
  python scripts/analyze_curl_sh.py 你的导出.sh
  python scripts/analyze_curl_sh.py 你的导出.sh --host y.qq.com
  python scripts/analyze_curl_sh.py 你的导出.sh --host kugou.com
  ```

  会列出每条请求的 方法、Host、URL，并按 Host 统计条数，方便筛出音乐接口再回到 Fiddler 里对单条 **Copy as cURL** 复现。

- **你导出的是 .har**  
  用 **`scripts/analyze_har.py`**：

  ```bash
  python scripts/analyze_har.py 你的文件.har
  python scripts/analyze_har.py 你的文件.har --host y.qq.com
  ```

### 四、和本项目的配合

- **复现某条 API**：Fiddler 里 **Copy as cURL** → 在 `probe_qq_song_heat.py` 或新脚本里用相同 URL、Header、Body 发请求。  
- **批量筛接口**：导出为 .sh 或 HAR → 用 `analyze_curl_sh.py` / `analyze_har.py` 按 `y.qq.com`、`kugou.com` 过滤，找到目标接口的 URL 和参数，再写成爬虫或探测脚本。

### 五、更多歌曲数据（基于抓包接口）

抓包里若出现以下接口，可用来**扩充歌曲数据来源**（歌单、H5 歌曲详情等）：

| 需求         | 抓包中的接口 | 说明 |
|--------------|----------------------|------|
| 歌曲元数据   | **u6.y.qq.com/cgi-bin/musics.fcg** | H5 端明文 JSON，协议与 `u.y.qq.com` 的 musicu.fcg 类似，comm + req_0(module/method/param)。 |
| 歌单/歌曲列表 | **c.y.qq.com/vipdown/fcgi-bin/fcg_3g_song_list_rover.fcg** | POST，可能需从抓包中拿到完整 body/参数。 |
| 播放/试听链接 | 多 CDN 的 m4a URL（fileid + vkey/guid/uin） | 需先由某接口拿到 vkey 再拼 URL。 |
| 歌手封面     | **y.gtimg.cn** 规则 `T002R500x500M000{歌手mid}_1.jpg` | 已知歌手 mid 即可拼地址。 |

项目里提供了探测脚本，用于验证上述接口是否可复现、能拿到哪些字段：

```bash
python scripts/probe_more_song_sources.py
```

脚本会请求：  
1）**u6.y.qq.com** 的 musics.fcg（歌曲详情 + Setting）；  
2）**c.y.qq.com** 的 fcg_3g_song_list_rover.fcg。  

若 u6 返回 `code=2000` 等，多半需要**登录 cookie 或 sign**（用 Fiddler 里抓到的完整 Cookie/Header 再试）；  
若 c.y.qq.com 返回空或 JSONP，需在 Fiddler 里打开**歌单页/歌曲列表页**，看该接口的 **POST body** 参数（如 listid、albumid 等），再在脚本里带上相同参数请求。  

**在 Fiddler 里怎么找到 Cookie 和 sign：**

1. **先抓到那条请求**  
   在手机 QQ 音乐里打开「歌曲详情」或「歌单」页，让请求出现在 Fiddler 的 Live Traffic 里。在列表里找到 **u6.y.qq.com** 且 URL 里带 `musics.fcg` 的那一条，**单击选中**。

2. **看请求头（Request Headers）**  
   选中后，右侧或下方会显示该请求的详情。切到 **Request**（请求）那一块，再点 **Headers** 或「请求头」标签，会看到一列 Header 名和值：
   - **Cookie**：找名字叫 `Cookie` 的那一行，整行内容就是完整 Cookie（很长一串），**右键该行 → Copy value** 或**双击值再全选复制**。
   - **sign**：在同一列里找有没有叫 `sign`、`Sign`、`mask` 等的行，若有也一起复制值（脚本里要带到 Header 里）。

3. **用「Copy as cURL」一次性带走（推荐）**  
   若 Fiddler 有 **Copy → Copy as cURL**（或右键请求选「复制为 cURL」），会得到一整行 `curl '...' -H 'Cookie: ...' -H 'sign: ...' ...`，里面已经包含 Cookie 和所有 Header。把这段贴到记事本，搜 `Cookie` 和 `sign` 就能看到；或直接把 cURL 贴到终端执行，看能否复现 200。  
   若没有 cURL，就按上一步在 Headers 里手动复制 `Cookie` 和 `sign` 的值。

4. **填进脚本**  
   在 `probe_more_song_sources.py` 里，给 `httpx.post(..., headers={...})` 加上你复制的 Cookie 和 sign，例如：
   ```python
   headers = {
       **HEADERS_H5,
       "Cookie": "你复制的整串 Cookie",
       "sign": "你复制的 sign 值",  # 若有这一项
   }
   ```
   再运行脚本看 u6 是否还返回 2000。

如果界面里没看到「Headers」：有的版本是 **Inspectors → Request → Headers**，或点开请求后上半部分是 URL、下面有 **Headers / Body** 两个标签，点 Headers 即可看到 Cookie 等。

**你贴的那条 cURL 说明：**

- **Cookie**：在 cURL 里就是 `-H "cookie: pgv_pvid=...; uin=...; qm_keyst=..."` 这一整段，引号里的内容就是完整 Cookie（已包含在你贴的里面）。
- **sign**：这条请求里没有单独的 sign Header，**sign 在 URL 里**，即问号后面的 `sign=zzcfe09941ycytkq03jxaqde2ku8l6dy746xk98a1127e`。
- 这条请求是 **GetPlayTopIndexChart**，且 URL 带 **encoding=ag-1**，表示**请求体和响应体都是加密的**（body 那一大串不是 JSON，是 ag-1 编码）。所以即使用同一份 Cookie 和 URL 重放，拿到的也是二进制响应，需要客户端解密才能得到榜单数据。

若要拿到**明文歌曲数据**，在 Fiddler 里要找的是：**没有** `encoding=ag-1`、且 **Request Body 是明文 JSON**（能看到 `{"comm":...,"req_0":{...}}`）的那条 u6 请求（例如点进某首**歌曲详情 H5 页**或**歌单列表**时触发的 musics.fcg）。那种请求用脚本发同样 JSON + Cookie 就能直接得到 JSON 响应。

项目里已用你这条 cURL 写好**原样重放**脚本，可直接运行看返回内容：
```bash
python scripts/replay_curl_u6.py
```
响应会保存到 `data/replay_u6_response.bin`（当前为 ag-1 二进制，非 JSON）。

**可以尝试解密 ag-1 吗？**

可以尝试，但**请求/响应体的 ag-1 编解码**目前没有现成公开实现，需要自己从 QQ 音乐 H5 的 JS 里找或逆向。

- **URL 的 sign（zzc 签名）**：已有人逆向并写出算法，见 [Jixun 的博客](https://jixun.uk/posts/2024/qqmusic-zzc-sign/)（含 TypeScript 实现）。你若能拿到**明文请求 JSON**，可用该算法自己算 sign，不必依赖抓包里的 sign。
- **ag-1 的 body 与响应**：请求体那一大串和服务器返回的二进制都是 ag-1 编码。项目里提供了实验脚本，先重放拿到响应，再尝试几种常见解密（gzip、zlib、单字节 XOR 等）：
  ```bash
  python scripts/replay_curl_u6.py   # 生成 data/replay_u6_response.bin
  python scripts/try_decrypt_ag1_response.py
  ```
  若脚本里的简单方式都解不开，就需要在浏览器里：打开 y.qq.com → F12 → Sources 里全局搜 `ag-1` 或 `encoding`，或在 Network 里对 musics.fcg 的响应下断点，看是哪段 JS 处理了响应体（即 ag-1 解码逻辑），再从中还原算法或直接调用页面里的解码函数。`try_decrypt_ag1_response.py` 运行结束也会打印这段操作提示。

**建议**：在 Fiddler 里单独打开「歌单列表」「某首歌曲详情 H5 页」，只导出这几条请求的 cURL，查看 u6 的 `module`/`method`/`param` 和 c.y.qq.com 的 body，再在 `probe_more_song_sources.py` 中补全参数复现，即可确认能多拿到哪些歌曲数据并接入现有爬虫。

**music_index 页面打开后调用了哪些接口？**  
已根据入口 JS 做了分析，结论是：页面会**自行再请求** `u6.y.qq.com/cgi-bin/musics.fcg?encoding=ag-1`，共两批批量请求——一批带 `songMidList + lastDays`（榜单/趋势，可能含热度），一批带 `songMidList + requireSongInfo`（歌曲详情/songInfo）。请求和响应都是 **ag-1 编码**，详见 **`scripts/music_index_api_analysis.md`**。

---

## 1. mitmproxy（推荐，纯 Python/命令行）

- **优点**：无需 Java、跨平台、可导出 HAR、支持脚本过滤/重放。
- **安装**：
  ```bash
  pip install mitmproxy
  # 或项目里已有 .venv-mitmproxy 可激活后：pip install mitmproxy
  ```
- **使用**：
  ```bash
  mitmproxy -p 8080
  ```
  浏览器访问 **http://mitm.it** 按手机系统下载并安装证书。手机 WiFi 代理：电脑 IP，端口 8080。
- **导出**：在 mitmproxy 里按 `w` 可保存为 HAR；或 `mitmdump -w out.flow` 录制成 flow 文件。

---

## 2. Charles Proxy

- **优点**：界面友好、支持 SSL 解密、可导出 HAR/cURL。
- **缺点**：收费（有试用）；Linux 官方无原生版，可用 Wine 或只在 Windows/macOS 上用。
- 官网：https://www.charlesproxy.com/

---

## 3. Fiddler Everywhere（Linux 详细安装）

- **优点**：图形界面、支持 HTTPS 解密、可导出 HAR/cURL；官方提供 Linux AppImage。
- **默认代理端口**：**8866**（手机填电脑 IP + 8866）。
- **官网下载**：https://www.telerik.com/download/fiddler/fiddler-everywhere-linux  

### 安装步骤（Linux）

1. **下载 AppImage**  
   打开上面的链接，选择 **Fiddler Everywhere Linux (Ubuntu 20+)** 下载，得到类似 `Fiddler Everywhere-7.x.x-x86_64.AppImage` 的文件。

2. **放到目录并赋予执行权限**（例如放到 `~/bin` 或 `~/.local/bin`）：
   ```bash
   mv ~/Downloads/Fiddler*.AppImage ~/.local/bin/fiddler-everywhere.AppImage
   chmod +x ~/.local/bin/fiddler-everywhere.AppImage
   ```

3. **依赖（Ubuntu 22.04 / 24.04 等）**  
   若运行报错缺少 FUSE，先装：
   ```bash
   sudo apt install libfuse2
   ```
   若启动时出现沙箱相关错误，可加 `--no-sandbox` 运行：
   ```bash
   ~/.local/bin/fiddler-everywhere.AppImage --no-sandbox
   ```

4. **首次启动**  
   会要求注册/登录 Telerik 账号（有免费试用）。登录后即可开始抓包。

### 抓手机流量

1. 电脑与手机连**同一 WiFi**。
2. 在 Fiddler Everywhere 里打开 **Settings → Connections**，确认 **Fiddler listens on port** 为 `8866`，并勾选 **Allow remote computers to connect**。
3. 手机 WiFi 设置里配置**代理**：主机填电脑在本网的 IP（如 `192.168.1.100`），端口填 `8866`。
4. 手机浏览器打开 **http://\<电脑IP\>:8866**（或 Fiddler 界面里提示的地址），下载并安装 Fiddler 根证书。
5. 在手机上打开 QQ 音乐/酷狗等 App 操作，电脑上 Fiddler 即可看到解密后的请求。

---

## 5. HTTP Toolkit

- **优点**：开源、跨平台、一键启动并自动配证书，对非技术用户友好。
- 官网：https://httptoolkit.com/  
  Linux 可下载 AppImage 或 deb。

---

## 6. 手机端直接抓包（不经过电脑代理）

- **HttpCanary / Packet Capture（Android）**：手机本机安装，需在系统里安装其 CA 证书（Android 7+ 需配合「用户证书」或 root）。适合快速看请求，但导出/分析不如电脑端方便。
- **Stream（iOS）**：需安装描述文件并信任证书，可抓本机 App 的 HTTPS。

---

## 与本项目的配合

抓到的请求可：

- 在 Burp/mitmproxy/Charles 里**复制为 cURL**，再在脚本里复现。
- 用 mitmproxy 的 **HAR 或 flow 导出**，再写脚本解析 URL/参数（如补全 `probe_qq_song_heat.py` 里缺失的「歌曲热度」接口）。

**通用步骤**：手机与电脑同 WiFi → 手机 WiFi 设置代理（电脑 IP + 工具端口）→ 浏览器打开工具提示的页面（如 http://mitm.it 或 http://burp）→ 下载并安装 CA 证书 → 打开 QQ 音乐/酷狗等 App 操作 → 在工具里查看、导出请求。
