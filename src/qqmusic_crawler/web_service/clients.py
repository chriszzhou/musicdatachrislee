from __future__ import annotations

from typing import Any, Optional, Tuple

from ..client import QQMusicClient
from ..config import settings
from ..crawler import CrawlerService
from ..kugou_client import KugouMusicClient
from ..netease_client import NeteaseMusicClient
from ..storage import Storage

from .paths import get_platform_meta, normalize_platform

def build_client(platform: str) -> Any:
    p = normalize_platform(platform)
    if p == "netease":
        return NeteaseMusicClient(
            base_url=settings.netease_base_url,
            timeout=settings.qqmusic_timeout,
            max_retries=settings.qqmusic_max_retries,
            rate_limit_qps=settings.netease_rate_limit_qps,
            metric_workers=settings.netease_metric_workers,
            metric_batch_size=settings.netease_metric_batch_size,
        )
    if p == "kugou":
        return KugouMusicClient(
            base_url=settings.kugou_base_url,
            timeout=settings.qqmusic_timeout,
            max_retries=settings.qqmusic_max_retries,
            rate_limit_qps=settings.kugou_rate_limit_qps,
            metric_workers=settings.kugou_metric_workers,
            metric_batch_size=settings.kugou_metric_batch_size,
        )
    return QQMusicClient(
        base_url=settings.qqmusic_base_url,
        timeout=settings.qqmusic_timeout,
        max_retries=settings.qqmusic_max_retries,
        rate_limit_qps=settings.qqmusic_rate_limit_qps,
    )


def _build_snapshot_service(platform: str, database_url: str) -> Tuple[Any, CrawlerService]:
    storage = Storage(database_url)
    storage.create_tables()
    client = build_client(platform)
    return client, CrawlerService(client=client, storage=storage)


def _resolve_artist(service: CrawlerService, artist_name: str) -> Optional[Tuple[str, str]]:
    name = (artist_name or "").strip()
    if not name:
        return None
    candidates = service.find_artist_candidates_by_name(
        keyword=name,
        max_pages=8,
        page_size=settings.qqmusic_default_artist_page_size,
    )
    if not candidates:
        return None
    first = candidates[0]
    return first["artist_mid"], first["name"]

