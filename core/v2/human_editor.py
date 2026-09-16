"""V2 Step 9 Human Editor - 人工编辑 TestCase + Revision 快照 + Validator 校验。

对应 PROGRESS.md §9 蓝图 `Human Review(人工确认/编辑)`。职责：
  - 编辑白名单过滤（防止越权修改 test_point_ids / 系统字段）
  - 编辑前创建 Revision 快照（保存修改前完整 TestCase + changed_fields）
  - 乐观锁并发控制（updated_at 冲突 → 整体事务回滚）
  - identity fingerprint 不变，content_hash 重算
  - 编辑后必须过 Validator（FAIL→VALIDATION_FAILED，PASS→RE_REVIEW_REQUIRED）
  - provenance=HUMAN（当前生效版本来源）

★ Step 9 铁律（用户冻结）：
  - 不自动重评审（保存仅标记 RE_REVIEW_REQUIRED，用户主动点击才 Review）
  - VALIDATION_FAILED 可恢复（用户重新编辑 → EDITED → Validator）
  - Revision 保存修改前快照（不是修改后），changed_fields 为 Step 10 数据源
  - 乐观锁冲突时整体回滚（无错误 Revision，无半截 TestCase）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import (
    Provenance,
    TestCase,
    TestCaseRevision,
    TestCaseStatus,
)
from core.v2 import repository as repo
from core.v2.fingerprint import compute_testcase_content_hash

logger = logging.getLogger("v2.human_editor")

# ============================================================
# 编辑白名单（用户冻结）
# ============================================================

# 可人工修改的字段
EDITABLE_FIELDS: set[str] = {
    "title",
    "steps",
    "expected",
    "precondition",
    "priority",
    "module",
    "remark",
}

# 系统字段（不可通过普通编辑接口修改）
SYSTEM_FIELDS: set[str] = {
    "id",
    "run_id",
    "version_id",
    "fingerprint",
    "created_at",
    "schema_version",
    "test_point_ids",  # 追溯链由系统维护，Re-link 留未来
    "display_id",
    "type",
    "provenance",
    "status",
    "confidence_level",
    "generation_mode",
    "content_hash",
    "validation_errors",
    "data_plan",
    "updated_at",
}


# ============================================================
# 编辑结果
# ============================================================


@dataclass
class EditResult:
    """人工编辑产物（对齐 Step 3/4/5/6/7/8 的 *Result 约定）。"""

    test_case_id: str
    success: bool = False
    revision_no: int = 0  # 新创建的 Revision 编号
    changed_fields: list[str] = field(default_factory=list)
    new_status: TestCaseStatus | None = None
    rejected_fields: list[str] = field(default_factory=list)  # 被拒绝的系统字段
    validation_errors: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


# ============================================================
# 白名单过滤
# ============================================================


def filter_edit_payload(payload: dict) -> tuple[dict, list[str]]:
    """过滤编辑载荷：只保留白名单字段，返回 (filtered_updates, rejected_fields)。

    防止前端越权修改 test_point_ids / id / fingerprint 等系统字段。
    """
    updates = {k: v for k, v in payload.items() if k in EDITABLE_FIELDS}
    rejected = [k for k in payload if k not in EDITABLE_FIELDS]
    return updates, rejected


# ============================================================
# Revision 快照 + changed_fields
# ============================================================


def create_revision_snapshot(tc: TestCase, updates: dict, revision_no: int) -> TestCaseRevision:
    """创建修改前快照 + 计算 changed_fields。

    ★ snapshot 保存的是"修改前的完整 TestCase"（不是修改后）。
    ★ changed_fields 记录哪些字段实际被修改（值不同），为 Step 10 Preference Learning 提供数据源。
    """
    # 修改前完整快照
    snapshot = tc.model_dump(mode="json")

    # 计算实际变化的字段（值不同才算 changed）
    changed_fields: list[str] = []
    for key, new_value in updates.items():
        old_value = getattr(tc, key, None)
        # steps 需要特殊处理（list[TestStep] vs list[dict]）
        if key == "steps":
            old_steps = [s.model_dump(mode="json") for s in (old_value or [])]
            if old_steps != new_value:
                changed_fields.append(key)
        elif old_value != new_value:
            changed_fields.append(key)

    return TestCaseRevision(
        test_case_id=tc.id,
        revision_no=revision_no,
        snapshot=snapshot,
        changed_fields=changed_fields,
        provenance=Provenance.HUMAN,
        changed_by="user",
        change_source="human_edit",
    )


# ============================================================
# 编辑后 Validator（复用 Step 5 tc_validator 逻辑）
# ============================================================


def validate_edited_case(tc: TestCase) -> tuple[bool, list[str]]:
    """编辑后校验：结构完整性检查（steps 非空 / action 非空 / expected 非空 / title 非空）。

    返回 (is_valid, errors)。FAIL → VALIDATION_FAILED，不进入 RE_REVIEW_REQUIRED。
    ★ 不重新计算 fingerprint（identity 不变），只校验结构。
    """
    errors: list[str] = []

    if not (tc.title or "").strip():
        errors.append("title 不能为空")
    if not tc.steps:
        errors.append("steps 不能为空")
    else:
        for i, step in enumerate(tc.steps):
            if not (step.action or "").strip():
                errors.append(f"第 {i + 1} 步 action 不能为空")
    if not (tc.expected or "").strip():
        errors.append("expected 不能为空")
    if not (tc.module or "").strip():
        errors.append("module 不能为空")

    return len(errors) == 0, errors


# ============================================================
# 编辑主逻辑
# ============================================================


def edit_test_case(
    test_case_id: str,
    updates: dict,
    *,
    expected_updated_at: str | None = None,
) -> EditResult:
    """人工编辑 TestCase 主入口。

    流程（用户冻结版）：
      1. 拉 TestCase（必须存在）
      2. 白名单过滤（拒绝系统字段修改）
      3. 乐观锁检查（expected_updated_at 冲突 → 整体回滚）
      4. Create Pre-Edit Snapshot（Revision revision_no+1）
      5. Apply updates（title/steps/expected/...）
      6. provenance=HUMAN，fingerprint 不变，content_hash 重算
      7. status=EDITED → Validator
         - FAIL → VALIDATION_FAILED（可恢复）
         - PASS → RE_REVIEW_REQUIRED
      8. 持久化（Revision + TestCase）

    ★ 不自动触发重评审（用户主动点击才 Review）。
    ★ 乐观锁冲突时整体回滚（无错误 Revision，无半截 TestCase）。
    """
    result = EditResult(test_case_id=test_case_id)

    # 1. 拉 TestCase
    tc = repo.get_test_case(test_case_id)
    if tc is None:
        result.issues.append(f"TestCase 不存在: {test_case_id}")
        return result

    # 记录原始 updated_at（乐观锁用）
    original_updated_at = tc.updated_at.isoformat() if hasattr(tc.updated_at, "isoformat") else str(tc.updated_at)
    if expected_updated_at is None:
        expected_updated_at = original_updated_at

    # 2. 白名单过滤
    filtered_updates, rejected = filter_edit_payload(updates)
    result.rejected_fields = rejected
    if rejected:
        logger.warning("Step 9 编辑: 拒绝系统字段修改 %s", rejected)

    if not filtered_updates:
        result.issues.append("无有效编辑字段（全部被拒绝或为空）")
        return result

    # 3. 状态检查：REVIEWED / VALIDATION_FAILED / RE_REVIEW_REQUIRED 可编辑
    if tc.status not in (TestCaseStatus.REVIEWED, TestCaseStatus.VALIDATION_FAILED, TestCaseStatus.RE_REVIEW_REQUIRED):
        result.issues.append(
            f"当前状态 {tc.status.value} 不允许编辑（仅 REVIEWED/VALIDATION_FAILED/RE_REVIEW_REQUIRED 可编辑）"
        )
        return result

    # 4. Create Pre-Edit Snapshot（修改前快照）
    latest_rev_no = repo.get_latest_revision_no(test_case_id)
    new_rev_no = latest_rev_no + 1
    revision = create_revision_snapshot(tc, filtered_updates, new_rev_no)
    result.revision_no = new_rev_no
    result.changed_fields = revision.changed_fields

    # 5. Apply updates
    for key, value in filtered_updates.items():
        if key == "steps":
            # steps 需要从 dict 重建 TestStep
            from core.schemas import TestStep

            tc.steps = [TestStep.model_validate(s) for s in value]
        else:
            setattr(tc, key, value)

    # 6. provenance=HUMAN，fingerprint 不变，content_hash 重算
    tc.provenance = Provenance.HUMAN
    # fingerprint 保持不变（identity 不变，还是同一 TestCase）
    tc.content_hash = compute_testcase_content_hash(
        title=tc.title,
        precondition=tc.precondition,
        steps_text=tc.render_steps_text(),
        expected=tc.expected,
        type=tc.type.value if hasattr(tc.type, "value") else str(tc.type),
        priority=tc.priority.value if hasattr(tc.priority, "value") else str(tc.priority),
    )

    # 7. status=EDITED → Validator
    tc.status = TestCaseStatus.EDITED
    is_valid, errors = validate_edited_case(tc)
    if is_valid:
        tc.status = TestCaseStatus.RE_REVIEW_REQUIRED
        result.new_status = TestCaseStatus.RE_REVIEW_REQUIRED
    else:
        tc.status = TestCaseStatus.VALIDATION_FAILED
        tc.validation_errors = errors
        result.new_status = TestCaseStatus.VALIDATION_FAILED
        result.validation_errors = errors

    # 8. 持久化（乐观锁 + Revision + TestCase）
    try:
        # 乐观锁更新（冲突抛 ConcurrentModificationError，整体回滚）
        repo.update_test_case_with_lock(tc, expected_updated_at)
        # Revision 保存（只在 TestCase 更新成功后）
        repo.save_test_case_revision(revision)
        result.success = True
        logger.info(
            "Step 9 编辑成功: tc=%s revision=%d changed=%s status=%s",
            test_case_id,
            new_rev_no,
            revision.changed_fields,
            tc.status.value,
        )
    except repo.ConcurrentModificationError as e:
        result.issues.append(str(e))
        logger.warning("Step 9 编辑冲突: tc=%s error=%s", test_case_id, e)
        return result

    return result
