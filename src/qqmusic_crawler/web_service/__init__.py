"""Web 层业务逻辑（由旧版单文件 web_service.py 拆分为子模块）。"""

from .crawl_ops import crawl_track
from .milestones import (
    delete_milestone_entry,
    get_milestone_logs,
    remove_milestone_outliers,
)
from .new_song import (
    get_new_song_chart_data,
    get_new_song_current_metrics,
    get_new_song_toplist_rows,
    update_new_song_one_platform,
)
from .paths import (
    SUPPORTED_PLATFORMS,
    get_platform_meta,
    normalize_platform,
    prune_old_snapshots,
    resolve_data_paths_for_debug,
)
from .reporting import get_report, get_report_chart_data, get_reports_all_platforms
from .search_top import (
    get_artist_snapshot_metrics_all_platforms,
    get_top_songs,
    get_top_songs_slice,
    search_songs,
    search_songs_all_platforms,
)
from .toplist_ops import check_artist_toplist, get_today_toplist_from_platform_dbs

__all__ = [
    "SUPPORTED_PLATFORMS",
    "check_artist_toplist",
    "crawl_track",
    "delete_milestone_entry",
    "get_milestone_logs",
    "get_new_song_chart_data",
    "get_new_song_current_metrics",
    "get_new_song_toplist_rows",
    "get_artist_snapshot_metrics_all_platforms",
    "get_platform_meta",
    "get_report",
    "get_report_chart_data",
    "get_reports_all_platforms",
    "get_today_toplist_from_platform_dbs",
    "get_top_songs",
    "get_top_songs_slice",
    "normalize_platform",
    "prune_old_snapshots",
    "remove_milestone_outliers",
    "search_songs",
    "search_songs_all_platforms",
    "resolve_data_paths_for_debug",
    "update_new_song_one_platform",
]
