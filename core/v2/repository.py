"""V2 Repository - Pydantic ↔ SQLite 映射（唯一持久化入口）。

对应 docs/v2/step1-data-model.md §10。约定：
  - 写入：实体 → 列（枚举取 .value，datetime 取 isoformat，list/dict → *_json 列）
  - 读出：行 → dict（JSON 列解析、列名还原）→ Model.model_validate（天然完成 Schema 校验）
  - M:N 关系与子表（field_specs / findings / links）随主实体一并读写
"""

from __future__ import annotations

import json
import sqlite3

from core.schemas import (
    CoverageObligation,
    FieldSpec,
    GenerationConfig,
    Preference,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementVersion,
    ReviewFinding,
    ReviewReport,
    Run,
    TargetType,
    TestCase,
    TestCaseRevision,
    TestPoint,
    new_ulid,
)
from core.v2.db import v2_conn, v2_read_conn
from core.v2.fingerprint import (
    compute_item_content_hash,
    compute_item_identity_fingerprint,
    compute_strategy_testpoint_fingerprint,
    compute_testcase_content_hash,
    compute_testcase_fingerprint,
    compute_testpoint_fingerprint,
)
from core.v2.resolver import ReferentialValidator


def _j(value: object) -> str:
    """序列化为 JSON 列文本"""
    return json.dumps(value, ensure_ascii=False, default=str)


def _dt(value: object) -> str:
    """datetime → ISO 文本"""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _en(value: object) -> str | None:
    """枚举/StrEnum → 其字符串值"""
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _loads(text: str | None, default: object) -> object:
    return json.loads(text) if text else default


def _upsert(table: str, columns: list[str]) -> str:
    """生成 upsert SQL：INSERT ... ON CONFLICT(id) DO UPDATE。

    ★ 必须用 upsert 而非 INSERT OR REPLACE：后者的语义是"先 DELETE 再 INSERT"，
    而 DELETE 会触发子表的 ON DELETE CASCADE，导致重存父实体时静默级联删光子数据
    （如重存 doc 会删掉其所有 version→item；重存 run 会删掉其所有 case）。
    upsert 原地更新、不删行，故不触发级联。
    """
    cols = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c != "id")
    return f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {updates}"


# ============================================================
# 用户（V2：ULID 主键）
# ============================================================


def save_user(uid: str, username: str, password_hash: str, legacy_int_id: int | None = None) -> None:
    with v2_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, legacy_int_id, username, password_hash, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET legacy_int_id=excluded.legacy_int_id, "
            "username=excluded.username, password_hash=excluded.password_hash",
            (uid, legacy_int_id, username, password_hash),
        )


def get_user_by_legacy_id(legacy_int_id: int) -> sqlite3.Row | None:
    with v2_read_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE legacy_int_id = ?", (legacy_int_id,)).fetchone()


# ============================================================
# 需求层：Doc / Version / Item(+FieldSpec)
# ============================================================


def save_doc(doc: RequirementDoc) -> None:
    with v2_conn() as conn:
        conn.execute(
            _upsert(
                "requirement_docs",
                [
                    "id",
                    "user_id",
                    "title",
                    "source_type",
                    "asset_ids_json",
                    "material_ids_json",
                    "latest_version_id",
                    "status",
                    "created_at",
                    "updated_at",
                    "schema_version",
                ],
            ),
            (
                doc.id,
                doc.user_id,
                doc.title,
                _en(doc.source_type),
                _j(doc.asset_ids),
                _j(doc.material_ids),
                doc.latest_version_id,
                _en(doc.status),
                _dt(doc.created_at),
                _dt(doc.updated_at),
                doc.schema_version,
            ),
        )


def get_doc(doc_id: str) -> RequirementDoc | None:
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM requirement_docs WHERE id = ?", (doc_id,)).fetchone()
    if not row:
        return None
    return RequirementDoc.model_validate(
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "source_type": row["source_type"],
            "asset_ids": _loads(row["asset_ids_json"], []),
            "material_ids": _loads(row["material_ids_json"], []),
            "latest_version_id": row["latest_version_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "schema_version": row["schema_version"],
        }
    )


def save_version(version: RequirementVersion) -> None:
    with v2_conn() as conn:
        conn.execute(
            _upsert(
                "requirement_versions",
                [
                    "id",
                    "doc_id",
                    "version_no",
                    "raw_text",
                    "change_summary",
                    "source_ref_json",
                    "provenance",
                    "status",
                    "created_at",
                    "updated_at",
                    "schema_version",
                ],
            ),
            (
                version.id,
                version.doc_id,
                version.version_no,
                version.raw_text,
                version.change_summary,
                _j(version.source_ref.model_dump(mode="json")) if version.source_ref else None,
                _en(version.provenance),
                _en(version.status),
                _dt(version.created_at),
                _dt(version.updated_at),
                version.schema_version,
            ),
        )


def get_version(version_id: str) -> RequirementVersion | None:
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM requirement_versions WHERE id = ?", (version_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["source_ref"] = _loads(row["source_ref_json"], None)
    data.pop("source_ref_json", None)
    return RequirementVersion.model_validate(data)


def list_versions(doc_id: str) -> list[RequirementVersion]:
    with v2_read_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM requirement_versions WHERE doc_id = ? ORDER BY version_no", (doc_id,)
        ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        data["source_ref"] = _loads(row["source_ref_json"], None)
        data.pop("source_ref_json", None)
        result.append(RequirementVersion.model_validate(data))
    return result


def save_item(item: RequirementItem) -> None:
    """保存需求项及其 FieldSpec 子表（先删后插，子表 id 为 DB 代理键）。

    Step 6：入库前兜底计算 fingerprint（identity）与 content_hash（内容），
    为变更影响分析提供跨版本匹配键与内容变化判定依据。
    """
    if not item.fingerprint:
        item.fingerprint = compute_item_identity_fingerprint(
            module=item.module, type=_en(item.type) or "", statement=item.statement
        )
    if not item.content_hash:
        item.content_hash = compute_item_content_hash(
            statement=item.statement,
            fields=item.fields,
            rules=item.rules,
            permissions=item.permissions,
            acceptance_criteria=item.acceptance_criteria,
        )
    with v2_conn() as conn:
        conn.execute(
            _upsert(
                "requirement_items",
                [
                    "id",
                    "version_id",
                    "seq",
                    "type",
                    "module",
                    "statement",
                    "rules_json",
                    "permissions_json",
                    "acceptance_json",
                    "source_ref_json",
                    "priority_hint",
                    "confidence",
                    "confidence_level",
                    "provenance",
                    "status",
                    "fingerprint",
                    "content_hash",
                    "created_at",
                    "updated_at",
                    "schema_version",
                ],
            ),
            (
                item.id,
                item.version_id,
                item.seq,
                _en(item.type),
                item.module,
                item.statement,
                _j([r.model_dump(mode="json") for r in item.rules]),
                _j([p.model_dump(mode="json") for p in item.permissions]),
                _j(item.acceptance_criteria),
                _j(item.source_ref.model_dump(mode="json")) if item.source_ref else None,
                _en(item.priority_hint),
                item.confidence,
                _en(item.confidence_level),
                _en(item.provenance),
                _en(item.status),
                item.fingerprint,
                item.content_hash,
                _dt(item.created_at),
                _dt(item.updated_at),
                item.schema_version,
            ),
        )
        conn.execute("DELETE FROM field_specs WHERE item_id = ?", (item.id,))
        for f in item.fields:
            conn.execute(
                """INSERT INTO field_specs
                   (id, item_id, name, label, data_type, required, nullable, min_length, max_length,
                    min_value, max_value, pattern, enum_values_json, precision, unique_flag,
                    default_value, unit, format, example)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    new_ulid(),
                    item.id,
                    f.name,
                    f.label,
                    _en(f.data_type),
                    int(f.required),
                    int(f.nullable),
                    f.min_length,
                    f.max_length,
                    f.min_value,
                    f.max_value,
                    f.pattern,
                    _j(f.enum_values),
                    f.precision,
                    int(f.unique),
                    f.default,
                    f.unit,
                    f.format,
                    f.example,
                ),
            )


def _row_to_item(row: sqlite3.Row, fields: list[FieldSpec]) -> RequirementItem:
    return RequirementItem.model_validate(
        {
            "id": row["id"],
            "version_id": row["version_id"],
            "seq": row["seq"],
            "type": row["type"],
            "module": row["module"],
            "statement": row["statement"],
            "rules": _loads(row["rules_json"], []),
            "permissions": _loads(row["permissions_json"], []),
            "acceptance_criteria": _loads(row["acceptance_json"], []),
            "source_ref": _loads(row["source_ref_json"], None),
            "priority_hint": row["priority_hint"],
            "confidence": row["confidence"],
            "confidence_level": row["confidence_level"],
            "provenance": row["provenance"],
            "status": row["status"],
            "fingerprint": row["fingerprint"],
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "schema_version": row["schema_version"],
            "fields": [f.model_dump(mode="json") for f in fields],
        }
    )


def _load_fields(conn: sqlite3.Connection, item_id: str) -> list[FieldSpec]:
    rows = conn.execute("SELECT * FROM field_specs WHERE item_id = ?", (item_id,)).fetchall()
    fields = []
    for r in rows:
        fields.append(
            FieldSpec(
                name=r["name"],
                label=r["label"],
                data_type=r["data_type"],
                required=bool(r["required"]),
                nullable=bool(r["nullable"]),
                min_length=r["min_length"],
                max_length=r["max_length"],
                min_value=r["min_value"],
                max_value=r["max_value"],
                pattern=r["pattern"],
                enum_values=_loads(r["enum_values_json"], []),
                precision=r["precision"],
                unique=bool(r["unique_flag"]),
                default=r["default_value"],
                unit=r["unit"],
                format=r["format"],
                example=r["example"],
            )
        )
    return fields


def get_item(item_id: str) -> RequirementItem | None:
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM requirement_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        return _row_to_item(row, _load_fields(conn, item_id))


def list_items(version_id: str) -> list[RequirementItem]:
    with v2_read_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM requirement_items WHERE version_id = ? ORDER BY seq", (version_id,)
        ).fetchall()
        return [_row_to_item(r, _load_fields(conn, r["id"])) for r in rows]


# ============================================================
# 生成审计 + 运行会话
# ============================================================


def save_generation_config(cfg: GenerationConfig) -> None:
    with v2_conn() as conn:
        conn.execute(
            "INSERT INTO generation_configs (id, model_provider, model_name, temperature, max_tokens, "
            "enable_thinking, prompt_version, generator_version, reviewer_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET model_provider=excluded.model_provider, "
            "model_name=excluded.model_name, temperature=excluded.temperature, max_tokens=excluded.max_tokens, "
            "enable_thinking=excluded.enable_thinking, prompt_version=excluded.prompt_version, "
            "generator_version=excluded.generator_version, reviewer_version=excluded.reviewer_version",
            (
                cfg.id,
                cfg.model_provider,
                cfg.model_name,
                cfg.temperature,
                cfg.max_tokens,
                None if cfg.enable_thinking is None else int(cfg.enable_thinking),
                cfg.prompt_version,
                cfg.generator_version,
                cfg.reviewer_version,
            ),
        )


def get_generation_config(cfg_id: str) -> GenerationConfig | None:
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM generation_configs WHERE id = ?", (cfg_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data.pop("created_at", None)
    data["enable_thinking"] = None if row["enable_thinking"] is None else bool(row["enable_thinking"])
    return GenerationConfig.model_validate(data)


def save_run(run: Run) -> None:
    with v2_conn() as conn:
        conn.execute(
            _upsert(
                "runs",
                [
                    "id",
                    "user_id",
                    "doc_id",
                    "requirement_version_id",
                    "generation_config_id",
                    "strategy_profile",
                    "status",
                    "counts_json",
                    "legacy_session_id",
                    "failed_step",
                    "error_message",
                    "created_at",
                    "updated_at",
                    "schema_version",
                ],
            ),
            (
                run.id,
                run.user_id,
                run.doc_id,
                run.requirement_version_id,
                run.generation_config_id,
                run.strategy_profile,
                _en(run.status),
                run.counts.model_dump_json(),
                run.legacy_session_id,
                run.failed_step,
                run.error_message,
                _dt(run.created_at),
                _dt(run.updated_at),
                run.schema_version,
            ),
        )


def get_run(run_id: str) -> Run | None:
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["counts"] = _loads(row["counts_json"], {})
    data.pop("counts_json", None)
    return Run.model_validate(data)


# ============================================================
# 测试点（+ 需求项链接）
# ============================================================


def save_test_point(tp: TestPoint) -> None:
    """保存测试点；以 fingerprint 为业务幂等身份，同一 fingerprint 多次保存复用旧 id。

    Step 3 引入：
      - 入库前若 tp.fingerprint 为空，自动兜底计算（避免旧调用方漏传）。
      - 先按 fingerprint 查旧行：若存在且 id 不同，则复用旧 id 重写 tp，保证：
          1) ULID 稳定，不造成下游 test_case_points / obligation_coverage 断链；
          2) UNIQUE(fingerprint) 索引不被破坏。
      - upsert 列表新增 generation_scope / fingerprint 两列。
    """
    if not tp.fingerprint:
        if tp.provenance == Provenance.STRATEGY and tp.obligation_id and tp.technique:
            # Step 4 公式：version_id | "strategy" | obligation_id | technique | canonical(strategy_params)
            tp.fingerprint = compute_strategy_testpoint_fingerprint(
                version_id=tp.version_id,
                obligation_id=tp.obligation_id,
                technique=tp.technique.value if hasattr(tp.technique, "value") else str(tp.technique),
                strategy_params=tp.strategy_params,
            )
        else:
            # Step 3 公式：version_id | scope | sorted(item_ids) | module | subcategory | normalize(title)
            tp.fingerprint = compute_testpoint_fingerprint(
                version_id=tp.version_id,
                generation_scope=tp.generation_scope.value
                if hasattr(tp.generation_scope, "value")
                else str(tp.generation_scope),
                item_ids=list(tp.item_ids),
                module=tp.module,
                subcategory=tp.subcategory,
                title=tp.title,
            )

    with v2_conn() as conn:
        # 幂等身份对齐：若已有同 fingerprint 行但 id 不同，复用旧 id
        existing = conn.execute("SELECT id FROM test_points WHERE fingerprint = ?", (tp.fingerprint,)).fetchone()
        if existing and existing["id"] != tp.id:
            tp.id = existing["id"]
        conn.execute(
            _upsert(
                "test_points",
                [
                    "id",
                    "run_id",
                    "version_id",
                    "module",
                    "subcategory",
                    "title",
                    "description",
                    "dimension",
                    "technique",
                    "obligation_id",
                    "priority",
                    "provenance",
                    "status",
                    "generation_scope",
                    "fingerprint",
                    "strategy_params_json",
                    "created_at",
                    "updated_at",
                    "schema_version",
                ],
            ),
            (
                tp.id,
                tp.run_id,
                tp.version_id,
                tp.module,
                tp.subcategory,
                tp.title,
                tp.description,
                _en(tp.dimension),
                _en(tp.technique),
                tp.obligation_id,
                _en(tp.priority),
                _en(tp.provenance),
                _en(tp.status),
                _en(tp.generation_scope),
                tp.fingerprint,
                _j(tp.strategy_params) if tp.strategy_params is not None else None,
                _dt(tp.created_at),
                _dt(tp.updated_at),
                tp.schema_version,
            ),
        )
        conn.execute("DELETE FROM test_point_items WHERE test_point_id = ?", (tp.id,))
        conn.executemany(
            "INSERT OR IGNORE INTO test_point_items (test_point_id, requirement_item_id) VALUES (?,?)",
            [(tp.id, iid) for iid in tp.item_ids],
        )


def _row_to_test_point(row: sqlite3.Row, item_ids: list[str]) -> TestPoint:
    data = dict(row)
    data["item_ids"] = item_ids
    # strategy_params_json → strategy_params（dict | None）
    sp_json = data.pop("strategy_params_json", None)
    data["strategy_params"] = json.loads(sp_json) if sp_json else None
    return TestPoint.model_validate(data)


def get_test_point(tp_id: str) -> TestPoint | None:
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM test_points WHERE id = ?", (tp_id,)).fetchone()
        if not row:
            return None
        links = conn.execute(
            "SELECT requirement_item_id FROM test_point_items WHERE test_point_id = ?", (tp_id,)
        ).fetchall()
        return _row_to_test_point(row, [r["requirement_item_id"] for r in links])


def get_test_point_by_fingerprint(fingerprint: str) -> TestPoint | None:
    """按业务指纹查测试点（Step 3 orchestrator 幂等重跑时用于定位旧行）。"""
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM test_points WHERE fingerprint = ?", (fingerprint,)).fetchone()
        if not row:
            return None
        links = conn.execute(
            "SELECT requirement_item_id FROM test_point_items WHERE test_point_id = ?", (row["id"],)
        ).fetchall()
        return _row_to_test_point(row, [r["requirement_item_id"] for r in links])


def list_test_points_by_version(version_id: str) -> list[TestPoint]:
    """按 RequirementVersion 列出全部测试点（Step 3 覆盖率报告与幂等清理用）。"""
    with v2_read_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM test_points WHERE version_id = ? ORDER BY created_at", (version_id,)
        ).fetchall()
        result: list[TestPoint] = []
        for row in rows:
            links = conn.execute(
                "SELECT requirement_item_id FROM test_point_items WHERE test_point_id = ?", (row["id"],)
            ).fetchall()
            result.append(_row_to_test_point(row, [r["requirement_item_id"] for r in links]))
        return result


def list_test_points_by_run(run_id: str) -> list[TestPoint]:
    """按 Run 列出全部测试点（幂等重跑时清理旧产物用）。"""
    with v2_read_conn() as conn:
        rows = conn.execute("SELECT * FROM test_points WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
        result: list[TestPoint] = []
        for row in rows:
            links = conn.execute(
                "SELECT requirement_item_id FROM test_point_items WHERE test_point_id = ?", (row["id"],)
            ).fetchall()
            result.append(_row_to_test_point(row, [r["requirement_item_id"] for r in links]))
        return result


def list_test_points_by_item(item_id: str) -> list[TestPoint]:
    """按 RequirementItem 反查关联的 TestPoint（Step 6 追溯：item→TP，经 test_point_items）。"""
    with v2_read_conn() as conn:
        tp_ids = [
            r["test_point_id"]
            for r in conn.execute(
                "SELECT test_point_id FROM test_point_items WHERE requirement_item_id = ?", (item_id,)
            ).fetchall()
        ]
        result = []
        for tp_id in tp_ids:
            row = conn.execute("SELECT * FROM test_points WHERE id = ?", (tp_id,)).fetchone()
            if row:
                links = conn.execute(
                    "SELECT requirement_item_id FROM test_point_items WHERE test_point_id = ?", (tp_id,)
                ).fetchall()
                result.append(_row_to_test_point(row, [r["requirement_item_id"] for r in links]))
        return result


# ============================================================
# 测试用例（+ 测试点链接）
# ============================================================


def save_test_case(tc: TestCase) -> None:
    """保存测试用例；以 fingerprint 为业务幂等身份，同一 fingerprint 多次保存复用旧 id。

    Step 5 引入（对齐 save_test_point 的幂等模式）：
      - 入库前若 tc.content_hash 为空，自动兜底计算（仅依赖内容字段）。
      - 入库前若 tc.fingerprint 为空，自动兜底计算（version_id 从 run 反查，
        因为 TestCase 模型本身不存 version_id）。
      - 先按 fingerprint 查旧行：若存在且 id 不同，复用旧 id，保证：
          1) ULID 稳定，不造成下游 test_case_points 断链；
          2) UNIQUE(fingerprint) 索引不被破坏。
      - upsert 列表新增 generation_mode / content_hash / validation_errors_json / data_plan_json 四列。
    """
    with v2_conn() as conn:
        # content_hash 兜底（总是可算，仅依赖内容字段）
        if not tc.content_hash:
            tc.content_hash = compute_testcase_content_hash(
                title=tc.title,
                precondition=tc.precondition,
                steps_text=tc.render_steps_text(),
                expected=tc.expected,
                type=_en(tc.type) or "",
                priority=_en(tc.priority) or "",
            )
        # fingerprint 兜底（version_id 从 run 反查）
        # ★ 仅当 test_point_ids 非空时才计算：迁移自 V1 的旧用例无 test_point_ids，
        #   若强算会得到 sha256(version_id|mode|"") 的相同指纹 → 同 run 下多个迁移用例 UNIQUE 碰撞坦缩。
        #   留 NULL（SQLite UNIQUE 索引允许多个 NULL，不冲突）；Step 5 生成的用例必然有 test_point_ids（1:1 派生）。
        if not tc.fingerprint and tc.test_point_ids:
            run_row = conn.execute("SELECT requirement_version_id FROM runs WHERE id = ?", (tc.run_id,)).fetchone()
            version_id = run_row["requirement_version_id"] if run_row else None
            tc.fingerprint = compute_testcase_fingerprint(
                version_id=version_id,
                generation_mode=_en(tc.generation_mode) or "",
                test_point_ids=list(tc.test_point_ids),
            )
        # 幂等身份对齐：若已有同 fingerprint 行但 id 不同，复用旧 id（fingerprint 为 NULL 时跳过）
        if tc.fingerprint:
            existing = conn.execute("SELECT id FROM test_cases WHERE fingerprint = ?", (tc.fingerprint,)).fetchone()
            if existing and existing["id"] != tc.id:
                tc.id = existing["id"]
        conn.execute(
            _upsert(
                "test_cases",
                [
                    "id",
                    "run_id",
                    "display_id",
                    "module",
                    "title",
                    "precondition",
                    "steps_json",
                    "expected",
                    "priority",
                    "type",
                    "remark",
                    "fingerprint",
                    "provenance",
                    "status",
                    "confidence_level",
                    "generation_mode",
                    "content_hash",
                    "validation_errors_json",
                    "data_plan_json",
                    "created_at",
                    "updated_at",
                    "schema_version",
                ],
            ),
            (
                tc.id,
                tc.run_id,
                tc.display_id,
                tc.module,
                tc.title,
                tc.precondition,
                _j([s.model_dump(mode="json") for s in tc.steps]),
                tc.expected,
                _en(tc.priority),
                _en(tc.type),
                tc.remark,
                tc.fingerprint,
                _en(tc.provenance),
                _en(tc.status),
                _en(tc.confidence_level),
                _en(tc.generation_mode),
                tc.content_hash,
                _j(tc.validation_errors),
                _j([d.model_dump(mode="json") for d in tc.data_plan]),
                _dt(tc.created_at),
                _dt(tc.updated_at),
                tc.schema_version,
            ),
        )
        conn.execute("DELETE FROM test_case_points WHERE test_case_id = ?", (tc.id,))
        conn.executemany(
            "INSERT OR IGNORE INTO test_case_points (test_case_id, test_point_id) VALUES (?,?)",
            [(tc.id, pid) for pid in tc.test_point_ids],
        )


def _row_to_test_case(row: sqlite3.Row, point_ids: list[str]) -> TestCase:
    data = dict(row)
    data["steps"] = _loads(row["steps_json"], [])
    data.pop("steps_json", None)
    data["validation_errors"] = _loads(row["validation_errors_json"], [])
    data.pop("validation_errors_json", None)
    data["data_plan"] = _loads(row["data_plan_json"], [])
    data.pop("data_plan_json", None)
    data["test_point_ids"] = point_ids
    return TestCase.model_validate(data)


def get_test_case(tc_id: str) -> TestCase | None:
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM test_cases WHERE id = ?", (tc_id,)).fetchone()
        if not row:
            return None
        links = conn.execute("SELECT test_point_id FROM test_case_points WHERE test_case_id = ?", (tc_id,)).fetchall()
        return _row_to_test_case(row, [r["test_point_id"] for r in links])


def list_test_cases(run_id: str) -> list[TestCase]:
    with v2_read_conn() as conn:
        rows = conn.execute("SELECT * FROM test_cases WHERE run_id = ? ORDER BY display_id", (run_id,)).fetchall()
        result = []
        for row in rows:
            links = conn.execute(
                "SELECT test_point_id FROM test_case_points WHERE test_case_id = ?", (row["id"],)
            ).fetchall()
            result.append(_row_to_test_case(row, [r["test_point_id"] for r in links]))
        return result


def update_test_case_status(tc_id: str, status: str) -> None:
    """更新用例状态（状态机合法性由应用层 TestCase.transition_to 保证）"""
    with v2_conn() as conn:
        conn.execute("UPDATE test_cases SET status = ?, updated_at = datetime('now') WHERE id = ?", (status, tc_id))


def get_test_case_by_fingerprint(fingerprint: str) -> TestCase | None:
    """按业务身份指纹查 TestCase（Step 5 幂等：同 version+mode+test_point_ids 复用旧行）。"""
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM test_cases WHERE fingerprint = ?", (fingerprint,)).fetchone()
        if not row:
            return None
        links = conn.execute(
            "SELECT test_point_id FROM test_case_points WHERE test_case_id = ?", (row["id"],)
        ).fetchall()
        return _row_to_test_case(row, [r["test_point_id"] for r in links])


def list_test_cases_by_version(version_id: str) -> list[TestCase]:
    """按 RequirementVersion 查 TestCase（通过 run.requirement_version_id 联结）。"""
    with v2_read_conn() as conn:
        rows = conn.execute(
            "SELECT tc.* FROM test_cases tc JOIN runs r ON tc.run_id = r.id "
            "WHERE r.requirement_version_id = ? ORDER BY tc.display_id",
            (version_id,),
        ).fetchall()
        result = []
        for row in rows:
            links = conn.execute(
                "SELECT test_point_id FROM test_case_points WHERE test_case_id = ?", (row["id"],)
            ).fetchall()
            result.append(_row_to_test_case(row, [r["test_point_id"] for r in links]))
        return result


def list_test_cases_by_test_point(tp_id: str) -> list[TestCase]:
    """按 TestPoint 反查关联的 TestCase（Step 6 追溯：TP→TC，经 test_case_points）。"""
    with v2_read_conn() as conn:
        tc_ids = [
            r["test_case_id"]
            for r in conn.execute(
                "SELECT test_case_id FROM test_case_points WHERE test_point_id = ?", (tp_id,)
            ).fetchall()
        ]
        result = []
        for tc_id in tc_ids:
            row = conn.execute("SELECT * FROM test_cases WHERE id = ?", (tc_id,)).fetchone()
            if row:
                links = conn.execute(
                    "SELECT test_point_id FROM test_case_points WHERE test_case_id = ?", (tc_id,)
                ).fetchall()
                result.append(_row_to_test_case(row, [r["test_point_id"] for r in links]))
        return result


# ============================================================
# 覆盖义务（+ obligation_coverage 唯一事实源）
# ============================================================


def save_obligation(ob: CoverageObligation) -> None:
    with v2_conn() as conn:
        conn.execute(
            _upsert(
                "coverage_obligations",
                [
                    "id",
                    "run_id",
                    "item_id",
                    "technique",
                    "target",
                    "description",
                    "params_json",
                    "status",
                    "created_at",
                    "updated_at",
                    "schema_version",
                ],
            ),
            (
                ob.id,
                ob.run_id,
                ob.item_id,
                _en(ob.technique),
                ob.target,
                ob.description,
                _j(ob.params),
                _en(ob.status),
                _dt(ob.created_at),
                _dt(ob.updated_at),
                ob.schema_version,
            ),
        )


def get_obligation(ob_id: str) -> CoverageObligation | None:
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM coverage_obligations WHERE id = ?", (ob_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["params"] = _loads(row["params_json"], {})
    data.pop("params_json", None)
    return CoverageObligation.model_validate(data)


def get_obligation_by_natural_key(run_id: str, item_id: str, technique: str, target: str) -> CoverageObligation | None:
    """按业务自然键查 obligation（Step 4 幂等重跑时用于复用旧 id）。

    natural key = (run_id, item_id, technique, target)，在同一 run 下唯一标识一个 obligation。
    Step 4 orchestrator 在持久化前查旧行，若存在则复用旧 ULID，保证下游 TestPoint
    的 fingerprint（含 obligation_id）稳定 → 幂等重跑不产生重复行。
    """
    with v2_read_conn() as conn:
        row = conn.execute(
            "SELECT * FROM coverage_obligations WHERE run_id = ? AND item_id = ? AND technique = ? AND target = ?",
            (run_id, item_id, technique, target),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["params"] = _loads(row["params_json"], {})
    data.pop("params_json", None)
    return CoverageObligation.model_validate(data)


def list_obligations(run_id: str) -> list[CoverageObligation]:
    with v2_read_conn() as conn:
        rows = conn.execute("SELECT * FROM coverage_obligations WHERE run_id = ?", (run_id,)).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        data["params"] = _loads(row["params_json"], {})
        data.pop("params_json", None)
        result.append(CoverageObligation.model_validate(data))
    return result


def add_coverage(obligation_id: str, target_type: TargetType | str, target_id: str) -> None:
    """登记某义务被某测试点/用例覆盖（唯一事实源）。

    多态 target 无 DB 外键，写入前先过 Domain Validator（§6）。
    """
    ReferentialValidator().validate(target_type, target_id)
    with v2_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO obligation_coverage (obligation_id, target_type, target_id) VALUES (?,?,?)",
            (obligation_id, _en(target_type), target_id),
        )


def coverage_targets(obligation_id: str, target_type: TargetType | str) -> list[str]:
    """查询覆盖某义务的指定类型目标 id 列表（供 satisfied_by_* @property 使用）"""
    with v2_read_conn() as conn:
        rows = conn.execute(
            "SELECT target_id FROM obligation_coverage WHERE obligation_id = ? AND target_type = ?",
            (obligation_id, _en(target_type)),
        ).fetchall()
    return [r["target_id"] for r in rows]


def obligation_coverage_ratio(run_id: str) -> float:
    """覆盖率硬指标（代码算）：已覆盖义务数 / 总义务数"""
    with v2_read_conn() as conn:
        total = conn.execute("SELECT COUNT(1) AS n FROM coverage_obligations WHERE run_id = ?", (run_id,)).fetchone()[
            "n"
        ]
        if total == 0:
            return 1.0
        covered = conn.execute(
            "SELECT COUNT(DISTINCT obligation_id) AS n FROM coverage_obligations o "
            "JOIN obligation_coverage c ON c.obligation_id = o.id WHERE o.run_id = ?",
            (run_id,),
        ).fetchone()["n"]
    return round(covered / total, 4)


# ============================================================
# 评审（多轮 + findings 子表）
# ============================================================


def save_review_report(report: ReviewReport) -> None:
    # 多态 finding.target 无 DB 外键，写入前先过 Domain Validator（§6）
    validator = ReferentialValidator()
    for f in report.findings:
        validator.validate(f.target_type, f.target_id)
    with v2_conn() as conn:
        conn.execute(
            _upsert(
                "review_reports",
                [
                    "id",
                    "run_id",
                    "revision",
                    "trigger_type",
                    "scores_json",
                    "overall_score",
                    "summary",
                    "obligation_coverage",
                    "review_target_type",
                    "review_target_ids_json",
                    "coverage_detail_json",
                    "executability_detail_json",
                    "dimension_reasons_json",
                    "created_at",
                    "updated_at",
                    "schema_version",
                ],
            ),
            (
                report.id,
                report.run_id,
                report.revision,
                _en(report.trigger_type),
                report.scores.model_dump_json(),
                report.overall_score,
                report.summary,
                report.obligation_coverage,
                _en(report.review_target_type),
                _j(list(report.review_target_ids)),
                _j(report.coverage_detail.model_dump(mode="json")) if report.coverage_detail else None,
                _j(report.executability_detail.model_dump(mode="json")) if report.executability_detail else None,
                _j(report.dimension_reasons),
                _dt(report.created_at),
                _dt(report.updated_at),
                report.schema_version,
            ),
        )
        conn.execute("DELETE FROM review_findings WHERE report_id = ?", (report.id,))
        for f in report.findings:
            conn.execute(
                """INSERT INTO review_findings
                   (id, report_id, dimension, severity, target_type, target_id, issue,
                    suggestion, provenance, auto_fixable, detail_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f.id,
                    report.id,
                    _en(f.dimension),
                    _en(f.severity),
                    _en(f.target_type),
                    f.target_id,
                    f.issue,
                    f.suggestion,
                    _en(f.provenance),
                    int(f.auto_fixable),
                    _j(f.detail) if f.detail is not None else None,
                ),
            )


def get_review_report(report_id: str) -> ReviewReport | None:
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM review_reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            return None
        frows = conn.execute("SELECT * FROM review_findings WHERE report_id = ?", (report_id,)).fetchall()
    findings = []
    for f in frows:
        fdata = dict(f)
        fdata.pop("report_id", None)
        fdata["auto_fixable"] = bool(f["auto_fixable"])
        fdata["detail"] = _loads(f["detail_json"], None)
        fdata.pop("detail_json", None)
        findings.append(ReviewFinding.model_validate(fdata))
    data = dict(row)
    data["scores"] = _loads(row["scores_json"], {})
    data.pop("scores_json", None)
    data["review_target_ids"] = _loads(row["review_target_ids_json"], [])
    data.pop("review_target_ids_json", None)
    data["coverage_detail"] = _loads(row["coverage_detail_json"], None)
    data.pop("coverage_detail_json", None)
    data["executability_detail"] = _loads(row["executability_detail_json"], None)
    data.pop("executability_detail_json", None)
    data["dimension_reasons"] = _loads(row["dimension_reasons_json"], {})
    data.pop("dimension_reasons_json", None)
    data["findings"] = [f.model_dump(mode="json") for f in findings]
    return ReviewReport.model_validate(data)


def list_review_reports(run_id: str) -> list[ReviewReport]:
    """按 revision 升序返回某 run 的全部评审轮次（P0-4）"""
    with v2_read_conn() as conn:
        rows = conn.execute("SELECT id FROM review_reports WHERE run_id = ? ORDER BY revision", (run_id,)).fetchall()
    reports = []
    for r in rows:
        rep = get_review_report(r["id"])
        if rep:
            reports.append(rep)
    return reports


def get_latest_review_report(run_id: str) -> ReviewReport | None:
    """取某 run 最新一轮评审（max revision），供 revision 递增与 Step 8 消费。"""
    with v2_read_conn() as conn:
        row = conn.execute(
            "SELECT id FROM review_reports WHERE run_id = ? ORDER BY revision DESC LIMIT 1", (run_id,)
        ).fetchone()
    return get_review_report(row["id"]) if row else None


# ============================================================
# 偏好
# ============================================================


def save_preference(pref: Preference) -> None:
    with v2_conn() as conn:
        conn.execute(
            _upsert(
                "preferences",
                [
                    "id",
                    "user_id",
                    "category",
                    "pattern",
                    "weight",
                    "active",
                    "source_diff_json",
                    "created_at",
                    "updated_at",
                    "schema_version",
                ],
            ),
            (
                pref.id,
                pref.user_id,
                pref.category,
                pref.pattern,
                pref.weight,
                int(pref.active),
                _j(pref.source_diff) if pref.source_diff is not None else None,
                _dt(pref.created_at),
                _dt(pref.updated_at),
                pref.schema_version,
            ),
        )


def get_preference(pref_id: str) -> Preference | None:
    with v2_read_conn() as conn:
        row = conn.execute("SELECT * FROM preferences WHERE id = ?", (pref_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["active"] = bool(row["active"])
    data["source_diff"] = _loads(row["source_diff_json"], None)
    data.pop("source_diff_json", None)
    return Preference.model_validate(data)


# ============================================================
# Step 9: TestCaseRevision（人工编辑快照）
# ============================================================


def save_test_case_revision(rev: TestCaseRevision) -> None:
    """保存用例修订快照（幂等：同 test_case_id + revision_no 复用旧 id）。"""
    with v2_conn() as conn:
        conn.execute(
            _upsert(
                "test_case_revisions",
                [
                    "id",
                    "test_case_id",
                    "revision_no",
                    "snapshot_json",
                    "changed_fields_json",
                    "provenance",
                    "changed_by",
                    "change_source",
                    "created_at",
                    "schema_version",
                ],
            ),
            (
                rev.id,
                rev.test_case_id,
                rev.revision_no,
                _j(rev.snapshot),
                _j(rev.changed_fields),
                _en(rev.provenance),
                rev.changed_by,
                rev.change_source,
                _dt(rev.created_at),
                rev.schema_version,
            ),
        )


def _row_to_revision(row: sqlite3.Row) -> TestCaseRevision:
    data = dict(row)
    data["snapshot"] = _loads(row["snapshot_json"], {})
    data.pop("snapshot_json", None)
    data["changed_fields"] = _loads(row["changed_fields_json"], [])
    data.pop("changed_fields_json", None)
    return TestCaseRevision.model_validate(data)


def get_test_case_revisions(test_case_id: str) -> list[TestCaseRevision]:
    """获取某用例的全部修订历史（按 revision_no 升序）。"""
    with v2_read_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM test_case_revisions WHERE test_case_id = ? ORDER BY revision_no", (test_case_id,)
        ).fetchall()
        return [_row_to_revision(r) for r in rows]


def get_latest_revision_no(test_case_id: str) -> int:
    """获取某用例的最新 revision_no（无修订返回 0）。"""
    with v2_read_conn() as conn:
        row = conn.execute(
            "SELECT MAX(revision_no) as max_rev FROM test_case_revisions WHERE test_case_id = ?", (test_case_id,)
        ).fetchone()
        return row["max_rev"] if row and row["max_rev"] is not None else 0


class ConcurrentModificationError(Exception):
    """乐观锁冲突：数据已被其他用户修改。"""

    pass


def update_test_case_with_lock(tc: TestCase, expected_updated_at: str) -> None:
    """乐观锁更新用例：WHERE id=? AND updated_at=?，冲突抛 ConcurrentModificationError。

    ★ 事务完整性：冲突时整体回滚，不产生部分更新或错误 Revision。
    expected_updated_at 为读取时的 updated_at ISO 字符串。
    """
    from datetime import UTC, datetime

    new_updated_at = datetime.now(UTC).isoformat()
    with v2_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE test_cases SET
                module=?, title=?, precondition=?, steps_json=?, expected=?, priority=?, type=?, remark=?,
                provenance=?, status=?, content_hash=?, validation_errors_json=?, data_plan_json=?,
                updated_at=?
            WHERE id=? AND updated_at=?
            """,
            (
                tc.module,
                tc.title,
                tc.precondition,
                _j([s.model_dump(mode="json") for s in tc.steps]),
                tc.expected,
                _en(tc.priority),
                _en(tc.type),
                tc.remark,
                _en(tc.provenance),
                _en(tc.status),
                tc.content_hash,
                _j(tc.validation_errors),
                _j([d.model_dump(mode="json") for d in tc.data_plan]),
                new_updated_at,
                tc.id,
                expected_updated_at,
            ),
        )
        if cursor.rowcount == 0:
            raise ConcurrentModificationError("当前用例已被更新，请刷新后重新编辑")
        tc.updated_at = datetime.fromisoformat(new_updated_at)
