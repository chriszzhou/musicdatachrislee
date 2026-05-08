"""新歌页等业务用常量（供 reporting / new_song 等共用，避免循环导入）。

值来自 `config.settings`（`.env` + 可选 `.env.qqmc`），勿在此写死歌手/歌名。
"""

from __future__ import annotations

from ..config import settings


def _strip_or(default: str, value: str) -> str:
    t = (value or "").strip()
    return t or default


NEW_SONG_ARTIST = _strip_or("李宇春", settings.qqmc_new_song_artist)
NEW_SONG_NAME = _strip_or("春雨里", settings.qqmc_new_song_name)
NEW_SONG_CHART_START_DATE = _strip_or("2026-03-16", settings.qqmc_new_song_chart_start_date)
NEW_SONG_CHART_NUM_POINTS = max(1, min(500, int(settings.qqmc_new_song_chart_num_points)))

# 各平台 song_mid（配置后优先按 mid 精确匹配，避免同名歌曲歧义）
NEW_SONG_MIDS = {
    "qq": (settings.qqmc_new_song_mid_qq or "").strip(),
    "netease": (settings.qqmc_new_song_mid_netease or "").strip(),
    "kugou": (settings.qqmc_new_song_mid_kugou or "").strip(),
}
