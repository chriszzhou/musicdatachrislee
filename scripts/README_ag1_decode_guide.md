# 如何从 y.qq.com 的 JS 里找到并复现 ag-1 解码

u6 的 `musics.fcg?encoding=ag-1` 请求/响应使用 **ag-1** 编码，要程序化拿到「歌曲成就」等榜单数据，需要找到前端的解码逻辑并复现（或用 Node 直接调用）。本文档是**实操步骤**，按顺序做即可缩小范围并尝试还原。

---

## 1. 确认涉及的文件

- **music-react**（统一请求层）：  
  `https://y.qq.com/lib/commercial/h5/music-react-2.3.0.min.js`  
  - 当 URL 匹配 `musics.fcg` 且 `encoding=ag-1` 时，会设置 `dataType: "arraybuffer"`, `responseType: "arraybuffer"`，请求返回的是**原始 ArrayBuffer**。
  - 代码特征：`B="ag-1"===I("encoding",E)`，然后 `dataType:e?"arraybuffer":"json"`，`.then(function(e){E("resolve",e)})`，即把**请求结果 e 原样** resolve 出去。  
  - 因此：若业务侧拿到的已是带 `resDataList` 的对象，说明在**更底层**（或业务封装里）对 arraybuffer 做过解码；若业务拿到的仍是 arraybuffer，则解码在**业务侧**对 `e` 的处理里。

- **music_index 入口**：  
  `https://y.qq.com/m/client/music_index/index.4c4e63969.js`  
  - 使用 `resDataList`（如 `i.resDataList`、`r[0].data`），说明最终用的是「解码后的对象」。  
  - 解码要么在：  
    - music-react 内部发请求后、在 resolve 前对 arraybuffer 做了解码；要么  
    - 在 music_index 的请求封装（如 `n("81ed4b7fd")` 等模块）里，对 promise 的 result 做了 `.then(buf => decode(buf))` 再传给业务。

- **ryqq 的 vendor.chunk**（Jixun 文中提到）：  
  `https://y.qq.com/ryqq/js/vendor.chunk.*.js`  
  - 主要包含 **zzc 签名**（URL 的 `sign`）的虚拟机与算法，不是 ag-1 body 编解码。  
  - ag-1 的编解码有可能也在同包其它模块，或分散在 music-react / 业务 bundle 里。

---

## 2. 用浏览器断点确认「谁把 arraybuffer 变成对象」

目标：看到「解码发生在哪一层、哪个函数」。

1. 打开  
   `https://y.qq.com/m/client/music_index/index.html?mid=003SqJTl4fnhRC`  
   F12 → **Network**，勾选 **Preserve log**，筛选 **musics.fcg**。

2. 在 **Sources** 里 **Ctrl+Shift+F** 全局搜索：  
   - `arraybuffer`  
   - `resDataList`  
   - `encoding` 或 `ag-1`  
   优先在 **music-react-2.3.0.min.js** 和 **index.4c4e63969.js** 里看。

3. 在 **Network** 里找到一条 **musics.fcg** 请求，右键 → **Copy** → **Copy as fetch**。

4. 在 **Console** 里执行刚才复制的 fetch，在 `fetch(...).then(r => r.arrayBuffer())` 后加一行，例如：  
   `then(buf => { console.log('ag1 raw length', buf.byteLength); return buf; })`  
   确认拿到的确实是 ArrayBuffer。

5. 在 **Sources** 里对 **music-react** 或 **index** 中 `.then(function(e){ E("resolve",e)` 或类似位置下**断点**，刷新页面。  
   - 若断点时 `e` 已是**对象**（带 `resDataList` 等），说明在到达这里之前，已有代码把 arraybuffer 转成了对象，需要**往前**看调用栈、或在该脚本里搜处理 `arrayBuffer`/`buffer` 的地方。  
   - 若断点时 `e` 仍是 **ArrayBuffer**，则继续**往后**跟，看谁在 then 链里对 `e` 做了处理并得到 `resDataList`，那里就是解码入口。

6. 在解码入口（或你认为的 decode 函数）上**设断点**，刷新后查看：  
   - 入参（是否为 ArrayBuffer / Uint8Array）；  
   - 出参（是否为 `{ resDataList: [...] }` 形状）；  
   - 该函数的 **Call Stack**，记下所在文件和大致位置（如「music-react 某行」「index 某 chunk」）。

---

## 3. 在源码里搜解码相关关键词

在已下载的 **music-react** 和 **index** 的 JS 里（可用编辑器或 grep），搜索：

- `arrayBuffer`、`ArrayBuffer`、`Uint8Array`  
- `resDataList`（多数在 index 里，看是谁赋的值）  
- `decode`、`decrypt`、`parse`、`decodeAg`、`ag1`、`ag-1`  
- `encoding`（配合 `ag-1` 或 `"ag-1"`）

若 JS 被压缩成单行，可先格式化（如用 Chrome DevTools 的 Pretty print），再搜。  
找到「把 buffer 转成对象」的那段逻辑后，把该函数或该段代码**单独抠出来**（包括它依赖的其它函数/常量），准备用 Node 复现或移植到 Python。

---

## 4. 常见实现方式（便于对照）

很多 H5 的「自定义二进制协议」会采用其一或组合：

- **Brotli / gzip**：先对 body 解压，再按约定解析。  
  u6 响应头常有 `content-encoding: br`，所以**先解 Brotli** 再处理剩余字节。
- **简单 XOR / 加减**：按字节与固定 key 或按位运算。
- **自定义二进制格式**：前几字节表示长度或版本，后面是 TLV、MessagePack、或私有结构。
- **内嵌在虚拟机里**：和 zzc 一样，解码逻辑在 vendor 的 VM 字节码里，需要像 Jixun 那样反编译 VM 或直接导出 VM 的 decode 函数在 Node 里调用。

若你发现解码是「Brotli + 某段 C-like 的字节解析」，就按该逻辑用 Python/Node 重写；若是 VM 里的一坨，可以考虑：  
- 用 **Node** 直接 require 打包好的 vendor 或 music-react，在请求回调里拿到 arraybuffer 后，调用从源码里找到的 decode 函数；或  
- 用 **Puppeteer/Playwright** 在页面上下断点，在 Console 里执行 `decodeAg1(responseBuffer)` 并把结果打印出来，再在本地只复现「解析该 JSON 结构」的脚本。

---

## 5. 复现解码的两种路线

### 路线 A：Node 直接调前端 decode（推荐先试）

1. 用 **puppeteer** 或 **playwright** 打开 music_index 页面，在 **Page.exposeFunction** 或 **evaluate** 里，把 **window** 上挂的 decode 函数（或能访问到 decode 的模块）暴露出来。  
   若 decode 不在 window 上，可在断点停住时，在 Console 里把该函数赋给 `window.__ag1Decode = xxx`，再在 Node 里通过 `page.evaluate('window.__ag1Decode')` 拿到。

2. 在 Node 里用 **httpx/fetch** 重放 u6 请求（带 Cookie、sign、ag-1 的 body），拿到 ArrayBuffer，通过 **page.evaluate** 传给前端的 decode，取回解码后的对象。  
   这样无需完全逆向算法，只要「请求在 Node，解码在浏览器」。

### 路线 B：完全用 Python/Node 复现算法

1. 根据断点找到的**解码函数**，在格式化后的 JS 里把该函数及其依赖**完整抠出**。  
2. 若有 Brotli，用现有库（如 Node 的 `zlib`/`brotli`，Python 的 `brotli`）先解压。  
3. 若为自定义字节解析，按 JS 逻辑写成 Python/Node（读 Uint8Array、按位、拼接 JSON 等）。  
4. 若解码在 VM 里，则需要像 [Jixun 的 zzc 分析](https://jixun.uk/posts/2024/qqmusic-zzc-sign/) 一样，反编译 VM 或导出 VM 的 runner，在 Node 里执行 VM 的 decode 字节码。

---

## 6. 参考与脚本

- **zzc 签名**（URL 的 sign，与 ag-1 body 不同）：  
  [对抗 QQ 音乐网页端的请求签名 (zzc + ag-1) - Jixun's Blog](https://jixun.uk/posts/2024/qqmusic-zzc-sign/)  
  文中虚拟机代码来自：  
  `https://y.qq.com/ryqq/js/vendor.chunk.b6ee1532c576a0967fed.js`  
  该文主要讲 sign，ag-1 的 body/响应解码需在上述 JS 里单独找。

- 项目内已有：  
  - `scripts/try_decrypt_ag1_response.py`：对 `data/replay_u6_response.bin` 尝试 gzip/zlib/XOR 等，可在此基础上补「Brotli 解压 + 你找到的 ag-1 解析」。  
  - `scripts/replay_curl_u6.py`：重放 u6 请求，得到二进制响应；若响应是 br，可先解 Brotli 再送入解码。  
  - `scripts/music_index_api_analysis.md`：接口与请求链说明。

- 下载 JS 到本地便于搜索（可选）：  
  ```bash
  curl -sL -o music_react.js "https://y.qq.com/lib/commercial/h5/music-react-2.3.0.min.js?max_age=604800&app=qqmusic&version=20250616&md5=13da3f2162"
  curl -sL -o index.js "https://y.qq.com/m/client/music_index/index.4c4e63969.js"
  grep -n "arraybuffer\|resDataList\|ag-1\|decode\|encoding" music_react.js index.js
  ```

---

## 7. 小结

| 步骤 | 做什么 |
|-----|--------|
| 1 | 确认 ag-1 时请求返回 ArrayBuffer，resolve 前/后谁把它变成带 `resDataList` 的对象 |
| 2 | 用断点找到「解码函数」所在文件和调用链 |
| 3 | 在 music-react / index / vendor 里搜 arrayBuffer、resDataList、decode、ag-1 |
| 4 | 抠出解码逻辑：Brotli + 自研解析 或 VM 字节码 |
| 5 | 复现：Node 调前端 decode，或 Python/Node 完全重写 |

当前项目**尚未**实现 ag-1 解码；完成上述步骤后，可将解码逻辑接到 `try_decrypt_ag1_response.py` 或新脚本，实现「请求 u6 → 解 br → 解 ag-1 → 解析歌曲成就」。
