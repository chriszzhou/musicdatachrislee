from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import SUPPORTED_PLATFORMS, get_platform_meta

def get_milestone_logs(base_dir: Optional[Path] = None, limit: int = 500) -> Dict[str, Any]:
    """
    读取三平台收藏量里程碑日志，按时间倒序合并返回。
    日志行格式：YYYY-MM-DD HH:MM:SS 歌曲名 收藏量
    """
    root = base_dir or Path(".")
    entries: List[Dict[str, Any]] = []
    for platform in SUPPORTED_PLATFORMS:
        meta = get_platform_meta(platform)
        log_path = root / Path(meta["changes_db"]).parent / "milestone_{}.log".format(platform)
        if not log_path.is_file():
            continue
        name = meta.get("name", platform)
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    try:
                        ts = "{} {}".format(parts[0], parts[1])
                        count = int(parts[-1])
                        song_name = " ".join(parts[2:-1]) if len(parts) > 3 else parts[2]
                        entries.append(
                            {"platform": platform, "platform_name": name, "time": ts, "song_name": song_name, "favorite_count": count}
                        )
                    except (ValueError, IndexError):
                        continue
        except OSError:
            continue
    entries.sort(key=lambda x: x["time"], reverse=True)
    return {"ok": True, "entries": entries[:limit]}


def delete_milestone_entry(
    platform: str,
    time_str: str,
    song_name: str,
    favorite_count: int,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    从指定平台的里程碑 log 中删除一条记录（完全匹配：时间 + 歌曲名 + 收藏量）。
    日志行格式：YYYY-MM-DD HH:MM:SS 歌曲名 收藏量
    """
    root = (base_dir or Path(".")).resolve()
    meta = get_platform_meta(platform)
    log_path = root / Path(meta["changes_db"]).parent / "milestone_{}.log".format(platform)
    if not log_path.is_file():
        return {"ok": False, "error": "未找到日志文件: {}".format(log_path)}

    count_str = str(favorite_count)
    removed = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        kept = []
        for line in lines:
            raw = line.rstrip("\n\r")
            s = raw.strip()
            if not s:
                kept.append(line)
                continue
            parts = s.split()
            if len(parts) < 3:
                kept.append(line)
                continue
            line_time = "{} {}".format(parts[0], parts[1])
            line_count = parts[-1]
            line_song = " ".join(parts[2:-1]) if len(parts) > 3 else parts[2]
            if line_time == time_str and line_count == count_str and line_song == song_name:
                removed.append(raw)
                continue
            kept.append(line)
        if not removed:
            return {"ok": False, "error": "未找到匹配的记录"}
        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except OSError as e:
        return {"ok": False, "error": "读写日志失败: {}".format(e)}
    return {"ok": True, "removed": len(removed), "message": "已删除 1 条里程碑记录"}


def remove_milestone_outliers(
    platform: str,
    base_dir: Optional[Path] = None,
    threshold: int = 100,
) -> Dict[str, Any]:
    """
    剔除异常数据：对指定平台的变化表做「n-1 与 n+1 接近、n 异常」的修正，
    并删除里程碑 log 中对应异常收藏量记录。
    实现见包内 metric_outlier_correction.run()，三平台共用同一套表结构。
    """
    from ..metric_outlier_correction import run as run_outlier_correction

    root = (base_dir or Path(".")).resolve()
    meta = get_platform_meta(platform)
    changes_db = root / meta["changes_db"]
    if not changes_db.is_file():
        return {"ok": False, "error": "未找到变化库: {}".format(changes_db)}

    result = run_outlier_correction(
        changes_db=changes_db,
        threshold=threshold,
        method="neighbor",
        dry_run=False,
        fix_snapshot=False,
        repo_root=root,
    )
    if result.get("error"):
        return {"ok": False, "error": result["error"]}
    return {
        "ok": True,
        "updated": result.get("updated", 0),
        "removed_log_lines": result.get("removed_log_lines", 0),
        "message": "已修正 {} 条变化表记录，并从里程碑 log 中删除 {} 条异常记录。".format(
            result.get("updated", 0),
            result.get("removed_log_lines", 0),
        ),
    }

