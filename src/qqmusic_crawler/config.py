from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 与 run_web / 包内模块一致：以「含 src/qqmusic_crawler 的仓库根」为基准加载环境文件
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILES = (str(_REPO_ROOT / ".env"), str(_REPO_ROOT / ".env.qqmc"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    qqmusic_base_url: str = Field(
        default="https://u.y.qq.com/cgi-bin/musicu.fcg", alias="QQMUSIC_BASE_URL"
    )
    netease_base_url: str = Field(
        default="https://music.163.com", alias="NETEASE_BASE_URL"
    )
    kugou_base_url: str = Field(default="http://mobilecdn.kugou.com", alias="KUGOU_BASE_URL")
    qqmusic_timeout: int = Field(default=15, alias="QQMUSIC_TIMEOUT")
    qqmusic_max_retries: int = Field(default=3, alias="QQMUSIC_MAX_RETRIES")
    qqmusic_rate_limit_qps: float = Field(default=1.0, alias="QQMUSIC_RATE_LIMIT_QPS")
    netease_rate_limit_qps: float = Field(default=8.0, alias="NETEASE_RATE_LIMIT_QPS")
    netease_metric_workers: int = Field(default=8, alias="NETEASE_METRIC_WORKERS")
    netease_metric_batch_size: int = Field(default=10, alias="NETEASE_METRIC_BATCH_SIZE")
    kugou_rate_limit_qps: float = Field(default=5.0, alias="KUGOU_RATE_LIMIT_QPS")
    kugou_metric_workers: int = Field(default=8, alias="KUGOU_METRIC_WORKERS")
    kugou_metric_batch_size: int = Field(default=10, alias="KUGOU_METRIC_BATCH_SIZE")
    qqmusic_default_artist_page_size: int = Field(
        default=40, alias="QQMUSIC_DEFAULT_ARTIST_PAGE_SIZE"
    )
    qqmusic_default_song_page_size: int = Field(
        default=50, alias="QQMUSIC_DEFAULT_SONG_PAGE_SIZE"
    )

    # ---------- 业务可调（可放在 .env.qqmc）----------
    qqmc_new_song_artist: str = Field(default="李宇春", alias="QQMC_NEW_SONG_ARTIST")
    qqmc_new_song_name: str = Field(default="春雨里", alias="QQMC_NEW_SONG_NAME")
    qqmc_new_song_chart_start_date: str = Field(
        default="2026-03-16", alias="QQMC_NEW_SONG_CHART_START_DATE"
    )
    qqmc_new_song_chart_num_points: int = Field(
        default=10, alias="QQMC_NEW_SONG_CHART_NUM_POINTS", ge=1, le=500
    )

    qqmc_toplist_artist_name: str = Field(default="李宇春", alias="QQMC_TOPLIST_ARTIST_NAME")
    qqmc_toplist_schedule_start_hour: int = Field(
        default=8, alias="QQMC_TOPLIST_SCHEDULE_START_HOUR", ge=0, le=23
    )
    qqmc_toplist_interval_minutes: int = Field(
        default=20, alias="QQMC_TOPLIST_INTERVAL_MINUTES", ge=1, le=60
    )
    qqmc_new_song_update_interval_sec: int = Field(
        default=60, alias="QQMC_NEW_SONG_UPDATE_INTERVAL_SEC", ge=10
    )
    qqmc_crawl_track_artist_name: str = Field(
        default="李宇春", alias="QQMC_CRAWL_TRACK_ARTIST_NAME"
    )
    qqmc_crawl_track_interval_minutes: int = Field(
        default=30, alias="QQMC_CRAWL_TRACK_INTERVAL_MINUTES", ge=1, le=120
    )
    qqmc_default_topsongs_artist_name: str = Field(
        default="", alias="QQMC_DEFAULT_TOPSONGS_ARTIST_NAME"
    )

    @property
    def effective_default_topsongs_artist(self) -> str:
        """首页 TOP N 默认歌手：未单独配置时与榜单定时歌手一致。"""
        t = (self.qqmc_default_topsongs_artist_name or "").strip()
        return t or (self.qqmc_toplist_artist_name or "").strip() or "李宇春"


settings = Settings()
