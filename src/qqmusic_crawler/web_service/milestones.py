from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .paths import SUPPORTED_PLATFORMS, get_platform_meta

# 与 tracking._favorite_milestone_should_log 一致：只保留「记录的收藏量 >= 该值」的行（剔除旧规则下低于 1 万档的记录）
MILESTONE_LOG_MIN_RECORDED_FAVORITE = 10_000


def prune_milestone_logs_sub_10k_entries(
    base_dir: Optional[Path] = None,
    min_recorded_favorite: int = MILESTONE_LOG_MIN_RECORDED_FAVORITE,
) -> Dict[str, Any]:
    """
    重写各平台 milestone_*.log：删除「行末收藏量 < min_recorded_favorite」的行。
    用于剔除旧规则（如增量>1000、QQ 5k 档、网易/酷狗 1k/5k 档）产生的记录；新规则下首次有效记录至少为跨越 1 万档后的值，故必然 >= 10000。
    """
    root = (base_dir or Path(".")).resolve()
    min_fav = max(1, int(min_recorded_favorite))
    total_removed = 0
    per_platform: Dict[str, int] = {}
    for platform in SUPPORTED_PLATFORMS:
        meta = get_platform_meta(platform)
        log_path = root / Path(meta["changes_db"]).parent / "milestone_{}.log".format(platform)
        if not log_path.is_file():
            continue
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            logger.warning("读取里程碑日志失败 {}: {}", log_path, e)
            continue
        kept: List[str] = []
        removed_here = 0
        for line in lines:
            s = line.strip()
            if not s:
                kept.append(line)
                continue
            parts = s.split()
            if len(parts) < 3:
                kept.append(line)
                continue
            try:
                count = int(parts[-1])
            except ValueError:
                kept.append(line)
                continue
            if count < min_fav:
                removed_here += 1
                continue
            kept.append(line)
        if removed_here:
            try:
                with open(log_path, "w", encoding="utf-8") as f:
                    f.writelines(kept)
            except OSError as e:
                logger.warning("写回里程碑日志失败 {}: {}", log_path, e)
                continue
        per_platform[platform] = removed_here
        total_removed += removed_here
    if total_removed:
        logger.info("里程碑日志清理：共删除 {} 条（各平台 {}）", total_removed, per_platform)
    return {"ok": True, "removed": total_removed, "per_platform": per_platform}


def get_milestone_logs(base_dir: Optional[Path] = None, limit: int = 500) -> Dict[str, Any]:
    """
    读取三平台收藏量里程碑日志。

    - by_platform：各平台独立列表，按时间升序（从早到晚）；每平台最多 ``limit`` 条（取时间最近的 limit 条再保持升序）。
    - entries：兼容旧前端，全平台合并后按时间倒序，共返回最多 ``limit`` 条。

    日志行格式：YYYY-MM-DD HH:MM:SS 歌曲名 收藏量
    """
    root = base_dir or Path(".")
    per_cap = max(1, int(limit))
    by_platform: Dict[str, List[Dict[str, Any]]] = {p: [] for p in SUPPORTED_PLATFORMS}

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
                        by_platform[platform].append(
                            {
                                "platform": platform,
                                "platform_name": name,
                                "time": ts,
                                "song_name": song_name,
                                "favorite_count": count,
                            }
                        )
                    except (ValueError, IndexError):
                        continue
        except OSError:
            continue

    out_by_platform: Dict[str, List[Dict[str, Any]]] = {}
    merged: List[Dict[str, Any]] = []
    for platform in SUPPORTED_PLATFORMS:
        lst = sorted(by_platform[platform], key=lambda x: x["time"])
        if len(lst) > per_cap:
            lst = lst[-per_cap:]
        out_by_platform[platform] = lst
        merged.extend(lst)

    merged.sort(key=lambda x: x["time"], reverse=True)
    return {
        "ok": True,
        "by_platform": out_by_platform,
        "entries": merged[:per_cap],
    }


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


def run_kugou_outlier_correction_until_clean(
    base_dir: Optional[Path] = None,
    threshold: int = 100,
    max_rounds: int = 50,
) -> Dict[str, Any]:
    """
    酷狗专用：反复执行异常修正，直到一轮内「变化表修正条数 + 里程碑 log 删除条数」均为 0，
    或达到 max_rounds（防止异常数据互相牵连时死循环）。
    """
    total_updated = 0
    total_removed = 0
    rounds = 0
    last_error: Optional[str] = None
    for _ in range(max(1, max_rounds)):
        r = remove_milestone_outliers("kugou", base_dir=base_dir, threshold=threshold)
        if not r.get("ok"):
            last_error = str(r.get("error") or "未知错误")
            return {
                "ok": False,
                "error": last_error,
                "rounds": rounds,
                "total_updated": total_updated,
                "total_removed_log_lines": total_removed,
            }
        u = int(r.get("updated") or 0)
        rm = int(r.get("removed_log_lines") or 0)
        total_updated += u
        total_removed += rm
        rounds += 1
        if u == 0 and rm == 0:
            break
    return {
        "ok": True,
        "rounds": rounds,
        "total_updated": total_updated,
        "total_removed_log_lines": total_removed,
    }

