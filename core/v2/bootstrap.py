"""V2 启动引导 - 幂等确保 data_v2.db 已就绪（Step 10.1）。

职责边界（用户冻结）：
  bootstrap = 负责"正确初始化"，失败必须 raise（绝不吞异常）；
  web/__init__.py = 负责 try/except 隔离 V2 故障、保护 V1、留 v2_ready 状态给 10.3。

三个坑防护（用户补充 A/B/C）：
  A. create_v2_schema() 成功 != schema 一定完整 —— 建表后必须 verify，防"部分成功却没抛异常"。
  B. 文件存在 != 数据库 ready —— 绝不以 os.path.exists 判断，必须查 schema_meta + 关键表
     （真实项目曾出现 data_v2.db 存在但 Tables=[] 的状态）。
  C. 表数量不作为唯一正确性证明 —— 按名校验关键表 + 关键 UNIQUE 索引 + 连接级 foreign_keys，
     避免将来新增内部元数据表（21→22）反而把正常版本判成失败。

数据安全铁律（用户冻结）：ensure_v2_ready() 全程只做"幂等建表 + 只读校验"，
  绝不含 DROP/DELETE/重建；已有 v9 + 业务数据调用后数据必须原样保留。
"""

from __future__ import annotations

import logging

from core.v2.db import v2_read_conn
from core.v2.ddl import SCHEMA_VERSION, create_v2_schema, get_schema_version

logger = logging.getLogger("v2.bootstrap")


class V2BootstrapError(RuntimeError):
    """V2 初始化或 schema 校验失败。

    由 bootstrap 主动 raise（不吞异常）；调用方 web/__init__.py 负责捕获并隔离，保护 V1。
    """


# 核心管线表（按名校验，非计数）。preferences/preference_links/materials/assets 为辅助表，不计入关键集。
CRITICAL_TABLES: frozenset[str] = frozenset(
    {
        "schema_meta",
        "users",
        "requirement_docs",
        "requirement_versions",
        "requirement_items",
        "field_specs",
        "generation_configs",
        "runs",
        "test_points",
        "test_cases",
        "coverage_obligations",
        "review_reports",
        "review_findings",
        "test_point_items",
        "test_case_points",
        "obligation_coverage",
        "test_case_revisions",
    }
)

# 幂等 upsert 依赖的 UNIQUE 指纹索引（TestPoint / TestCase 身份去重）。
CRITICAL_INDEXES: frozenset[str] = frozenset(
    {
        "ux_test_points_fingerprint",
        "ux_test_cases_fingerprint",
    }
)


def _safe_schema_version() -> int | None:
    """读取 schema_version；schema_meta 尚不存在（全新/空库）时返回 None 而非抛异常。"""
    try:
        return get_schema_version()
    except Exception:
        return None


def verify_v2_schema() -> list[str]:
    """只读校验 V2 schema 完整性；返回问题清单（空 list = 健康）。本函数绝不抛异常，只报告。

    ★ 连接工厂约束（用户冻结，补充②）：所有查询（尤其 PRAGMA foreign_keys）必须走 V2 自己的
      连接工厂 v2_read_conn()（内部 _new_conn() 已执行 PRAGMA foreign_keys=ON），
      严禁用裸 sqlite3.connect()。否则会出现"应用连接 ON / verify 连接 OFF"的假失败。
    """
    problems: list[str] = []

    # 1. schema_version 必须等于当前代码期望值（SCHEMA_VERSION，当前=9；10.2 升到 10 后自动跟随）
    version = _safe_schema_version()
    if version != SCHEMA_VERSION:
        problems.append(f"schema_version={version!r}, 期望 {SCHEMA_VERSION}")

    with v2_read_conn() as conn:
        # 2. 关键表全部存在（按名比对，非计数 —— 补充 C）
        existing_tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing_tables = CRITICAL_TABLES - existing_tables
        if missing_tables:
            problems.append(f"缺失关键表: {sorted(missing_tables)}")

        # 3. 关键 UNIQUE 索引全部存在
        existing_indexes = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        missing_indexes = CRITICAL_INDEXES - existing_indexes
        if missing_indexes:
            problems.append(f"缺失关键索引: {sorted(missing_indexes)}")

        # 4. 连接级 foreign_keys 已开启（走 v2_read_conn，_new_conn 已设 ON —— 补充②）
        fk_row = conn.execute("PRAGMA foreign_keys").fetchone()
        if not (fk_row is not None and fk_row[0]):
            problems.append("foreign_keys 未开启（PRAGMA foreign_keys != 1）")

    return problems


def ensure_v2_ready() -> None:
    """幂等：确保 data_v2.db 已初始化并通过 schema 校验；未通过则抛 V2BootstrapError。

    流程：create_v2_schema()（幂等建表/升级）→ verify_v2_schema()（只读复核）→ 不通过则 raise。
    ★ 绝不以"文件是否存在"判断 ready（补充 B）；绝不含 DROP/DELETE/重建（数据安全铁律，补充①）。
    """
    # before 仅用于日志区分"首次初始化 vs 已就绪"，不作为 ready 判据（补充 B）
    before = _safe_schema_version()

    # 幂等建表/升级：对已 v9 的库全 no-op（CREATE TABLE IF NOT EXISTS + 迁移分支跳过 + 幂等重写 schema_meta），
    # 因此不删/不改任何业务数据。
    create_v2_schema()

    # 建表后必须复核（补充 A：create 成功 != schema 一定完整）
    problems = verify_v2_schema()
    if problems:
        raise V2BootstrapError("V2 schema 校验未通过: " + "; ".join(problems))

    if before is None:
        logger.info("V2 首次初始化完成 (schema_version=%s)", SCHEMA_VERSION)
    else:
        logger.info("V2 已就绪 (schema_version=%s)", before)
