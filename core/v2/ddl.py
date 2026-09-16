"""V2 数据库 DDL - 规范化表结构（schema_version=9）。

对应 docs/v2/step1-data-model.md §8。要点：
  - 独立 data_v2.db，ULID(TEXT) 主键，users 自带 ULID + legacy_int_id 映射
  - FieldSpec 独立表（策略引擎跨项查询）；rules/permissions/steps/params 为 Pydantic 校验的 JSON 列
  - 多态引用 (target_type,target_id) 无 DB 外键，由 core/v2/resolver.py 的 Domain Validator 保障
  - obligation_coverage 是覆盖关系唯一事实源
  - 🟡 只设计不实现的表（test_scenarios/llm_invocations）此处不建（test_case_revisions 已于 Step 9 激活建表）
"""

from __future__ import annotations

import logging
import sqlite3

from core.v2.db import v2_conn

logger = logging.getLogger("v2.ddl")

SCHEMA_VERSION = 9

# V2 全量表结构（幂等：IF NOT EXISTS）
V2_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 用户（V2：ULID 主键；legacy_int_id 映射 V1 原整型 id）
CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    legacy_int_id  INTEGER,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 2
);

-- 需求：Doc → Version → Item → FieldSpec
CREATE TABLE IF NOT EXISTS requirement_docs (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(id),
    title             TEXT NOT NULL,
    source_type       TEXT NOT NULL,
    asset_ids_json    TEXT NOT NULL DEFAULT '[]',
    material_ids_json TEXT NOT NULL DEFAULT '[]',
    latest_version_id TEXT,
    status            TEXT NOT NULL DEFAULT 'draft',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    schema_version    INTEGER NOT NULL DEFAULT 2
);

CREATE TABLE IF NOT EXISTS requirement_versions (
    id             TEXT PRIMARY KEY,
    doc_id         TEXT NOT NULL REFERENCES requirement_docs(id) ON DELETE CASCADE,
    version_no     INTEGER NOT NULL,
    raw_text       TEXT,
    change_summary TEXT,
    source_ref_json TEXT,
    provenance     TEXT NOT NULL DEFAULT 'human',
    status         TEXT NOT NULL DEFAULT 'draft',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 2,
    UNIQUE (doc_id, version_no)
);

CREATE TABLE IF NOT EXISTS requirement_items (
    id               TEXT PRIMARY KEY,
    version_id       TEXT NOT NULL REFERENCES requirement_versions(id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    type             TEXT NOT NULL,
    module           TEXT NOT NULL,
    statement        TEXT NOT NULL,
    rules_json       TEXT NOT NULL DEFAULT '[]',
    permissions_json TEXT NOT NULL DEFAULT '[]',
    acceptance_json  TEXT NOT NULL DEFAULT '[]',
    source_ref_json  TEXT,
    priority_hint    TEXT,
    confidence       REAL NOT NULL DEFAULT 1.0,
    confidence_level TEXT NOT NULL DEFAULT 'high',
    provenance       TEXT NOT NULL DEFAULT 'llm',
    status           TEXT NOT NULL DEFAULT 'draft',
    fingerprint      TEXT,
    content_hash     TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    schema_version   INTEGER NOT NULL DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_items_version ON requirement_items(version_id);
CREATE INDEX IF NOT EXISTS idx_items_fingerprint ON requirement_items(fingerprint);

CREATE TABLE IF NOT EXISTS field_specs (
    id              TEXT PRIMARY KEY,
    item_id         TEXT NOT NULL REFERENCES requirement_items(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    label           TEXT NOT NULL,
    data_type       TEXT NOT NULL,
    required        INTEGER NOT NULL DEFAULT 0,
    nullable        INTEGER NOT NULL DEFAULT 0,
    min_length      INTEGER,
    max_length      INTEGER,
    min_value       REAL,
    max_value       REAL,
    pattern         TEXT,
    enum_values_json TEXT NOT NULL DEFAULT '[]',
    precision       INTEGER,
    unique_flag     INTEGER NOT NULL DEFAULT 0,
    default_value   TEXT,
    unit            TEXT,
    format          TEXT,
    example         TEXT
);
CREATE INDEX IF NOT EXISTS idx_fields_item ON field_specs(item_id);

-- 生成审计配置 + 运行会话
CREATE TABLE IF NOT EXISTS generation_configs (
    id                TEXT PRIMARY KEY,
    model_provider    TEXT NOT NULL,
    model_name        TEXT NOT NULL,
    temperature       REAL NOT NULL,
    max_tokens        INTEGER,
    enable_thinking   INTEGER,
    prompt_version    TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    reviewer_version  TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                     TEXT PRIMARY KEY,
    user_id                TEXT NOT NULL REFERENCES users(id),
    doc_id                 TEXT NOT NULL REFERENCES requirement_docs(id),
    requirement_version_id TEXT NOT NULL REFERENCES requirement_versions(id),
    generation_config_id   TEXT NOT NULL REFERENCES generation_configs(id),
    strategy_profile       TEXT,
    status                 TEXT NOT NULL DEFAULT 'ingesting',
    counts_json            TEXT NOT NULL DEFAULT '{}',
    legacy_session_id      INTEGER,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    schema_version         INTEGER NOT NULL DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_runs_version ON runs(requirement_version_id);

-- 测试资产
CREATE TABLE IF NOT EXISTS test_points (
    id                TEXT PRIMARY KEY,
    run_id            TEXT,
    version_id        TEXT,
    module            TEXT NOT NULL,
    subcategory       TEXT NOT NULL,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL,
    dimension         TEXT NOT NULL,
    technique         TEXT,
    obligation_id     TEXT,
    priority          TEXT NOT NULL DEFAULT 'P1',
    provenance        TEXT NOT NULL DEFAULT 'llm',
    status            TEXT NOT NULL DEFAULT 'draft',
    generation_scope  TEXT NOT NULL DEFAULT 'item',
    fingerprint       TEXT,
    strategy_params_json TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    schema_version    INTEGER NOT NULL DEFAULT 2
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_test_points_fingerprint ON test_points(fingerprint);
CREATE INDEX IF NOT EXISTS idx_test_points_version ON test_points(version_id);

CREATE TABLE IF NOT EXISTS test_cases (
    id               TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    display_id       TEXT NOT NULL,
    module           TEXT NOT NULL,
    title            TEXT NOT NULL,
    precondition     TEXT NOT NULL DEFAULT '',
    steps_json       TEXT NOT NULL DEFAULT '[]',
    expected         TEXT NOT NULL,
    priority         TEXT NOT NULL DEFAULT 'P1',
    type             TEXT NOT NULL,
    remark           TEXT NOT NULL DEFAULT '',
    fingerprint      TEXT,
    provenance       TEXT NOT NULL DEFAULT 'llm',
    status           TEXT NOT NULL DEFAULT 'generated',
    confidence_level TEXT NOT NULL DEFAULT 'medium',
    generation_mode  TEXT NOT NULL DEFAULT 'llm',
    content_hash     TEXT,
    validation_errors_json TEXT NOT NULL DEFAULT '[]',
    data_plan_json   TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    schema_version   INTEGER NOT NULL DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_cases_run ON test_cases(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_test_cases_fingerprint ON test_cases(fingerprint);

CREATE TABLE IF NOT EXISTS coverage_obligations (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    item_id        TEXT NOT NULL REFERENCES requirement_items(id),
    technique      TEXT NOT NULL,
    target         TEXT NOT NULL,
    description    TEXT NOT NULL,
    params_json    TEXT NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_obligations_item ON coverage_obligations(item_id);

-- 评审（多轮）
CREATE TABLE IF NOT EXISTS review_reports (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    revision            INTEGER NOT NULL,
    trigger_type        TEXT NOT NULL,
    scores_json         TEXT NOT NULL,
    overall_score       REAL NOT NULL,
    summary             TEXT NOT NULL DEFAULT '',
    obligation_coverage REAL NOT NULL DEFAULT 0.0,
    review_target_type  TEXT NOT NULL DEFAULT 'testcase',
    review_target_ids_json TEXT NOT NULL DEFAULT '[]',
    coverage_detail_json TEXT,
    executability_detail_json TEXT,
    dimension_reasons_json TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    schema_version      INTEGER NOT NULL DEFAULT 2,
    UNIQUE (run_id, revision)
);

CREATE TABLE IF NOT EXISTS review_findings (
    id            TEXT PRIMARY KEY,
    report_id     TEXT NOT NULL REFERENCES review_reports(id) ON DELETE CASCADE,
    dimension     TEXT NOT NULL,
    severity      TEXT NOT NULL,
    target_type   TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    issue         TEXT NOT NULL,
    suggestion    TEXT,
    provenance    TEXT NOT NULL,
    auto_fixable  INTEGER NOT NULL DEFAULT 0,
    detail_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_findings_target ON review_findings(target_type, target_id);

-- 追溯 / 覆盖 链接表（M:N）
CREATE TABLE IF NOT EXISTS test_point_items (
    test_point_id       TEXT NOT NULL REFERENCES test_points(id) ON DELETE CASCADE,
    requirement_item_id TEXT NOT NULL REFERENCES requirement_items(id) ON DELETE CASCADE,
    PRIMARY KEY (test_point_id, requirement_item_id)
);

CREATE TABLE IF NOT EXISTS test_case_points (
    test_case_id  TEXT NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    test_point_id TEXT NOT NULL REFERENCES test_points(id) ON DELETE CASCADE,
    PRIMARY KEY (test_case_id, test_point_id)
);

-- 覆盖唯一事实源；target 多态，无 DB 外键（由 resolver 校验）
CREATE TABLE IF NOT EXISTS obligation_coverage (
    obligation_id TEXT NOT NULL REFERENCES coverage_obligations(id) ON DELETE CASCADE,
    target_type   TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    PRIMARY KEY (obligation_id, target_type, target_id)
);

-- 偏好
CREATE TABLE IF NOT EXISTS preferences (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    category        TEXT NOT NULL,
    pattern         TEXT NOT NULL,
    weight          REAL NOT NULL DEFAULT 1.0,
    active          INTEGER NOT NULL DEFAULT 1,
    source_diff_json TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 2
);

CREATE TABLE IF NOT EXISTS preference_links (
    preference_id TEXT NOT NULL REFERENCES preferences(id) ON DELETE CASCADE,
    run_id        TEXT,
    test_case_id  TEXT
);

-- 素材 / 二进制资源
CREATE TABLE IF NOT EXISTS materials (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users(id),
    title          TEXT NOT NULL,
    content        TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 2
);

CREATE TABLE IF NOT EXISTS assets (
    id         TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL,
    owner_id   TEXT NOT NULL,
    filename   TEXT,
    media_type TEXT,
    data       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_assets_owner ON assets(owner_type, owner_id);

-- Step 9: 用例多版本（人工编辑快照，Preference Learning 数据源）
CREATE TABLE IF NOT EXISTS test_case_revisions (
    id                  TEXT PRIMARY KEY,
    test_case_id        TEXT NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    revision_no         INTEGER NOT NULL,
    snapshot_json       TEXT NOT NULL,
    changed_fields_json TEXT NOT NULL DEFAULT '[]',
    provenance          TEXT NOT NULL DEFAULT 'llm',
    changed_by          TEXT NOT NULL DEFAULT 'system',
    change_source       TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    schema_version      INTEGER NOT NULL DEFAULT 2,
    UNIQUE (test_case_id, revision_no)
);
CREATE INDEX IF NOT EXISTS idx_revisions_case ON test_case_revisions(test_case_id);
"""


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """对已存在的 schema_version=3 数据库执行 v3 → v4 升级：

    Step 4 引入 test_points.strategy_params_json 列（策略引擎结构化参数）。
    新建的数据库 CREATE TABLE 已含该列，无需进入本分支。
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(test_points)").fetchall()}
    if "strategy_params_json" not in cols:
        conn.execute("ALTER TABLE test_points ADD COLUMN strategy_params_json TEXT")
        logger.info("schema v3→v4: test_points 新增列 strategy_params_json")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """对已存在的 schema_version=2 数据库执行 v2 → v3 升级：

    Step 3 引入 test_points.generation_scope + test_points.fingerprint 两列与 UNIQUE 索引。
    新建的数据库 CREATE TABLE 已含两列，无需进入本分支。
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(test_points)").fetchall()}
    if "generation_scope" not in cols:
        conn.execute("ALTER TABLE test_points ADD COLUMN generation_scope TEXT NOT NULL DEFAULT 'item'")
        logger.info("schema v2→v3: test_points 新增列 generation_scope")
    if "fingerprint" not in cols:
        conn.execute("ALTER TABLE test_points ADD COLUMN fingerprint TEXT")
        logger.info("schema v2→v3: test_points 新增列 fingerprint")
        # 已有行补算 fingerprint（避免 UNIQUE 索引创建后旧行全为 NULL 导致后续幂等失败）
        from core.v2.fingerprint import compute_testpoint_fingerprint

        rows = conn.execute(
            "SELECT id, version_id, generation_scope, module, subcategory, title FROM test_points"
        ).fetchall()
        for row in rows:
            item_ids = [
                r["requirement_item_id"]
                for r in conn.execute(
                    "SELECT requirement_item_id FROM test_point_items WHERE test_point_id = ?", (row["id"],)
                ).fetchall()
            ]
            fp = compute_testpoint_fingerprint(
                version_id=row["version_id"],
                generation_scope=row["generation_scope"] or "item",
                item_ids=item_ids,
                module=row["module"],
                subcategory=row["subcategory"],
                title=row["title"],
            )
            conn.execute("UPDATE test_points SET fingerprint = ? WHERE id = ?", (fp, row["id"]))
        logger.info("schema v2→v3: 已为 %d 行 test_points 补算 fingerprint", len(rows))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_test_points_fingerprint ON test_points(fingerprint)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_test_points_version ON test_points(version_id)")


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """对已存在的 schema_version=4 数据库执行 v4 → v5 升级：

    Step 5 引入 test_cases 的 4 个新列（generation_mode / content_hash /
    validation_errors_json / data_plan_json）与 fingerprint UNIQUE 索引。
    新建的数据库 CREATE TABLE 已含该等列与索引，无需进入本分支。

    幂等身份回填策略：
      - content_hash：所有行都可补算（仅依赖 title/precondition/steps/expected/type/priority）。
      - fingerprint：仅对 test_point_ids 非空的行补算（需从 run 反查 version_id）；
        迁移自 V1 的旧用例无 test_point_ids，fingerprint 留 NULL（SQLite UNIQUE 索引允许多个 NULL，不冲突）。
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(test_cases)").fetchall()}
    if "generation_mode" not in cols:
        conn.execute("ALTER TABLE test_cases ADD COLUMN generation_mode TEXT NOT NULL DEFAULT 'llm'")
        logger.info("schema v4→v5: test_cases 新增列 generation_mode")
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE test_cases ADD COLUMN content_hash TEXT")
        logger.info("schema v4→v5: test_cases 新增列 content_hash")
    if "validation_errors_json" not in cols:
        conn.execute("ALTER TABLE test_cases ADD COLUMN validation_errors_json TEXT NOT NULL DEFAULT '[]'")
        logger.info("schema v4→v5: test_cases 新增列 validation_errors_json")
    if "data_plan_json" not in cols:
        conn.execute("ALTER TABLE test_cases ADD COLUMN data_plan_json TEXT NOT NULL DEFAULT '[]'")
        logger.info("schema v4→v5: test_cases 新增列 data_plan_json")

    # 已有行补算 content_hash（总是可行）与 fingerprint（仅 test_point_ids 非空时）
    from core.v2.fingerprint import compute_testcase_content_hash, compute_testcase_fingerprint

    rows = conn.execute(
        "SELECT id, run_id, title, precondition, steps_json, expected, type, priority, "
        "generation_mode, fingerprint FROM test_cases"
    ).fetchall()
    backfilled_fp = 0
    for row in rows:
        # content_hash：从 steps_json 重建 steps_text（与 TestCase.render_steps_text 一致）
        steps_text = _render_steps_text_from_json(row["steps_json"])
        ch = compute_testcase_content_hash(
            title=row["title"],
            precondition=row["precondition"] or "",
            steps_text=steps_text,
            expected=row["expected"],
            type=row["type"],
            priority=row["priority"],
        )
        # fingerprint：仅当有 test_point_ids 时补算（version_id 从 run 反查）
        fp = row["fingerprint"]
        if not fp:
            point_ids = [
                r["test_point_id"]
                for r in conn.execute(
                    "SELECT test_point_id FROM test_case_points WHERE test_case_id = ?", (row["id"],)
                ).fetchall()
            ]
            if point_ids:
                run_row = conn.execute(
                    "SELECT requirement_version_id FROM runs WHERE id = ?", (row["run_id"],)
                ).fetchone()
                version_id = run_row["requirement_version_id"] if run_row else None
                fp = compute_testcase_fingerprint(
                    version_id=version_id,
                    generation_mode=row["generation_mode"] or "llm",
                    test_point_ids=point_ids,
                )
                backfilled_fp += 1
        conn.execute(
            "UPDATE test_cases SET content_hash = ?, fingerprint = COALESCE(?, fingerprint) WHERE id = ?",
            (ch, fp, row["id"]),
        )
    logger.info(
        "schema v4→v5: 已为 %d 行 test_cases 补算 content_hash，%d 行补算 fingerprint", len(rows), backfilled_fp
    )

    # 索引升级：drop 普通索引 idx_cases_fp，建 UNIQUE 索引 ux_test_cases_fingerprint
    conn.execute("DROP INDEX IF EXISTS idx_cases_fp")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_test_cases_fingerprint ON test_cases(fingerprint)")


def _render_steps_text_from_json(steps_json: str | None) -> str:
    """从 steps_json 重建与 TestCase.render_steps_text() 一致的文本（仅用于迁移补算 content_hash）。"""
    import json as _json

    if not steps_json:
        return ""
    try:
        steps = _json.loads(steps_json)
    except (ValueError, TypeError):
        return ""
    if not isinstance(steps, list):
        return ""
    lines = []
    for step in sorted(steps, key=lambda s: s.get("seq", 0) if isinstance(s, dict) else 0):
        if not isinstance(step, dict):
            continue
        text = f"{step.get('seq', '')}. {step.get('action', '')}"
        if step.get("data"):
            text += f"（输入：{step['data']}）"
        if step.get("expected"):
            text += f" → {step['expected']}"
        lines.append(text)
    return "\n".join(lines)


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """v5 → v6：Step 6 引入 requirement_items.fingerprint + content_hash（变更影响分析双 hash）。

    新建库 CREATE TABLE 已含两列，无需进入本分支。
    回填：fingerprint 从 module/type/statement 算；content_hash 需 fields（从 field_specs 重建
    FieldSpec，复用 repository._load_fields 保证与 save_item 计算一致）+ rules/permissions/acceptance（从 JSON 列解析）。
    ★ fingerprint 非 UNIQUE（同 doc 跨版本的同一 item fingerprint 相同），仅建普通索引加速匹配查询。
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(requirement_items)").fetchall()}
    if "fingerprint" not in cols:
        conn.execute("ALTER TABLE requirement_items ADD COLUMN fingerprint TEXT")
        logger.info("schema v5→v6: requirement_items 新增列 fingerprint")
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE requirement_items ADD COLUMN content_hash TEXT")
        logger.info("schema v5→v6: requirement_items 新增列 content_hash")

    import json as _json

    from core.v2.fingerprint import compute_item_content_hash, compute_item_identity_fingerprint
    from core.v2.repository import _load_fields

    rows = conn.execute(
        "SELECT id, type, module, statement, rules_json, permissions_json, acceptance_json FROM requirement_items"
    ).fetchall()
    for row in rows:
        fp = compute_item_identity_fingerprint(module=row["module"], type=row["type"], statement=row["statement"])
        fields = _load_fields(conn, row["id"])
        rules = _json.loads(row["rules_json"]) if row["rules_json"] else []
        perms = _json.loads(row["permissions_json"]) if row["permissions_json"] else []
        acc = _json.loads(row["acceptance_json"]) if row["acceptance_json"] else []
        ch = compute_item_content_hash(
            statement=row["statement"], fields=fields, rules=rules, permissions=perms, acceptance_criteria=acc
        )
        conn.execute("UPDATE requirement_items SET fingerprint = ?, content_hash = ? WHERE id = ?", (fp, ch, row["id"]))
    logger.info("schema v5→v6: 已为 %d 行 requirement_items 补算 fingerprint/content_hash", len(rows))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_fingerprint ON requirement_items(fingerprint)")


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """v8 → v9：Step 9 引入 test_case_revisions 表（人工编辑快照）。

    新建库 CREATE TABLE 已含该表，无需进入本分支。
    旧库无历史 Revision 数据，仅建表即可。
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS test_case_revisions (
            id                  TEXT PRIMARY KEY,
            test_case_id        TEXT NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
            revision_no         INTEGER NOT NULL,
            snapshot_json       TEXT NOT NULL,
            changed_fields_json TEXT NOT NULL DEFAULT '[]',
            provenance          TEXT NOT NULL DEFAULT 'llm',
            changed_by          TEXT NOT NULL DEFAULT 'system',
            change_source       TEXT NOT NULL DEFAULT '',
            created_at          TEXT NOT NULL,
            schema_version      INTEGER NOT NULL DEFAULT 2,
            UNIQUE (test_case_id, revision_no)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_revisions_case ON test_case_revisions(test_case_id)")
    logger.info("schema v8→v9: 新增 test_case_revisions 表")


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """v7 → v8：Step 8 无 DDL 列变更（仅状态机代码层变更：REVIEWED → ARCHIVED 放开）。

    保留此迁移分支为未来扩展预留（如 optimizer_actions 表持久化）。
    新建库 CREATE TABLE 已含全部列，无需进入本分支。
    """
    logger.info("schema v7→v8: Step 8 无 DDL 变更（仅状态机代码层变更）")


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """v6 → v7：Step 7 引入 review_reports 5 列 + review_findings.detail_json（评审明细/证据/预留字段）。

    新建库 CREATE TABLE 已含该等列，无需进入本分支。
    新列均可空或有默认，旧评审行无需回填业务值（旧报告的明细字段留 NULL/默认）。
    """
    rr_cols = {row["name"] for row in conn.execute("PRAGMA table_info(review_reports)").fetchall()}
    if "review_target_type" not in rr_cols:
        conn.execute("ALTER TABLE review_reports ADD COLUMN review_target_type TEXT NOT NULL DEFAULT 'testcase'")
        conn.execute("ALTER TABLE review_reports ADD COLUMN review_target_ids_json TEXT NOT NULL DEFAULT '[]'")
        conn.execute("ALTER TABLE review_reports ADD COLUMN coverage_detail_json TEXT")
        conn.execute("ALTER TABLE review_reports ADD COLUMN executability_detail_json TEXT")
        conn.execute("ALTER TABLE review_reports ADD COLUMN dimension_reasons_json TEXT NOT NULL DEFAULT '{}'")
        logger.info("schema v6→v7: review_reports 新增 5 列")
    rf_cols = {row["name"] for row in conn.execute("PRAGMA table_info(review_findings)").fetchall()}
    if "detail_json" not in rf_cols:
        conn.execute("ALTER TABLE review_findings ADD COLUMN detail_json TEXT")
        logger.info("schema v6→v7: review_findings 新增列 detail_json")


def create_v2_schema() -> None:
    """在 V2 数据库中创建全部表（幂等）并写入 schema_version；检测到旧版本自动升级"""
    with v2_conn() as conn:
        # 1. 先跑 executescript（新 db 直接建齐 v4 列与索引；旧 db 的 CREATE TABLE IF NOT EXISTS 不会重跑，仅补建缺失表）
        conn.executescript(V2_SCHEMA_SQL)
        # 2. 检测旧版本→逐级升级（CREATE INDEX 已在 V2_SCHEMA_SQL 幂等执行）
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        existing = 0
        if row is not None:
            try:
                existing = int(row["value"])
            except (TypeError, ValueError):
                existing = 0
        if 0 < existing < 3:
            _migrate_v2_to_v3(conn)
        if 0 < existing < 4:
            _migrate_v3_to_v4(conn)
        if 0 < existing < 5:
            _migrate_v4_to_v5(conn)
        if 0 < existing < 6:
            _migrate_v5_to_v6(conn)
        if 0 < existing < 7:
            _migrate_v6_to_v7(conn)
        if 0 < existing < 8:
            _migrate_v7_to_v8(conn)
        if 0 < existing < 9:
            _migrate_v8_to_v9(conn)
        # 3. 写入当前 schema_version
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
    logger.info("V2 schema 创建完成 (schema_version=%s)", SCHEMA_VERSION)


def get_schema_version() -> int | None:
    """读取当前 V2 schema 版本（未初始化返回 None）"""
    from core.v2.db import v2_read_conn

    with v2_read_conn() as conn:
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else None
