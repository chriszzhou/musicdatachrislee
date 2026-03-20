# QQMusic Crawler (Python)

用于抓取 QQ 音乐公开可访问数据的示例工程，当前支持：

- 按歌手名查 MID、单歌手抓取与变化追踪、按日/月/年变化报告、歌手上榜检查（均在 **Web** 内按平台操作）
- 网易云 / 酷狗同等能力（Web 内切换平台；数据各自独立库）

> 说明：QQ 音乐接口可能变更或增加风控，本项目用于学习接口抓取流程与工程化组织方式。

## 1. 环境准备

- Python 3.8+

安装依赖（**务必与运行 Web 时用的 Python 是同一个**，例如用 `python3.9 run_web.py` 则用 `python3.9 -m pip`）：

```bash
python3.9 -m pip install -e .
# 或系统默认已是 3.9+ 时：
pip install -e .
```

## 2. 配置

复制环境变量模板：

```bash
cp .env.example .env
# 可选：歌手名 / 新歌名 / 定时任务等（见模板内说明）
cp .env.qqmc.example .env.qqmc
```

环境文件均放在**仓库根目录**；`config` 会依次加载 `.env`、`.env.qqmc`（后者覆盖同名键）。`.env.qqmc` 已加入 `.gitignore`，适合本机差异配置。

新增平台配置示例（可在 `.env` 调整）：

- `NETEASE_RATE_LIMIT_QPS` / `NETEASE_METRIC_WORKERS` / `NETEASE_METRIC_BATCH_SIZE`
- `KUGOU_BASE_URL` / `KUGOU_RATE_LIMIT_QPS` / `KUGOU_METRIC_WORKERS` / `KUGOU_METRIC_BATCH_SIZE`

**业务可调（推荐写在 `.env.qqmc`，也可写在 `.env`）** — 详见根目录 **`.env.qqmc.example`**：

- **新歌页**：`QQMC_NEW_SONG_ARTIST`、`QQMC_NEW_SONG_NAME`、`QQMC_NEW_SONG_CHART_START_DATE`、`QQMC_NEW_SONG_CHART_NUM_POINTS`
- **定时任务**：`QQMC_TOPLIST_ARTIST_NAME`、`QQMC_TOPLIST_SCHEDULE_START_HOUR`、`QQMC_TOPLIST_INTERVAL_MINUTES`、`QQMC_NEW_SONG_UPDATE_INTERVAL_SEC`、`QQMC_CRAWL_TRACK_ARTIST_NAME`、`QQMC_CRAWL_TRACK_INTERVAL_MINUTES`
- **首页默认**：`QQMC_DEFAULT_TOPSONGS_ARTIST_NAME`（不填则与 `QQMC_TOPLIST_ARTIST_NAME` 相同）

**SQLite 并发**（可选，减轻后台写入与 Web 读库的锁冲突；详见 `sqlite_util.py`）：

- `QQMC_SQLITE_CONNECT_TIMEOUT` — `sqlite3.connect(timeout=…)` 秒数（默认 `30`）
- `QQMC_SQLITE_BUSY_TIMEOUT_MS` — `PRAGMA busy_timeout` 毫秒（默认 `60000`）；快照库经 SQLAlchemy 打开时也会设置

## 3. 运行

日常使用以 **Web 页面** 为入口即可（三平台统一：`http://127.0.0.1:8000/`）。**启动 Web 服务后**，进程内会按设定执行榜单拉取、新歌更新、以及三平台定时抓取（李宇春等），**无需**再跑独立定时脚本。各平台独立 CLI（`main` / `netease_main` / `kugou_main`）已移除。

### 3.0 Web 页面（统一三平台）

本仓库代码在 **`src/qqmusic_crawler/`**。需要两件事：

1. **能找到包**：`src` 须在路径里（`run_web.py` / `PYTHONPATH=src` / `pip install -e .` 均可）。  
2. **已安装第三方依赖**：`loguru`、`fastapi`、`uvicorn` 等须装进**当前使用的解释器**，否则报 **`No module named 'loguru'`**。用 `python3.9 -m pip install -e .` 即可一次装全。

`run_web.py` 会自动设置 **`PYTHONPATH`**，方便 `--reload` 子进程也能找到 `qqmusic_crawler`；依赖仍需按上一步安装。

**推荐**：在仓库根目录执行。

若系统默认 `python3` 低于 3.9，请用本机已安装的 **3.9+** 解释器显式运行：

```bash
python3.9 run_web.py --reload
```

等价于带 `PYTHONPATH=src` 的 uvicorn：

```bash
export PYTHONPATH=src
uvicorn qqmusic_crawler.web_main:app --host 0.0.0.0 --port 8000 --reload
```

若已 `pip install -e .`，可直接：

```bash
uvicorn qqmusic_crawler.web_main:app --host 0.0.0.0 --port 8000 --reload
```

浏览器访问：

- `http://127.0.0.1:8000/`

Web 页面支持：

- 平台切换（`qq` / `netease` / `kugou`）
- 歌手搜索
- 抓取并追踪（自动页数）
- 变化报告（year/month/day）
- 歌手上榜检查
- 歌曲 TOP N（收藏 / 评论）

使用示例：

1. 打开页面后先选择平台（例如 `kugou`）
2. 先执行“抓取并追踪”生成快照
3. 再执行“变化报告”或“当前快照 TOP N”查看结果

**开机自启 Web**：例如 systemd 里 `WorkingDirectory` 指向项目根，`ExecStart` 使用你的 `python3.9 …/run_web.py`（勿再引用已删除的 `scheduled_crawl`）。若机器上仍留有旧的 `scheduled_crawl` systemd/cron，可按仓库内 `CANCEL_SCHEDULED_CRAWL_AUTOSTART.md` 清理。

### 3.1 三平台数据目录（与 Web 写入路径一致）

**QQ 音乐**

- 快照库目录：`data/snapshots/`
- 变化库：`data/qqmusic_changes.db`
- 榜单库：`data/qqmusic_toplist.db`

**网易云**（与 QQ 隔离）

- 快照库目录：`data/netease_snapshots/`
- 变化库：`data/netease_changes.db`
- 榜单库：`data/netease_toplist.db`

**酷狗**（与 QQ/网易云隔离）

- 快照库目录：`data/kugou_snapshots/`
- 变化库：`data/kugou_changes.db`
- 榜单库：`data/kugou_toplist.db`

QQ 榜单库表名 `artist_toplist_hits`；去重键：`artist_mid + top_id + top_period + song_mid`；时间字段含 `first_seen_at`、`last_seen_at`。

## 4. 数据库表

- `artists`（快照库中的表）
  - `artist_mid` (PK)
  - `name`
  - `fans`（歌手粉丝数）
  - `region`
  - `genre`
  - `raw_json`
- `songs`（快照库中的表）
  - `song_mid` (PK)
  - `song_id` (QQ 数字 ID)
  - `name`
  - `artist_mid` (FK -> artists.artist_mid)
  - `album_name`
  - `duration`
  - `publish_time`
  - `comment_count`（评论量，可作为热度参考）
  - `favorite_count`（已弃用，不再写入）
  - `favorite_count_text`（收藏量数值，已转数字，如 1200w+ -> 12000000）
  - `raw_json`

> 说明：当前仅抓取评论量和收藏量，保证抓取速度；官方“流行指数”接口暂未稳定可用。
> 酷狗公开接口下，评论量和收藏量可能不可用，当前会写入 0 并保持流程可运行。

## 5. 合规说明

- 请仅抓取公开可访问数据
- 遵守目标站点服务条款和 robots 规范
- 避免高并发请求，建议保持低速率并添加重试间隔

## 6. 变化表说明

变化库 `data/qqmusic_changes.db` 主要使用：

- `metric_changes`
  - 指标变化记录（字段 `metric`: `comment_count` / `favorite_count_text`，以及 `delta`）
- `artist_metric_changes`
  - 歌手指标变化记录（字段 `metric`: `fans`，以及 `delta`）
- `artist_toplist_hits`（在 `data/qqmusic_toplist.db`）
  - 记录歌手上榜歌曲、榜单名、名次、首末发现时间（支持去重）
