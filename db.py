"""数据库模块 - SQLite 持久化：用户 + 历史记录 + 偏好规则 + API Key 加密"""

import base64
import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


# ============================================================
# API Key 加密/解密（基于 Fernet 对称加密）
# ============================================================

def _get_fernet_key() -> bytes:
    """从环境变量或配置文件获取/生成加密密钥"""
    # 优先从环境变量获取
    key = os.environ.get("FERNET_KEY", "")
    if key:
        return key.encode() if isinstance(key, str) else key

    # 从 settings 表获取或生成
    stored = get_setting("_fernet_key")
    if stored:
        return stored.encode()

    # 生成新密钥并保存
    try:
        from cryptography.fernet import Fernet
        new_key = Fernet.generate_key().decode()
    except ImportError:
        # cryptography 未安装时，使用基于 secret_key 的确定性密钥（32 字节 base64）
        import yaml
        cfg_path = Path(__file__).parent / "config.yaml"
        secret = ""
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                secret = yaml.safe_load(f).get("secret_key", "")
        if not secret:
            secret = os.environ.get("FLASK_SECRET_KEY", "default-key-change-me")
        # 确保生成合法的 32 字节 URL-safe base64 密钥
        derived = hashlib.sha256(secret.encode()).digest()
        new_key = base64.urlsafe_b64encode(derived).decode()

    set_setting("_fernet_key", new_key)
    return new_key.encode()


def encrypt_api_key(plaintext: str) -> str:
    """加密 API Key。如果已加密（以 gAAAAA 开头）或为空则直接返回"""
    if not plaintext or plaintext.startswith("gAAAAA"):
        return plaintext
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_get_fernet_key())
        return f.encrypt(plaintext.encode()).decode()
    except ImportError:
        # cryptography 未安装时不做加密，返回原文
        print("[WARN] cryptography 未安装，API Key 将以明文存储。建议: pip install cryptography")
        return plaintext
    except Exception:
        return plaintext  # 加密失败时返回原文，不阻断流程


def decrypt_api_key(ciphertext: str) -> str:
    """解密 API Key。如果不是加密格式则直接返回（向后兼容）"""
    if not ciphertext or not ciphertext.startswith("gAAAAA"):
        return ciphertext
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_get_fernet_key())
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        return ciphertext  # 解密失败时返回原文（可能是旧的明文 key）

_DB_PATH = str(Path(__file__).parent / "data" / "data.db")
_conn_lock = threading.Lock()
_write_lock = threading.Lock()  # 写操作互斥锁，防止 SQLite 写冲突
_conn_instance: sqlite3.Connection | None = None


def set_db_path(path: str):
    global _DB_PATH, _conn_instance
    with _conn_lock:
        _DB_PATH = path
        _conn_instance = None  # 重置连接，下次使用时重新创建


def _get_conn() -> sqlite3.Connection:
    """获取模块级单例连接（线程安全，惰性初始化）"""
    global _conn_instance
    if _conn_instance is None:
        with _conn_lock:
            if _conn_instance is None:
                Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
                _conn_instance = sqlite3.connect(
                    _DB_PATH, check_same_thread=False,
                    timeout=30,  # 等待锁释放的超时时间
                )
                _conn_instance.row_factory = sqlite3.Row
                _conn_instance.execute("PRAGMA journal_mode=WAL")
                _conn_instance.execute("PRAGMA foreign_keys=ON")
                _conn_instance.execute("PRAGMA busy_timeout=10000")  # 10s 忙等待
    return _conn_instance


@contextmanager
def db_conn():
    """数据库上下文管理器，自动处理 commit/rollback（写操作互斥）"""
    conn = _get_conn()
    with _write_lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


@contextmanager
def db_read_conn():
    """只读上下文管理器（不需要写锁，允许并发读）"""
    conn = _get_conn()
    try:
        yield conn
    except Exception:
        raise


def init_db():
    """建表（幂等，可重复调用）"""
    with db_conn() as conn:
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

            CREATE INDEX IF NOT EXISTS idx_sessions_user_deleted
                ON sessions(user_id, is_deleted);

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

            CREATE TABLE IF NOT EXISTS materials (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER REFERENCES users(id),
                title      TEXT NOT NULL,
                content    TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_materials_user
                ON materials(user_id);

            CREATE TABLE IF NOT EXISTS material_images (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                material_id INTEGER NOT NULL REFERENCES materials(id),
                filename    TEXT,
                media_type  TEXT,
                data        TEXT NOT NULL,
                sort_order  INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS test_points (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER REFERENCES users(id),
                title       TEXT NOT NULL,
                requirement TEXT,
                points      TEXT NOT NULL,
                total       INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_test_points_user
                ON test_points(user_id);
        """)
        # 兼容已有数据库：为 sessions 表添加 user_id 列
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER REFERENCES users(id)")
        except sqlite3.OperationalError:
            pass  # 列已存在

        # 兼容已有数据库：为 preferences 表添加 user_id 列
        try:
            conn.execute("ALTER TABLE preferences ADD COLUMN user_id INTEGER REFERENCES users(id)")
        except sqlite3.OperationalError:
            pass  # 列已存在


# ============================================================
# Users CRUD
# ============================================================

def create_user(username: str, password: str) -> int:
    """注册新用户，返回 user_id"""
    with db_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password)),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("用户名已存在")


def verify_user(username: str, password: str) -> dict | None:
    """验证用户名密码，成功返回 {"id": ..., "username": ...}，失败返回 None"""
    with db_read_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if row and check_password_hash(row["password_hash"], password):
        return {"id": row["id"], "username": row["username"]}
    return None


def get_user_by_id(user_id: int) -> dict | None:
    """根据 ID 获取用户信息"""
    with db_read_conn() as conn:
        row = conn.execute(
            "SELECT id, username, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
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
    with db_conn() as conn:
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

    return session_id


def get_session(session_id: int) -> dict | None:
    """获取单条历史记录（含图片）"""
    with db_read_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ? AND is_deleted = 0", (session_id,)
        ).fetchone()
        if not row:
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

    return session


def list_sessions(limit: int = 50, offset: int = 0,
                  user_id: int | None = None) -> list[dict]:
    """列出历史记录摘要（不含 testcases 和 image data）"""
    with db_read_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT id, created_at, requirement, priority, tc_count "
                "FROM sessions WHERE is_deleted = 0 AND user_id = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, created_at, requirement, priority, tc_count "
                "FROM sessions WHERE is_deleted = 0 "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) | {"requirement_preview": (r["requirement"] or "")[:80]} for r in rows]


def save_review(session_id: int, review_report: str):
    """为已有 session 保存评审报告"""
    with db_conn() as conn:
        conn.execute(
            "UPDATE sessions SET review_report = ? WHERE id = ?",
            (review_report, session_id),
        )


def delete_session(session_id: int):
    """软删除"""
    with db_conn() as conn:
        conn.execute("UPDATE sessions SET is_deleted = 1 WHERE id = ?", (session_id,))


# ============================================================
# Preferences CRUD
# ============================================================

def get_active_preferences(limit: int = 10, user_id: int | None = None) -> list[dict]:
    """获取活跃偏好规则，按权重降序。user_id 不为 None 时只返回该用户的偏好"""
    with db_read_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT id, category, pattern, weight FROM preferences "
                "WHERE active = 1 AND (user_id = ? OR user_id IS NULL) "
                "ORDER BY weight DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, category, pattern, weight FROM preferences "
                "WHERE active = 1 ORDER BY weight DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_preference_context(max_prefs: int = 10, user_id: int | None = None) -> str:
    """格式化偏好规则为 prompt 注入文本"""
    prefs = get_active_preferences(limit=max_prefs, user_id=user_id)
    if not prefs:
        return ""
    lines = [f"- [{p['category']}] {p['pattern']}" for p in prefs]
    return "\n".join(lines)


def save_preferences(preferences: list[dict], session_id: int,
                     source_diffs: list[dict] | None = None,
                     user_id: int | None = None):
    """保存新偏好规则，同时衰减同 category 的旧规则"""
    with db_conn() as conn:
        # 如果未指定 user_id，从 session 中获取
        if user_id is None:
            row = conn.execute("SELECT user_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                user_id = row["user_id"]

        for i, pref in enumerate(preferences):
            category = pref["category"]
            # 衰减同 category 旧规则（仅在同用户范围内，避免影响其他用户）
            if user_id is not None:
                conn.execute(
                    "UPDATE preferences SET weight = weight * 0.8 WHERE category = ? AND active = 1 AND user_id = ?",
                    (category, user_id),
                )
                conn.execute(
                    "UPDATE preferences SET active = 0 WHERE weight < 0.2 AND active = 1 AND user_id = ?",
                    (user_id,),
                )
            # user_id 为 None 时不执行衰减，避免误操作全局数据
            # 插入新规则
            source_diff = json.dumps(source_diffs[i], ensure_ascii=False) if source_diffs and i < len(source_diffs) else None
            cur = conn.execute(
                "INSERT INTO preferences (category, pattern, source_diff, weight, user_id) VALUES (?, ?, ?, 1.0, ?)",
                (category, pref["pattern"], source_diff, user_id),
            )
            pref_id = cur.lastrowid
            # 关联记录
            conn.execute(
                "INSERT INTO preference_links (preference_id, session_id) VALUES (?, ?)",
                (pref_id, session_id),
            )


def get_preference(pref_id: int) -> dict | None:
    """获取单条偏好规则"""
    with db_read_conn() as conn:
        row = conn.execute(
            "SELECT id, category, pattern, weight, active, user_id FROM preferences WHERE id = ?",
            (pref_id,),
        ).fetchone()
    return dict(row) if row else None


def update_preference(pref_id: int, active: int | None = None, pattern: str | None = None):
    """更新偏好规则（启用/禁用/修改）"""
    with db_conn() as conn:
        if active is not None:
            conn.execute("UPDATE preferences SET active = ? WHERE id = ?", (active, pref_id))
        if pattern is not None:
            conn.execute("UPDATE preferences SET pattern = ? WHERE id = ?", (pattern, pref_id))


def delete_preference(pref_id: int):
    """硬删除偏好规则"""
    with db_conn() as conn:
        conn.execute("DELETE FROM preference_links WHERE preference_id = ?", (pref_id,))
        conn.execute("DELETE FROM preferences WHERE id = ?", (pref_id,))


def list_all_preferences(user_id: int | None = None) -> list[dict]:
    """列出所有偏好（含停用的），用于管理面板"""
    with db_read_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT id, created_at, category, pattern, weight, active FROM preferences "
                "WHERE (user_id = ? OR user_id IS NULL) ORDER BY id DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, created_at, category, pattern, weight, active FROM preferences ORDER BY id DESC"
            ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# Settings (key-value)
# ============================================================

def get_setting(key: str) -> str | None:
    """获取单个设置值"""
    with db_read_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    return None


def set_setting(key: str, value: str):
    """写入/更新设置"""
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_dashboard_stats(user_id: int) -> dict:
    """获取仪表盘统计（单次查询）"""
    with db_read_conn() as conn:
        # 一条 SQL 同时拿到 count 和 sum
        row = conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(tc_count), 0) as total "
            "FROM sessions WHERE is_deleted = 0 AND user_id = ?",
            (user_id,),
        ).fetchone()
        total_sessions = row["cnt"]
        total_testcases = row["total"]

        # 最近 5 条记录（只查需要的列，不取 testcases 大字段）
        rows = conn.execute(
            "SELECT id, created_at, substr(requirement,1,60) as req_preview, tc_count "
            "FROM sessions WHERE is_deleted = 0 AND user_id = ? "
            "ORDER BY id DESC LIMIT 5",
            (user_id,),
        ).fetchall()
        recent = [dict(r) for r in rows]

    return {
        "total_sessions": total_sessions,
        "total_testcases": total_testcases,
        "recent": recent,
    }


# ============================================================
# Materials CRUD
# ============================================================

def create_material(user_id: int, title: str, content: str = "",
                    images: list[dict] | None = None) -> int:
    """创建项目资料，返回 material_id"""
    with db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO materials (user_id, title, content) VALUES (?, ?, ?)",
            (user_id, title, content),
        )
        mid = cur.lastrowid
        if images:
            for i, img in enumerate(images):
                conn.execute(
                    "INSERT INTO material_images (material_id, filename, media_type, data, sort_order) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (mid, img.get("filename"), img.get("media_type"), img["data"], i),
                )
    return mid


def list_materials(user_id: int) -> list[dict]:
    """列出用户的所有项目资料（不含图片数据）"""
    with db_read_conn() as conn:
        rows = conn.execute(
            "SELECT m.id, m.title, m.content, m.created_at, "
            "(SELECT COUNT(*) FROM material_images WHERE material_id = m.id) as image_count "
            "FROM materials m WHERE m.user_id = ? ORDER BY m.id DESC",
            (user_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["content_preview"] = (d["content"] or "")[:60]
            result.append(d)
    return result


def get_material(material_id: int) -> dict | None:
    """获取单条项目资料（含图片）"""
    with db_read_conn() as conn:
        row = conn.execute(
            "SELECT * FROM materials WHERE id = ?", (material_id,)
        ).fetchone()
        if not row:
            return None
        m = dict(row)
        images = conn.execute(
            "SELECT filename, media_type, data FROM material_images "
            "WHERE material_id = ? ORDER BY sort_order", (material_id,)
        ).fetchall()
        m["images"] = [dict(img) for img in images]
    return m


def get_materials_for_prompt(user_id: int, material_ids: list[int] | None = None) -> str:
    """格式化项目资料为 prompt 注入文本"""
    with db_read_conn() as conn:
        if material_ids:
            placeholders = ",".join("?" * len(material_ids))
            rows = conn.execute(
                f"SELECT id, title, content FROM materials WHERE id IN ({placeholders}) AND user_id = ?",
                (*material_ids, user_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, content FROM materials WHERE user_id = ? ORDER BY id DESC LIMIT 5",
                (user_id,),
            ).fetchall()
    if not rows:
        return ""
    lines = []
    for r in rows:
        lines.append(f"【{r['title']}】")
        if r["content"]:
            lines.append(r["content"])
    return "\n".join(lines)


def delete_material(material_id: int):
    """删除项目资料"""
    with db_conn() as conn:
        conn.execute("DELETE FROM material_images WHERE material_id = ?", (material_id,))
        conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))


# ============================================================
# Test Points CRUD
# ============================================================

def save_test_points(user_id: int, title: str, requirement: str,
                     points: list[dict], total: int) -> int:
    """保存测试点，返回 id"""
    with db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO test_points (user_id, title, requirement, points, total) VALUES (?, ?, ?, ?, ?)",
            (user_id, title, requirement, json.dumps(points, ensure_ascii=False), total),
        )
        return cur.lastrowid


def list_test_points(user_id: int) -> list[dict]:
    """列出用户的测试点记录"""
    with db_read_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, total, created_at FROM test_points WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_test_points(tp_id: int) -> dict | None:
    """获取单条测试点记录"""
    with db_read_conn() as conn:
        row = conn.execute("SELECT * FROM test_points WHERE id = ?", (tp_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["points"] = json.loads(d["points"])
    return d


def delete_test_points(tp_id: int):
    """删除测试点记录"""
    with db_conn() as conn:
        conn.execute("DELETE FROM test_points WHERE id = ?", (tp_id,))


def get_test_points_for_prompt(tp_id: int) -> str:
    """格式化测试点为 prompt 注入文本"""
    tp = get_test_points(tp_id)
    if not tp:
        return ""
    points = tp.get("points", [])
    if not points:
        return ""
    lines = []
    for module in points:
        mod_name = module.get("module", "未分类")
        lines.append(f"模块：{mod_name}")
        for p in module.get("points", []):
            title = p.get("title", "")
            desc = p.get("description", "")
            if desc:
                lines.append(f"- {title}：{desc}")
            else:
                lines.append(f"- {title}")
        lines.append("")
    return "\n".join(lines)


def get_model_config() -> dict:
    """获取模型配置（优先数据库，fallback 到 config.yaml）"""
    import yaml
    raw = get_setting("model_config")
    if raw:
        config = json.loads(raw)
        # 解密 API Key
        for section in ("generate", "review"):
            if section in config and "api_key" in config[section]:
                config[section]["api_key"] = decrypt_api_key(config[section]["api_key"])
        return config
    # fallback 到 config.yaml（使用绝对路径）
    try:
        cfg_path = Path(__file__).parent / "config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return {
            "generate": cfg.get("generate", {}),
            "review": cfg.get("review", {}),
        }
    except Exception:
        return {}


def save_model_config(config: dict):
    """保存模型配置到数据库（API Key 加密存储）"""
    config_to_save = json.loads(json.dumps(config))  # deep copy
    for section in ("generate", "review"):
        if section in config_to_save and "api_key" in config_to_save[section]:
            config_to_save[section]["api_key"] = encrypt_api_key(config_to_save[section]["api_key"])
    set_setting("model_config", json.dumps(config_to_save, ensure_ascii=False))
