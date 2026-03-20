from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SUPPORTED_PLATFORMS = ("qq", "netease", "kugou")

PLATFORM_CONFIG: Dict[str, Dict[str, str]] = {
    "qq": {
        "name": "QQ音乐",
        "snapshots_dir": "data/snapshots",
        "snapshot_prefix": "qqmusic",
        "changes_db": "data/qqmusic_changes.db",
        "toplist_db": "data/qqmusic_toplist.db",
    },
    "netease": {
        "name": "网易云音乐",
        "snapshots_dir": "data/netease_snapshots",
        "snapshot_prefix": "netease",
        "changes_db": "data/netease_changes.db",
        "toplist_db": "data/netease_toplist.db",
    },
    "kugou": {
        "name": "酷狗音乐",
        "snapshots_dir": "data/kugou_snapshots",
        "snapshot_prefix": "kugou",
        "changes_db": "data/kugou_changes.db",
        "toplist_db": "data/kugou_toplist.db",
    },
}


def normalize_platform(platform: str) -> str:
    p = (platform or "").strip().lower()
    if p not in SUPPORTED_PLATFORMS:
        return "qq"
    return p


def get_platform_meta(platform: str) -> Dict[str, str]:
    return PLATFORM_CONFIG[normalize_platform(platform)]


def _resolve_changes_db_path(platform: str, base_dir: Optional[Path] = None) -> Path:
    """解析变化库绝对路径：先试 base_dir（项目根），再试 cwd，返回第一个存在的路径。"""
    meta = get_platform_meta(platform)
    rel = meta["changes_db"]
    for root in [(base_dir or Path(".")).resolve(), Path.cwd().resolve()]:
        p = root / rel
        if p.is_file():
            return p
    return (base_dir or Path(".")).resolve() / rel


def _resolve_snapshots_dir(platform: str, base_dir: Optional[Path] = None) -> Path:
    """解析快照目录绝对路径：先试 base_dir（项目根）下存在则用，再试 cwd，否则返回 base_dir 下路径。"""
    meta = get_platform_meta(platform)
    rel = meta["snapshots_dir"]
    for root in [(base_dir or Path(".")).resolve(), Path.cwd().resolve()]:
        p = root / rel
        if p.is_dir():
            return p
    return (base_dir or Path(".")).resolve() / rel


def _resolve_toplist_db_path(platform: str, base_dir: Optional[Path] = None) -> Path:
    """解析榜单库绝对路径：先试 base_dir（项目根），再试 cwd，返回第一个存在的路径。"""
    meta = get_platform_meta(platform)
    rel = meta["toplist_db"]
    for root in [(base_dir or Path(".")).resolve(), Path.cwd().resolve()]:
        p = root / rel
        if p.is_file():
            return p
    return (base_dir or Path(".")).resolve() / rel


def resolve_data_paths_for_debug(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """返回各平台解析后的 data 路径，用于排查路径问题。"""
    root = base_dir or Path(".")
    out: Dict[str, Any] = {
        "cwd": str(Path.cwd().resolve()),
        "base_dir": str(root.resolve()),
        "platforms": {},
    }
    for platform in SUPPORTED_PLATFORMS:
        changes = _resolve_changes_db_path(platform, base_dir)
        snapshots = _resolve_snapshots_dir(platform, base_dir)
        toplist = _resolve_toplist_db_path(platform, base_dir)
        out["platforms"][platform] = {
            "changes_db": str(changes),
            "changes_db_exists": changes.is_file(),
            "snapshots_dir": str(snapshots),
            "snapshots_dir_exists": snapshots.is_dir(),
            "toplist_db": str(toplist),
            "toplist_db_exists": toplist.is_file(),
        }
    return out


def _snapshot_date_key(path: Path) -> str:
    """从快照文件名或 mtime 得到日期键 YYYYMMDD。文件名格式：prefix_mid_YYYYMMDD_HHMMSS.db"""
    stem = path.stem
    parts = stem.split("_")
    if len(parts) >= 2 and len(parts[-2]) == 8 and parts[-2].isdigit():
        return parts[-2]
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d")


def prune_old_snapshots(platform: str, keep_per_day: int = 1, base_dir: Optional[Path] = None) -> int:
    """
    按日期分组：每个自然日（如 20260227、20260228）只保留 keep_per_day 个快照 DB（默认 1 个），
    同一天多个时保留 mtime 最新的，其余删除。不删除 changes_db / toplist_db。
    返回删除的文件数量。
    """
    meta = get_platform_meta(platform)
    root = base_dir or Path(".")
    snapshots_dir = root / meta["snapshots_dir"]
    prefix = meta["snapshot_prefix"]
    if not snapshots_dir.is_dir():
        return 0
    pattern = "{}_*.db".format(prefix)
    all_files = list(snapshots_dir.glob(pattern))
    by_date: Dict[str, List[Path]] = {}
    for p in all_files:
        try:
            date_key = _snapshot_date_key(p)
            by_date.setdefault(date_key, []).append(p)
        except OSError:
            continue
    to_delete: List[Path] = []
    for date_key, paths in by_date.items():
        sorted_paths = sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
        to_delete.extend(sorted_paths[keep_per_day:])
    deleted = 0
    for p in to_delete:
        try:
            p.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted

