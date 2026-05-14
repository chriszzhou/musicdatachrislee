# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

多平台音乐数据爬虫与可视化系统，支持 QQ音乐、网易云音乐、酷狗音乐三个平台。采集歌手歌曲的收藏量、热度指数、榜单排名等指标，通过 FastAPI Web 界面展示数据变化报告和趋势图表。

## Commands

```bash
# 启动 Web 服务（开发模式）
python3.9 run_web.py --reload

# 安装依赖
python3.9 -m pip install -e .
```

## Architecture

### 数据流

1. **定时调度器** (`schedulers.py`) — Web 启动时创建 3 个 daemon 线程：
   - `toplist` 定时拉取：三平台榜单上榜检查
   - `new_song` 定时更新：指定新歌的收藏量追踪
   - `crawl_track` 定时抓取：歌手全量歌曲快照 + 指标变化检测 + 酷狗异常修正

2. **平台客户端** — 各平台独立的 HTTP 客户端：
   - `client.py` → QQ音乐（加密接口由 `qq_crypto.py` 处理）
   - `netease_client.py` → 网易云
   - `kugou_client.py` → 酷狗
   - `heat_scraper.py` / `kugou_heat_scraper.py` → 通过 Playwright 浏览器抓取热度数据

3. **存储层** — SQLite 数据库（`data/` 目录）：
   - `*_toplist.db` — 榜单数据
   - `*_changes.db` — 指标变化记录
   - `*_snapshots/` — 每次抓取的完整快照 `.db` 文件
   - `milestone_*.log` — 里程碑日志

4. **Web 服务** (`web_service/` 子包) — 业务逻辑层：
   - `paths.py` — 数据路径解析
   - `crawl_ops.py` — 抓取与快照操作
   - `reporting.py` — 变化报告生成
   - `search_top.py` — 搜索与 TOP N 查询
   - `toplist_ops.py` — 榜单操作
   - `new_song.py` — 新歌追踪
   - `milestones.py` — 里程碑检测与异常修正

### 配置

通过 `pydantic-settings` 加载环境变量（`config.py`）：
- `.env` — API 基础配置（超时、QPS 限制等）
- `.env.qqmc` — 业务参数（歌手名、新歌名、定时间隔等，覆盖 `.env` 中同名键）
- 所有 `QQMC_*` 前缀变量控制业务行为

### 关键设计决策

- **三平台并发**：所有定时任务和数据拉取均使用 `ThreadPoolExecutor(max_workers=3)` 三平台并发执行
- **线程安全**：同步业务逻辑通过 `_run_in_thread` 放入线程池，避免阻塞 asyncio 事件循环
- **快照策略**：每次抓取生成独立 SQLite 快照文件，每日自动清理只保留 1 个；变化记录一周后合并压缩
- **北京时区**：所有时间戳统一使用 `BEIJING_TZ = timezone(timedelta(hours=8))`

## Tech Stack

- Python 3.9, FastAPI + Uvicorn, Jinja2 模板
- SQLite (WAL mode) + SQLAlchemy
- httpx (HTTP 客户端), Playwright (浏览器热度抓取)
- pydantic-settings (配置管理), loguru (日志), tenacity (重试)
