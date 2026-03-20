"""
项目内 SQLite 统一打开策略：长等待锁、可选 WAL，减轻 Web 与后台线程并发读写时的冲突。

可通过环境变量调整（与 schedulers 一致使用 QQMC_ 前缀）：
- QQMC_SQLITE_CONNECT_TIMEOUT — connect(..., timeout=秒)，默认 30
- QQMC_SQLITE_BUSY_TIMEOUT_MS — PRAGMA busy_timeout（毫秒），默认 60000
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional, Union

PathLike = Union[str, Path]


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


DEFAULT_CONNECT_TIMEOUT_SEC = 30.0
DEFAULT_BUSY_TIMEOUT_MS = 60_000

CONNECT_TIMEOUT = max(1.0, _float_env("QQMC_SQLITE_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT_SEC))
BUSY_TIMEOUT_MS = max(100, _int_env("QQMC_SQLITE_BUSY_TIMEOUT_MS", DEFAULT_BUSY_TIMEOUT_MS))


def apply_pragmas_to_dbapi_connection(
    dbapi_connection: Any,
    *,
    busy_timeout_ms: Optional[int] = None,
    enable_wal: bool = True,
) -> None:
    """
    供 SQLAlchemy「connect」事件使用：对底层 pysqlite 连接设置 busy_timeout / WAL。
    """
    ms = BUSY_TIMEOUT_MS if busy_timeout_ms is None else busy_timeout_ms
    cur = dbapi_connection.cursor()
    try:
        cur.execute("PRAGMA busy_timeout={}".format(int(ms)))
        if enable_wal:
            try:
                cur.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
    finally:
        cur.close()


def apply_pragmas_to_connection(
    conn: sqlite3.Connection,
    *,
    busy_timeout_ms: Optional[int] = None,
    enable_wal: bool = True,
) -> None:
    """对已有 sqlite3.Connection 设置与 connect_sqlite 相同的 PRAGMA。"""
    apply_pragmas_to_dbapi_connection(conn, busy_timeout_ms=busy_timeout_ms, enable_wal=enable_wal)


def connect_sqlite(
    database: PathLike,
    *,
    timeout: Optional[float] = None,
    busy_timeout_ms: Optional[int] = None,
    row_factory: Any = None,
    enable_wal: bool = True,
) -> sqlite3.Connection:
    """
    打开 SQLite 文件，使用项目统一超时与 WAL（默认）。

    isolation_level 等行为与标准库默认一致，不改变事务语义。
    """
    path = database if isinstance(database, Path) else Path(database)
    t = CONNECT_TIMEOUT if timeout is None else max(0.5, float(timeout))
    conn = sqlite3.connect(str(path), timeout=t)
    apply_pragmas_to_connection(conn, busy_timeout_ms=busy_timeout_ms, enable_wal=enable_wal)
    if row_factory is not None:
        conn.row_factory = row_factory
    return conn
