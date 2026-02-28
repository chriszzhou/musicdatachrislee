# QQMusic Crawler (Python)

用于抓取 QQ 音乐公开可访问数据的示例工程，当前支持：

- 按歌手名查 MID（`find-artist`）
- 单歌手快照抓取（`crawl-track`）
- 按天/月/年查看变化（`report-changes`）
- 查询歌手是否有歌曲上榜（`check-artist-toplist`）
- 网易云同等功能（独立菜单 + 独立数据库）
- 酷狗同等功能（独立菜单 + 独立数据库）

> 说明：QQ 音乐接口可能变更或增加风控，本项目用于学习接口抓取流程与工程化组织方式。

## 1. 环境准备

- Python 3.8+

安装依赖：

```bash
pip install -e .
```

## 2. 配置

复制环境变量模板：

```bash
cp .env.example .env
```

新增平台配置示例（可在 `.env` 调整）：

- `NETEASE_RATE_LIMIT_QPS` / `NETEASE_METRIC_WORKERS` / `NETEASE_METRIC_BATCH_SIZE`
- `KUGOU_BASE_URL` / `KUGOU_RATE_LIMIT_QPS` / `KUGOU_METRIC_WORKERS` / `KUGOU_METRIC_BATCH_SIZE`

## 3. 运行

### 3.0 交互式菜单（推荐）

不想手输命令时可直接运行：

```bash
python menu.py
```

菜单内支持：

- 查看歌手歌曲信息（自动按歌手总歌曲数计算页数，不需要手输 `song-pages`）
- 查看变化报告（先选年/月/日，再输入对应日期；输出区间汇总 + 受影响歌曲名/歌手名）
- 查询歌手是否有歌曲上榜（并写入独立榜单库）

### 3.0.1 Web 页面（统一三平台）

启动方式：

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

### 3.0.2 定时抓取（三平台）

脚本 `scheduled_crawl.py` 会依次对 QQ、网易云、酷狗执行指定歌手的歌曲抓取并追踪，默认歌手为「李宇春」。**默认行为**：进程常驻，每 30 分钟执行一轮，直到手动停止（Ctrl+C 或杀进程）。

```bash
# 后台常驻，每 30 分钟执行一轮（推荐）
nohup python scheduled_crawl.py >> /var/log/scheduled_crawl.log 2>&1 &

# 指定间隔（例如每 60 分钟）
nohup python scheduled_crawl.py --interval 60 >> /var/log/scheduled_crawl.log 2>&1 &

# 指定歌手与平台
python scheduled_crawl.py --artist 李宇春 --platforms qq netease kugou

# 每个平台最多抓 2000 首（上限 2000）
python scheduled_crawl.py --artist 李宇春 --song-limit 2000
```

若希望由 cron 每 30 分钟调一次、每次只跑一轮后退出，使用 `--once`：

```bash
# 手动执行一次
python scheduled_crawl.py --once

# cron 示例（每 30 分钟执行一次脚本，脚本跑一轮即退出）
*/30 * * * * cd /path/to/qqmusic-crawler && python scheduled_crawl.py --once >> /var/log/scheduled_crawl.log 2>&1
```

**开机自启（systemd）**：用 systemd 管理进程，开机自动启动、异常退出会自动重启。

1. 复制并编辑服务文件（把 `PROJECT_DIR` 改成项目实际路径，若用虚拟环境则把 `ExecStart` 里的 `python3` 改成 `.venv/bin/python`）：
   ```bash
   mkdir -p ~/.config/systemd/user
   cp scheduled_crawl.service.example ~/.config/systemd/user/scheduled_crawl.service
   # 编辑 scheduled_crawl.service，修改 WorkingDirectory 和 ExecStart 中的路径
   ```
2. 启用并启动服务：
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now scheduled_crawl
   ```
3. 若希望**不登录用户也自动运行**（例如无图形界面开机即跑）：
   ```bash
   loginctl enable-linger $USER
   ```
4. 查看状态与日志：
   ```bash
   systemctl --user status scheduled_crawl
   journalctl --user -u scheduled_crawl -f
   ```

将 `/path/to/qqmusic-crawler` 换成实际项目路径；如需虚拟环境，在命令前加上该环境的 `python` 路径。

网易云独立菜单：

```bash
python netease_menu.py
```

网易云命令行入口：

```bash
python -m qqmusic_crawler.netease_main --help
```

酷狗独立菜单：

```bash
python kugou_menu.py
```

酷狗命令行入口：

```bash
python -m qqmusic_crawler.kugou_main --help
```

网易云默认数据路径（与 QQ 隔离）：

- 快照库目录：`data/netease_snapshots/`
- 变化库：`data/netease_changes.db`
- 榜单库：`data/netease_toplist.db`

酷狗默认数据路径（与 QQ/网易云隔离）：

- 快照库目录：`data/kugou_snapshots/`
- 变化库：`data/kugou_changes.db`
- 榜单库：`data/kugou_toplist.db`

### 3.1 先按歌手名查 MID

```bash
python -m qqmusic_crawler.main find-artist --name 陈奕迅 --max-pages 8
```

### 3.2 单歌手快照抓取 + 变化追踪（推荐）

每次执行都会创建一个新的快照库，并写入变化库（仅有变化才记录）。
执行 `crawl-track` 时会自动抓取歌手粉丝数并写入快照库 `artists.fans`。

```bash
# 直接用歌手名
python -m qqmusic_crawler.main crawl-track --artist-name 陈奕迅 --song-pages 2

# 或者用歌手 MID
python -m qqmusic_crawler.main crawl-track --artist-mid 003Nz2So3XXYek --song-pages 2
```

默认路径：

- 快照库目录：`data/snapshots/`
- 变化库：`data/qqmusic_changes.db`

### 3.3 查看某天变化报告

```bash
# 默认今天
python -m qqmusic_crawler.main report-changes

# 指定日期
python -m qqmusic_crawler.main report-changes --date 2026-02-26

# 指定月份
python -m qqmusic_crawler.main report-changes --month 2026-02

# 指定年份
python -m qqmusic_crawler.main report-changes --year 2026

# 只看某个歌手
python -m qqmusic_crawler.main report-changes --date 2026-02-26 --artist-mid 003Nz2So3XXYek
```

### 3.4 查询某个歌手是否有歌曲上榜

```bash
# 直接用歌手名
python -m qqmusic_crawler.main check-artist-toplist --artist-name 李宇春 --top-n 100

# 用歌手 MID，并限制输出条数
python -m qqmusic_crawler.main check-artist-toplist --artist-mid 002ZOuVm3Qn20Y --top-n 100 --limit 100
```

默认写入独立榜单库：`data/qqmusic_toplist.db`，表名 `artist_toplist_hits`。  
去重键：`artist_mid + top_id + top_period + song_mid`。  
时间字段：`first_seen_at`、`last_seen_at`。

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
