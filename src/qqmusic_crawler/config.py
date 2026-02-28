from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
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


settings = Settings()
