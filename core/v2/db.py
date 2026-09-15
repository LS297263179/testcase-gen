"""V2 数据库连接管理 - 独立 data_v2.db，与 V1 完全隔离。

沿用 V1 的连接策略（每次操作独立连接 + WAL + foreign_keys + 写锁），
但指向独立的 V2 数据库文件，保证 V1 运行时零影响、V2 可整体回滚（删文件）。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# V2 独立数据库路径（默认 data/data_v2.db）
_V2_DB_PATH = str(Path(__file__).parent.parent.parent / "data" / "data_v2.db")
_write_lock = threading.Lock()  # 写操作互斥，防止 SQLite 写冲突


def set_v2_db_path(path: str) -> None:
    """设置 V2 数据库路径（测试/迁移用）"""
    global _V2_DB_PATH
    _V2_DB_PATH = path


def get_v2_db_path() -> str:
    return _V2_DB_PATH


def _new_conn() -> sqlite3.Connection:
    """创建新连接（row_factory=Row，开启外键约束与 WAL）"""
    Path(_V2_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_V2_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def v2_conn() -> Iterator[sqlite3.Connection]:
    """写操作上下文（独立连接 + 写锁 + 自动提交/回滚/关闭）"""
    conn = _new_conn()
    with _write_lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


@contextmanager
def v2_read_conn() -> Iterator[sqlite3.Connection]:
    """读操作上下文（独立连接，自动关闭）"""
    conn = _new_conn()
    try:
        yield conn
    finally:
        conn.close()
