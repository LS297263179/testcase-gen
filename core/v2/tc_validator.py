"""V2 Step 5 测试用例校验器 - 候选 dict → TestCase（先修复，修不了再 FAILED）。

核心策略（用户反馈，对齐 plan D5）：区分**可自动修复**与**不可修复**：
  - 可修复（代码直接修，GENERATED → VALIDATED）：
      seq 不连续 → 重排 1..N；action 空的 step → 丢弃；type/priority 非法 → 枚举兜底；
      module → 覆写为 TestPoint.module；fingerprint/content_hash → 代码计算。
  - 不可修复（GENERATED → VALIDATION_FAILED + validation_errors）：
      steps 全空 / title 空 / expected 空 / test_point_ids 空。

Step 5 硬性覆写（代码强制，不接受 LLM 给的值）：
  - test_point_ids = [tp.id]（1:1 派生）；module = tp.module
  - provenance：mode=LLM→LLM，mode=CODE/HYBRID→STRATEGY
  - confidence_level：CODE→HIGH（确定性代码合成），LLM/HYBRID→MEDIUM
  - status：VALIDATED / VALIDATION_FAILED（由修复结果决定）
  - fingerprint（身份）/ content_hash（内容）：代码计算（见 core/v2/fingerprint.py）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import (
    ConfidenceLevel,
    GenerationMode,
    Priority,
    Provenance,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestPoint,
    TestStep,
)
from core.v2.fingerprint import compute_testcase_content_hash, compute_testcase_fingerprint
from core.v2.tc_generator import TestCaseCandidate

logger = logging.getLogger("v2.tc_validator")


# dimension/technique → TestCaseType 兜底推断（type 非法时用）
_DIMENSION_TO_TYPE = {
    "boundary": TestCaseType.BOUNDARY,
    "equivalence": TestCaseType.EQUIVALENCE,
    "permission": TestCaseType.PERMISSION,
    "exception": TestCaseType.EXCEPTION,
    "interaction": TestCaseType.LINKAGE,
    "linkage": TestCaseType.LINKAGE,
    "state": TestCaseType.STATE,
    "security": TestCaseType.SECURITY,
    "compatibility": TestCaseType.COMPATIBILITY,
    "performance": TestCaseType.PERFORMANCE,
}


@dataclass
class BuildResult:
    """校验/修复产物：一条 TestCase（已设 status/fingerprint/content_hash）+ 问题记录。"""

    test_case: TestCase | None = None
    status: TestCaseStatus = TestCaseStatus.VALIDATION_FAILED
    issues: list[str] = field(default_factory=list)  # 可修复项的修复记录（软提示）
    validation_errors: list[str] = field(default_factory=list)  # 不可修复原因（硬错误）


# ============================================================
# 修复 helper
# ============================================================


def _coerce_optional_str(v: object) -> str | None:
    """data/expected 字段：None 保留，其余转 str。"""
    if v is None:
        return None
    return str(v)


def _repair_steps(raw_steps: object, issues: list[str]) -> list[TestStep]:
    """修复 steps：丢弃非对象/action 空的 step，seq 重排 1..N（可修复项）。"""
    if not isinstance(raw_steps, list):
        if raw_steps not in (None, ""):
            issues.append("steps 不是数组，已置空")
        return []
    cleaned: list[dict] = []
    for s in raw_steps:
        if not isinstance(s, dict):
            issues.append("丢弃非对象 step 元素")
            continue
        action = s.get("action")
        if action is None or not str(action).strip():
            issues.append("丢弃 action 为空的 step")
            continue
        cleaned.append(
            {
                "action": str(action).strip(),
                "data": _coerce_optional_str(s.get("data")),
                "expected": _coerce_optional_str(s.get("expected")),
            }
        )
    # seq 重排 1..N（代码强制连续，忽略 LLM 给的 seq）
    return [
        TestStep(seq=i + 1, action=c["action"], data=c["data"], expected=c["expected"]) for i, c in enumerate(cleaned)
    ]


def _repair_type(raw_type: object, tp: TestPoint, issues: list[str]) -> TestCaseType:
    """type 非法 → 兜底（先按 dimension 推断，再退 FUNCTIONAL）。"""
    if raw_type is not None:
        try:
            return TestCaseType(str(raw_type))
        except ValueError:
            issues.append(f"type={raw_type!r} 非法，已兜底")
    dim = tp.dimension.value if hasattr(tp.dimension, "value") else str(tp.dimension)
    return _DIMENSION_TO_TYPE.get(dim, TestCaseType.FUNCTIONAL)


def _repair_priority(raw_priority: object, tp: TestPoint, issues: list[str]) -> Priority:
    """priority 非法 → 沿用 TestPoint.priority。"""
    if raw_priority is not None:
        try:
            return Priority(str(raw_priority))
        except ValueError:
            issues.append(f"priority={raw_priority!r} 非法，已沿用 TestPoint.priority")
    return tp.priority


def _derive_provenance(mode: GenerationMode) -> Provenance:
    """generation_mode → TestCase.provenance：LLM 合成→LLM；代码模板合成→STRATEGY。"""
    return Provenance.LLM if mode == GenerationMode.LLM else Provenance.STRATEGY


def _derive_confidence(mode: GenerationMode) -> ConfidenceLevel:
    """CODE（确定性代码合成）→ HIGH；LLM/HYBRID → MEDIUM（对齐 Step 1 §7 置信度表）。"""
    return ConfidenceLevel.HIGH if mode == GenerationMode.CODE else ConfidenceLevel.MEDIUM


# ============================================================
# 主校验/构建入口
# ============================================================


def validate_and_build(
    candidate: TestCaseCandidate,
    *,
    tp: TestPoint,
    run_id: str,
    version_id: str | None,
    display_id: str,
) -> BuildResult:
    """把 TestCaseCandidate 校验/修复为一条 TestCase（含 status/fingerprint/content_hash）。

    流程：
      1. 修复 steps（丢弃空 action + seq 重排）、type/priority 枚举兜底、module 覆写
      2. 硬性覆写：test_point_ids=[tp.id]、provenance、confidence_level、generation_mode、data_plan
      3. 判定不可修复项（steps/title/expected/test_point_ids 空）→ validation_errors
      4. 构建 TestCase（status=GENERATED）→ 计算 fingerprint（身份）+ content_hash（内容）
      5. 状态转移：无 validation_errors → VALIDATED；否则 → VALIDATION_FAILED（保存 errors）
    """
    issues = list(candidate.issues)
    raw = candidate.raw or {}
    mode = candidate.generation_mode

    # 1. 修复可修复项
    steps = _repair_steps(raw.get("steps"), issues)
    tc_type = _repair_type(raw.get("type"), tp, issues)
    priority = _repair_priority(raw.get("priority"), tp, issues)
    title = str(raw.get("title") or "").strip()
    expected = str(raw.get("expected") or "").strip()
    precondition = str(raw.get("precondition") or "")
    remark = str(raw.get("remark") or "")
    module = tp.module  # 硬性覆写：module 来自 TestPoint，不接受 LLM 改写

    # 2. 硬性覆写 test_point_ids（1:1 派生）
    test_point_ids = [tp.id]

    # 3. 判定不可修复项
    validation_errors: list[str] = []
    if not steps:
        validation_errors.append("steps 为空（无有效步骤，无法执行）")
    if not title:
        validation_errors.append("title 为空")
    if not expected:
        validation_errors.append("expected 为空")
    if not test_point_ids:
        validation_errors.append("test_point_ids 为空（1:1 派生应关联 1 个 TestPoint）")

    # 4. 构建 TestCase（status 默认 GENERATED）
    try:
        tc = TestCase(
            run_id=run_id,
            display_id=display_id,
            test_point_ids=test_point_ids,
            module=module,
            title=title,
            precondition=precondition,
            steps=steps,
            expected=expected,
            priority=priority,
            type=tc_type,
            remark=remark,
            provenance=_derive_provenance(mode),
            confidence_level=_derive_confidence(mode),
            generation_mode=mode,
            data_plan=list(candidate.data_plan),
        )
    except Exception as e:  # noqa: BLE001 - Pydantic 构建失败视为不可修复
        logger.warning("TestCase 构建失败 tp=%s: %s", tp.id, e)
        return BuildResult(
            test_case=None,
            status=TestCaseStatus.VALIDATION_FAILED,
            issues=issues,
            validation_errors=validation_errors + [f"TestCase Pydantic 构建失败: {e}"],
        )

    # 计算 fingerprint（身份，基于 version_id + generation_mode + test_point_ids）
    tc.fingerprint = compute_testcase_fingerprint(
        version_id=version_id,
        generation_mode=mode.value if hasattr(mode, "value") else str(mode),
        test_point_ids=test_point_ids,
    )
    # 计算 content_hash（内容，基于 title/precondition/steps/expected/type/priority）
    tc.content_hash = compute_testcase_content_hash(
        title=tc.title,
        precondition=tc.precondition,
        steps_text=tc.render_steps_text(),
        expected=tc.expected,
        type=tc.type.value if hasattr(tc.type, "value") else str(tc.type),
        priority=tc.priority.value if hasattr(tc.priority, "value") else str(tc.priority),
    )

    # 5. 状态转移（GENERATED → VALIDATED / VALIDATION_FAILED）
    if validation_errors:
        tc.validation_errors = validation_errors
        tc.transition_to(TestCaseStatus.VALIDATION_FAILED)
        status = TestCaseStatus.VALIDATION_FAILED
    else:
        tc.transition_to(TestCaseStatus.VALIDATED)
        status = TestCaseStatus.VALIDATED

    return BuildResult(test_case=tc, status=status, issues=issues, validation_errors=validation_errors)


# ============================================================
# 去重（按 fingerprint 严格去重）
# ============================================================


def dedupe_cases(cases: list[TestCase]) -> tuple[list[TestCase], list[str]]:
    """按 fingerprint 严格去重（同一 version+mode+test_point_ids 只保留首个）。

    1:1 派生下正常不会重复；此函数防御 orchestrator 层面同一 TestPoint 被处理两次。
    fingerprint 为 None 的（异常）不参与去重，全部保留。
    """
    seen: set[str] = set()
    result: list[TestCase] = []
    issues: list[str] = []
    for tc in cases:
        if tc.fingerprint and tc.fingerprint in seen:
            issues.append(f"重复 fingerprint 丢弃: {tc.fingerprint} title={tc.title}")
            continue
        if tc.fingerprint:
            seen.add(tc.fingerprint)
        result.append(tc)
    return result, issues
