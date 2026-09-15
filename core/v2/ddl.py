"""V2 数据库 DDL - 规范化表结构（schema_version=2）。

对应 docs/v2/step1-data-model.md §8。要点：
  - 独立 data_v2.db，ULID(TEXT) 主键，users 自带 ULID + legacy_int_id 映射
  - FieldSpec 独立表（策略引擎跨项查询）；rules/permissions/steps/params 为 Pydantic 校验的 JSON 列
  - 多态引用 (target_type,target_id) 无 DB 外键，由 core/v2/resolver.py 的 Domain Validator 保障
  - obligation_coverage 是覆盖关系唯一事实源
  - 🟡 只设计不实现的表（test_scenarios/test_case_revisions/llm_invocations）此处不建
"""

from __future__ import annotations

import logging

from core.v2.db import v2_conn

logger = logging.getLogger("v2.ddl")

SCHEMA_VERSION = 2

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
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    schema_version   INTEGER NOT NULL DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_items_version ON requirement_items(version_id);

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
    id             TEXT PRIMARY KEY,
    run_id         TEXT,
    version_id     TEXT,
    module         TEXT NOT NULL,
    subcategory    TEXT NOT NULL,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL,
    dimension      TEXT NOT NULL,
    technique      TEXT,
    obligation_id  TEXT,
    priority       TEXT NOT NULL DEFAULT 'P1',
    provenance     TEXT NOT NULL DEFAULT 'llm',
    status         TEXT NOT NULL DEFAULT 'draft',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 2
);

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
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    schema_version   INTEGER NOT NULL DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_cases_run ON test_cases(run_id);
CREATE INDEX IF NOT EXISTS idx_cases_fp ON test_cases(fingerprint);

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
    auto_fixable  INTEGER NOT NULL DEFAULT 0
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
"""


def create_v2_schema() -> None:
    """在 V2 数据库中创建全部表（幂等）并写入 schema_version"""
    with v2_conn() as conn:
        conn.executescript(V2_SCHEMA_SQL)
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
