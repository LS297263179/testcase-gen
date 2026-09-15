"""V1 → V2 数据迁移。

严格遵循 docs/v2/step1-data-model.md §9.1 铁律：
  绝不为"完整追溯"而让 AI 猜历史关系；无法确定的 = NULL；provenance = migrated。

映射：
  V1 users        → V2 users（分配 ULID，保留 legacy_int_id）
  V1 sessions     → RequirementDoc + RequirementVersion(v1) + Run + TestCase[] (+ ReviewReport summary)
  V1 test_points  → RequirementDoc + RequirementVersion(v1) + TestPoint[]（run_id=NULL，不猜关联）
  V1 preferences  → Preference（仅当 user 可映射）
  V1 materials/*_images → materials/assets（原样搬迁）
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Callable

from core.schemas import (
    GenerationConfig,
    Preference,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementVersion,
    ReviewReport,
    ReviewScores,
    ReviewTriggerType,
    Run,
    RunCounts,
    RunStatus,
    SourceType,
    TestCase,
    TestCaseType,
    TestDimension,
    TestPoint,
    TestStep,
    new_ulid,
)
from core.v2 import repository as repo
from core.v2.ddl import create_v2_schema

logger = logging.getLogger("v2.migrate")

# V1 中文用例类型 → V2 TestCaseType（未知归 FUNCTIONAL）
_TYPE_MAP: dict[str, TestCaseType] = {
    "功能测试": TestCaseType.FUNCTIONAL,
    "边界测试": TestCaseType.BOUNDARY,
    "异常测试": TestCaseType.EXCEPTION,
    "兼容性测试": TestCaseType.COMPATIBILITY,
    "性能测试": TestCaseType.PERFORMANCE,
    "安全测试": TestCaseType.SECURITY,
    "权限测试": TestCaseType.PERMISSION,
    "状态测试": TestCaseType.STATE,
    "联动测试": TestCaseType.LINKAGE,
    "等价类": TestCaseType.EQUIVALENCE,
    "交互测试": TestCaseType.FUNCTIONAL,
    "数据校验": TestCaseType.FUNCTIONAL,
}

_VALID_PRIORITIES = {p.value for p in Priority}


def _parse_json(raw: str | None, default: object) -> object:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _parse_steps(text: str | None) -> list[TestStep]:
    """V1 换行步骤文本 → 结构化 TestStep[]（去掉序号前缀）"""
    steps: list[TestStep] = []
    if not text:
        return steps
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    for i, line in enumerate(lines, 1):
        action = re.sub(r"^\d+[.、)\s]*", "", line).strip() or line
        steps.append(TestStep(seq=i, action=action))
    return steps


def _map_priority(raw: str | None) -> Priority:
    return Priority(raw) if raw in _VALID_PRIORITIES else Priority.P1


def _map_type(raw: str | None) -> TestCaseType:
    return _TYPE_MAP.get(raw or "", TestCaseType.FUNCTIONAL)


def _placeholder_config() -> GenerationConfig:
    """迁移占位生成配置：V1 无审计信息，按 §9.1 标 unknown，不编造。"""
    return GenerationConfig(
        model_provider="unknown",
        model_name="unknown",
        temperature=0.0,
        prompt_version="migrated-unknown",
        generator_version="1.0.0",
        reviewer_version=None,
    )


def _normalize_points(raw_points: list) -> list[tuple[str, str, str, str]]:
    """兼容 V1 新旧测试点格式，展平为 (module, subcategory, title, description)"""
    flat: list[tuple[str, str, str, str]] = []
    for m in raw_points:
        if not isinstance(m, dict):
            continue
        module = m.get("module", "未分类")
        subcats = m.get("subcategories")
        if subcats:  # 新格式
            for sc in subcats:
                sc_name = sc.get("name", "测试点")
                for p in sc.get("points", []):
                    flat.append((module, sc_name, p.get("title", ""), p.get("description", "")))
        elif "points" in m:  # 旧格式
            for p in m.get("points", []):
                flat.append((module, "测试点", p.get("title", ""), p.get("description", "")))
    return flat


def migrate(v1_db_path: str, *, create_schema: bool = True) -> dict:
    """读 V1 db（只读）→ 写 V2 db（路径需事先 set_v2_db_path）。返回计数统计。"""
    if create_schema:
        create_v2_schema()

    stats = {
        "users": 0,
        "docs": 0,
        "versions": 0,
        "runs": 0,
        "test_cases": 0,
        "test_points": 0,
        "preferences": 0,
        "materials": 0,
        "assets": 0,
        "review_reports": 0,
        "legacy_users": 0,
        "orphan_records": 0,
    }

    v1 = sqlite3.connect(f"file:{v1_db_path}?mode=ro", uri=True)
    v1.row_factory = sqlite3.Row
    try:
        # 1. users：old int id → ULID
        user_map: dict[int, str] = {}
        for u in v1.execute("SELECT id, username, password_hash FROM users"):
            uid = new_ulid()
            user_map[u["id"]] = uid
            repo.save_user(uid, u["username"], u["password_hash"], legacy_int_id=u["id"])
            stats["users"] += 1

        # 迁移占位生成配置（所有迁移 run 共用）
        cfg = _placeholder_config()
        repo.save_generation_config(cfg)

        # 孤儿记录（user_id 为 NULL 或指向不存在的用户）→ 归入合成 legacy 用户，
        # 既不丢数据，也不臆测具体归属（遵 §9.1 铁律）
        legacy_uid: dict[str, str] = {}

        def resolve_owner(raw_user_id: int | None) -> str:
            owner = user_map.get(raw_user_id)
            if owner is not None:
                return owner
            if "uid" not in legacy_uid:
                uid = new_ulid()
                repo.save_user(uid, "__legacy_unknown__", "!", legacy_int_id=None)
                legacy_uid["uid"] = uid
                stats["users"] += 1
                stats["legacy_users"] = 1
            stats["orphan_records"] += 1
            return legacy_uid["uid"]

        # 2. sessions → doc + version + run + cases (+ review summary)
        for s in v1.execute("SELECT * FROM sessions WHERE is_deleted = 0"):
            owner = resolve_owner(s["user_id"])
            requirement = s["requirement"] or ""
            doc = RequirementDoc(
                user_id=owner,
                title=(requirement.strip()[:30] or "迁移需求"),
                source_type=SourceType.TEXT,
            )
            repo.save_doc(doc)
            stats["docs"] += 1

            ver = RequirementVersion(
                doc_id=doc.id,
                version_no=1,
                raw_text=requirement,
                provenance=Provenance.MIGRATED,
            )
            repo.save_version(ver)
            stats["versions"] += 1

            raw_cases = _parse_json(s["testcases"], [])
            run = Run(
                user_id=owner,
                doc_id=doc.id,
                requirement_version_id=ver.id,
                generation_config_id=cfg.id,
                status=RunStatus.DONE,
                counts=RunCounts(cases=len(raw_cases)),
                legacy_session_id=s["id"],
            )
            repo.save_run(run)
            stats["runs"] += 1

            for i, tc in enumerate(raw_cases, 1):
                if not isinstance(tc, dict):
                    continue
                case = TestCase(
                    run_id=run.id,
                    display_id=str(tc.get("id") or f"TC_{i:03d}"),
                    module=tc.get("module", "未分类"),
                    title=tc.get("title", ""),
                    precondition=tc.get("precondition", "") or "",
                    steps=_parse_steps(tc.get("steps", "")),
                    expected=tc.get("expected", "") or "",
                    priority=_map_priority(tc.get("priority")),
                    type=_map_type(tc.get("type")),
                    remark=tc.get("remark", "") or "",
                    provenance=Provenance.MIGRATED,
                )
                repo.save_test_case(case)
                stats["test_cases"] += 1

            # 评审自由文本 → summary（无结构化分数，置 0 表"未评分"）
            review_text = s["review_report"]
            if review_text and review_text.strip():
                zero = ReviewScores(
                    coverage=0, accuracy=0, executability=0, consistency=0, missing_risk=0, duplication=0
                )
                report = ReviewReport(
                    run_id=run.id,
                    revision=1,
                    trigger_type=ReviewTriggerType.INITIAL,
                    scores=zero,
                    overall_score=0.0,
                    summary=review_text,
                    obligation_coverage=0.0,
                )
                repo.save_review_report(report)
                stats["review_reports"] += 1

        # 3. test_points → doc + version + points（run_id=NULL，不猜关联）
        for tp in v1.execute("SELECT * FROM test_points"):
            owner = resolve_owner(tp["user_id"])
            requirement = tp["requirement"] or ""
            doc = RequirementDoc(user_id=owner, title=tp["title"] or "迁移测试点", source_type=SourceType.TEXT)
            repo.save_doc(doc)
            stats["docs"] += 1
            ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text=requirement, provenance=Provenance.MIGRATED)
            repo.save_version(ver)
            stats["versions"] += 1

            for module, subcat, title, desc in _normalize_points(_parse_json(tp["points"], [])):
                point = TestPoint(
                    run_id=None,
                    version_id=ver.id,
                    item_ids=[],
                    module=module,
                    subcategory=subcat,
                    title=title,
                    description=desc,
                    dimension=TestDimension.FUNCTIONAL,
                    provenance=Provenance.MIGRATED,
                )
                repo.save_test_point(point)
                stats["test_points"] += 1

        # 4. preferences（孤儿归入 legacy 用户，不丢弃）
        for p in v1.execute("SELECT * FROM preferences"):
            owner = resolve_owner(p["user_id"])
            pref = Preference(
                user_id=owner,
                category=p["category"],
                pattern=p["pattern"],
                weight=p["weight"] if p["weight"] is not None else 1.0,
                active=bool(p["active"]),
                source_diff=_parse_json(p["source_diff"], None),
            )
            repo.save_preference(pref)
            stats["preferences"] += 1

        # 5. materials + images → materials/assets（原样搬迁，无 Pydantic 模型）
        _migrate_materials(v1, resolve_owner, stats)
    finally:
        v1.close()

    logger.info("V1→V2 迁移完成：%s", stats)
    return stats


def _migrate_materials(v1: sqlite3.Connection, resolve_owner: Callable[[int | None], str], stats: dict) -> None:
    """materials 与图片资源搬迁（简单表，直接 SQL）"""
    from core.v2.db import v2_conn

    with v2_conn() as conn:
        for m in v1.execute("SELECT * FROM materials"):
            owner = resolve_owner(m["user_id"])
            mid = new_ulid()
            conn.execute(
                "INSERT OR REPLACE INTO materials (id, user_id, title, content, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (mid, owner, m["title"], m["content"], m["created_at"], m["created_at"]),
            )
            stats["materials"] += 1
        # 图片资源（session_images / material_images）→ assets
        for tbl, owner_col, owner_kind in (
            ("material_images", "material_id", "material"),
            ("session_images", "session_id", "session"),
        ):
            if not _table_exists(v1, tbl):
                continue
            for img in v1.execute(f"SELECT * FROM {tbl}"):  # 表名来自固定白名单，非用户输入
                aid = new_ulid()
                conn.execute(
                    "INSERT OR REPLACE INTO assets (id, owner_type, owner_id, filename, media_type, data, sort_order) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        aid,
                        owner_kind,
                        str(img[owner_col]),
                        img["filename"],
                        img["media_type"],
                        img["data"],
                        img["sort_order"],
                    ),
                )
                stats["assets"] += 1


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None
