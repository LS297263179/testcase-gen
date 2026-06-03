"""数据库模块 - SQLite 持久化：用户 + 历史记录 + 偏好规则"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

_DB_PATH = "data.db"


def set_db_path(path: str):
    global _DB_PATH
    _DB_PATH = path


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """建表（幂等，可重复调用）"""
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            user_id       INTEGER REFERENCES users(id),
            requirement   TEXT NOT NULL,
            priority      TEXT,
            case_types    TEXT,
            testcases     TEXT NOT NULL,
            tc_count      INTEGER NOT NULL DEFAULT 0,
            review_report TEXT,
            is_deleted    INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS session_images (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id),
            filename   TEXT,
            media_type TEXT,
            data       TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS preferences (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            category    TEXT NOT NULL,
            pattern     TEXT NOT NULL,
            source_diff TEXT,
            weight      REAL NOT NULL DEFAULT 1.0,
            active      INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS preference_links (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            preference_id INTEGER NOT NULL REFERENCES preferences(id),
            session_id    INTEGER NOT NULL REFERENCES sessions(id),
            testcase_id   TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    # 兼容已有数据库：为 sessions 表添加 user_id 列
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER REFERENCES users(id)")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 列已存在
    conn.close()


# ============================================================
# Users CRUD
# ============================================================

def create_user(username: str, password: str) -> int:
    """注册新用户，返回 user_id"""
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        user_id = cur.lastrowid
        conn.commit()
        return user_id
    except sqlite3.IntegrityError:
        raise ValueError("用户名已存在")
    finally:
        conn.close()


def verify_user(username: str, password: str) -> dict | None:
    """验证用户名密码，成功返回 {"id": ..., "username": ...}，失败返回 None"""
    conn = _conn()
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()
    if row and check_password_hash(row["password_hash"], password):
        return {"id": row["id"], "username": row["username"]}
    return None


def get_user_by_id(user_id: int) -> dict | None:
    """根据 ID 获取用户信息"""
    conn = _conn()
    row = conn.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ============================================================
# Sessions CRUD
# ============================================================

def create_session(requirement: str, testcases: list[dict],
                   priority: str | None = None,
                   case_types: list[str] | None = None,
                   images: list[dict] | None = None,
                   review_report: str | None = None,
                   user_id: int | None = None) -> int:
    """保存一次生成记录，返回 session_id"""
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO sessions (user_id, requirement, priority, case_types, testcases, tc_count, review_report) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            requirement,
            priority,
            json.dumps(case_types, ensure_ascii=False) if case_types else None,
            json.dumps(testcases, ensure_ascii=False),
            len(testcases),
            review_report,
        ),
    )
    session_id = cur.lastrowid

    if images:
        for i, img in enumerate(images):
            conn.execute(
                "INSERT INTO session_images (session_id, filename, media_type, data, sort_order) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, img.get("filename"), img.get("media_type"), img["data"], i),
            )

    conn.commit()
    conn.close()
    return session_id


def get_session(session_id: int) -> dict | None:
    """获取单条历史记录（含图片）"""
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM sessions WHERE id = ? AND is_deleted = 0", (session_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None

    session = dict(row)
    session["testcases"] = json.loads(session["testcases"])
    if session["case_types"]:
        session["case_types"] = json.loads(session["case_types"])

    images = conn.execute(
        "SELECT filename, media_type, data FROM session_images "
        "WHERE session_id = ? ORDER BY sort_order", (session_id,)
    ).fetchall()
    session["images"] = [dict(img) for img in images]

    conn.close()
    return session


def list_sessions(limit: int = 50, offset: int = 0,
                  user_id: int | None = None) -> list[dict]:
    """列出历史记录摘要（不含 testcases 和 image data）"""
    conn = _conn()
    if user_id is not None:
        rows = conn.execute(
            "SELECT id, created_at, requirement, priority, tc_count, is_deleted "
            "FROM sessions WHERE is_deleted = 0 AND user_id = ? "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, created_at, requirement, priority, tc_count, is_deleted "
            "FROM sessions WHERE is_deleted = 0 "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    conn.close()
    result = []
    for row in rows:
        s = dict(row)
        s["requirement_preview"] = s["requirement"][:80]
        result.append(s)
    return result


def save_review(session_id: int, review_report: str):
    """为已有 session 保存评审报告"""
    conn = _conn()
    conn.execute(
        "UPDATE sessions SET review_report = ? WHERE id = ?",
        (review_report, session_id),
    )
    conn.commit()
    conn.close()


def delete_session(session_id: int):
    """软删除"""
    conn = _conn()
    conn.execute("UPDATE sessions SET is_deleted = 1 WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()


# ============================================================
# Preferences CRUD
# ============================================================

def get_active_preferences(limit: int = 10) -> list[dict]:
    """获取活跃偏好规则，按权重降序"""
    conn = _conn()
    rows = conn.execute(
        "SELECT id, category, pattern, weight FROM preferences "
        "WHERE active = 1 ORDER BY weight DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_preference_context(max_prefs: int = 10) -> str:
    """格式化偏好规则为 prompt 注入文本"""
    prefs = get_active_preferences(limit=max_prefs)
    if not prefs:
        return ""
    lines = [f"- [{p['category']}] {p['pattern']}" for p in prefs]
    return "\n".join(lines)


def save_preferences(preferences: list[dict], session_id: int,
                     source_diffs: list[dict] | None = None):
    """保存新偏好规则，同时衰减同 category 的旧规则"""
    conn = _conn()
    for i, pref in enumerate(preferences):
        category = pref["category"]
        # 衰减同 category 旧规则
        conn.execute(
            "UPDATE preferences SET weight = weight * 0.8 WHERE category = ? AND active = 1",
            (category,),
        )
        # 停用低权重规则
        conn.execute(
            "UPDATE preferences SET active = 0 WHERE weight < 0.2 AND active = 1",
        )
        # 插入新规则
        source_diff = json.dumps(source_diffs[i], ensure_ascii=False) if source_diffs and i < len(source_diffs) else None
        cur = conn.execute(
            "INSERT INTO preferences (category, pattern, source_diff, weight) VALUES (?, ?, ?, 1.0)",
            (category, pref["pattern"], source_diff),
        )
        pref_id = cur.lastrowid
        # 关联记录
        conn.execute(
            "INSERT INTO preference_links (preference_id, session_id) VALUES (?, ?)",
            (pref_id, session_id),
        )

    conn.commit()
    conn.close()


def update_preference(pref_id: int, active: int | None = None, pattern: str | None = None):
    """更新偏好规则（启用/禁用/修改）"""
    conn = _conn()
    if active is not None:
        conn.execute("UPDATE preferences SET active = ? WHERE id = ?", (active, pref_id))
    if pattern is not None:
        conn.execute("UPDATE preferences SET pattern = ? WHERE id = ?", (pattern, pref_id))
    conn.commit()
    conn.close()


def delete_preference(pref_id: int):
    """硬删除偏好规则"""
    conn = _conn()
    conn.execute("DELETE FROM preference_links WHERE preference_id = ?", (pref_id,))
    conn.execute("DELETE FROM preferences WHERE id = ?", (pref_id,))
    conn.commit()
    conn.close()


def list_all_preferences() -> list[dict]:
    """列出所有偏好（含停用的），用于管理面板"""
    conn = _conn()
    rows = conn.execute(
        "SELECT id, created_at, category, pattern, weight, active FROM preferences ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# Settings (key-value)
# ============================================================

def get_setting(key: str) -> str | None:
    """获取单个设置值"""
    conn = _conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row:
        return row["value"]
    return None


def set_setting(key: str, value: str):
    """写入/更新设置"""
    conn = _conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_model_config() -> dict:
    """获取模型配置（优先数据库，fallback 到 config.yaml）"""
    import yaml
    raw = get_setting("model_config")
    if raw:
        return json.loads(raw)
    # fallback
    try:
        with open("config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return {
            "generate": cfg.get("generate", {}),
            "review": cfg.get("review", {}),
        }
    except Exception:
        return {}


def save_model_config(config: dict):
    """保存模型配置到数据库"""
    set_setting("model_config", json.dumps(config, ensure_ascii=False))
