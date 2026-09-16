"""V2 Step 7 LLM Soft Review Engine - 软维度评审（accuracy / missing_risk / executability 语义层）。

★ 分工：只评代码算不了的 3 个软维度；硬维度（coverage/duplication/consistency/executability 结构层）
由 review_hard.py 代码算。每个软维度 LLM 必须给 score + reason + findings（可解释性，用户核心要求）。

★ 证据锚定 + 代码校验（用户核心要求）：LLM 的 finding.target_ref 必须解析为真实存在的
TestCase/RequirementItem（支持 ULID 或 display_id），经 TargetResolver 校验；非法 target 丢弃并记 issue。
LLM 不能自证——soft score 的可信度靠 reason + findings + 证据锚定，硬指标不由 LLM 决定。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from core.schemas import (
    Provenance,
    RequirementItem,
    ReviewDimension,
    ReviewFinding,
    Severity,
    TargetType,
    TestCase,
)
from core.v2.parser import extract_json
from core.v2.resolver import TargetResolver
from core.v2.review_prompts import REVIEW_USER_TEMPLATE, TEST_CASE_REVIEWER_PROMPT

logger = logging.getLogger("v2.review_soft")

# LLM 软维度评审失败/缺失时的中性默认分（记录 issue，不假装高质量；供降级可用）
DEFAULT_SOFT_SCORE_ON_FAILURE = 50.0

# LLM 软维度名 → ReviewDimension 映射（executability_semantic 归入 EXECUTABILITY 维度的语义子分）
_SOFT_DIM_TO_REVIEW = {
    "accuracy": ReviewDimension.ACCURACY,
    "missing_risk": ReviewDimension.MISSING_RISK,
    "executability_semantic": ReviewDimension.EXECUTABILITY,
}

_VALID_SEVERITIES = {s.value for s in Severity}


@dataclass
class SoftReviewResult:
    """LLM 软维度评审产物。"""

    accuracy_score: float = DEFAULT_SOFT_SCORE_ON_FAILURE
    missing_risk_score: float = DEFAULT_SOFT_SCORE_ON_FAILURE
    executability_semantic_score: float = DEFAULT_SOFT_SCORE_ON_FAILURE
    findings: list[ReviewFinding] = field(default_factory=list)  # provenance=LLM
    dimension_reasons: dict[str, str] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    calls: int = 0


# ============================================================
# 序列化（带真实 ULID + display_id 供 LLM 引用证据）
# ============================================================


def _serialize_cases(cases: list[TestCase]) -> str:
    data = [
        {
            "id": c.id,
            "display_id": c.display_id,
            "module": c.module,
            "title": c.title,
            "precondition": c.precondition,
            "steps": c.render_steps_text(),
            "expected": c.expected,
            "type": c.type.value if hasattr(c.type, "value") else str(c.type),
            "priority": c.priority.value if hasattr(c.priority, "value") else str(c.priority),
        }
        for c in cases
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)


def _serialize_items(items: list[RequirementItem]) -> str:
    data = [
        {
            "id": it.id,
            "module": it.module,
            "statement": it.statement,
            "rules": [{"name": r.name, "expression": r.expression} for r in it.rules],
            "permissions": [
                {"role": p.role, "resource": p.resource, "action": p.action, "allowed": p.allowed}
                for p in it.permissions
            ],
            "acceptance_criteria": it.acceptance_criteria,
            "source_ref": (it.source_ref.value if it.source_ref else None),
        }
        for it in items
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)


# ============================================================
# finding 校验（证据锚定）
# ============================================================


def _clamp_score(raw: object) -> float:
    """score clamp 到 [0,100]；非法值退回中性默认分。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SOFT_SCORE_ON_FAILURE
    return max(0.0, min(100.0, v))


def _resolve_target(
    resolver: TargetResolver,
    target_type_raw: object,
    target_ref: object,
    case_id_map: dict[str, str],
    item_ids: set[str],
) -> tuple[TargetType, str] | None:
    """把 LLM 给的 (target_type, target_ref) 解析为真实存在的 (TargetType, id)；非法返回 None。

    - target_type 必须是 testcase / requirement_item
    - target_ref 支持 ULID 或 display_id（case_id_map 把 display_id 映射回 id）
    - 最终经 TargetResolver.exists 权威校验存在性
    """
    try:
        tt = TargetType(str(target_type_raw))
    except ValueError:
        return None
    if tt not in (TargetType.TESTCASE, TargetType.REQUIREMENT_ITEM):
        return None
    ref = str(target_ref or "")
    if not ref:
        return None
    if tt == TargetType.TESTCASE:
        resolved = case_id_map.get(ref, ref)  # display_id → id，或直接是 id
        # 不在本次评审范围时，再用 resolver 兜底确认是否为库中真实 case
        if resolved not in case_id_map.values() and not resolver.exists(tt, resolved):
            return None
    else:  # REQUIREMENT_ITEM
        resolved = ref
        if resolved not in item_ids and not resolver.exists(tt, resolved):
            return None
    return tt, resolved


def _build_finding(
    dim: ReviewDimension,
    raw_finding: dict,
    resolver: TargetResolver,
    case_id_map: dict,
    item_ids: set,
    issues: list[str],
) -> ReviewFinding | None:
    """把 LLM 的单条 finding dict 校验/组装为 ReviewFinding；非法则丢弃并记 issue。"""
    target = _resolve_target(
        resolver, raw_finding.get("target_type"), raw_finding.get("target_ref"), case_id_map, item_ids
    )
    if target is None:
        issues.append(
            f"丢弃非法 target 的 {dim.value} finding: target_type={raw_finding.get('target_type')} "
            f"target_ref={raw_finding.get('target_ref')}（不存在或越界，证据锚定失败）"
        )
        return None
    target_type, target_id = target
    issue_text = str(raw_finding.get("issue") or "").strip()
    if not issue_text:
        issues.append(f"丢弃 issue 为空的 {dim.value} finding")
        return None
    severity_raw = str(raw_finding.get("severity") or "").lower()
    severity = Severity(severity_raw) if severity_raw in _VALID_SEVERITIES else Severity.MINOR
    detail = raw_finding.get("detail")
    if not isinstance(detail, dict):
        detail = None
    suggestion = raw_finding.get("suggestion")
    return ReviewFinding(
        dimension=dim,
        severity=severity,
        target_type=target_type,
        target_id=target_id,
        issue=issue_text,
        suggestion=str(suggestion) if suggestion else None,
        provenance=Provenance.LLM,
        auto_fixable=False,  # 软维度问题多需语义修复/重生成，非机械可修（Step 8/人工处理）
        detail=detail,
    )


# ============================================================
# 主入口
# ============================================================


def run_soft_review(client, test_cases: list[TestCase], items: list[RequirementItem]) -> SoftReviewResult:
    """调 LLM 评 3 个软维度，解析 + 证据校验 → SoftReviewResult。

    - LLM 调用/解析失败：软分用中性默认 + 记 issue（降级可用，不假装高质量）
    - 每个软维度：score(clamp) + reason(可解释) + findings(证据锚定，非法丢弃)
    - 缺失的维度：用中性默认分 + 记 issue
    """
    result = SoftReviewResult()
    if not test_cases:
        result.issues.append("无 TestCase 可评审，软维度用中性默认分")
        return result

    # target 解析辅助：display_id/id → id；item id 集合
    case_id_map: dict[str, str] = {}
    for c in test_cases:
        case_id_map[c.id] = c.id
        if c.display_id:
            case_id_map[c.display_id] = c.id
    item_ids = {it.id for it in items}
    resolver = TargetResolver()

    user_prompt = REVIEW_USER_TEMPLATE.format(
        testcases_json=_serialize_cases(test_cases), items_json=_serialize_items(items)
    )
    try:
        raw = client.chat(TEST_CASE_REVIEWER_PROMPT, user_prompt)
        result.calls = 1
    except Exception as e:  # noqa: BLE001 - LLM 调用失败降级
        logger.exception("LLM 软维度评审调用失败")
        result.issues.append(f"LLM 软维度评审调用失败，三维用中性默认分 {DEFAULT_SOFT_SCORE_ON_FAILURE}: {e}")
        result.calls = 1
        return result

    payload = extract_json(raw)
    soft_reviews = payload.get("soft_reviews") if isinstance(payload, dict) else payload
    if not isinstance(soft_reviews, list):
        result.issues.append("LLM 响应缺少 soft_reviews 数组，三维用中性默认分")
        return result

    seen_dims: set[str] = set()
    for entry in soft_reviews:
        if not isinstance(entry, dict):
            continue
        dim_raw = str(entry.get("dimension") or "")
        dim = _SOFT_DIM_TO_REVIEW.get(dim_raw)
        if dim is None:
            result.issues.append(
                f"忽略未知/越界软维度: {dim_raw}（只接受 accuracy/missing_risk/executability_semantic）"
            )
            continue
        seen_dims.add(dim_raw)
        score = _clamp_score(entry.get("score"))
        reason = str(entry.get("reason") or "").strip()
        # 记录分数与 reason
        if dim_raw == "accuracy":
            result.accuracy_score = score
        elif dim_raw == "missing_risk":
            result.missing_risk_score = score
        else:  # executability_semantic
            result.executability_semantic_score = score
        result.dimension_reasons[dim_raw] = reason or "(LLM 未给出理由)"
        if not reason:
            result.issues.append(f"{dim_raw} 缺少 reason（可解释性不足），已标记")
        # findings（证据锚定 + 非法丢弃）
        for raw_finding in entry.get("findings") or []:
            if not isinstance(raw_finding, dict):
                continue
            f = _build_finding(dim, raw_finding, resolver, case_id_map, item_ids, result.issues)
            if f is not None:
                result.findings.append(f)

    # 缺失维度兜底
    for dim_raw in ("accuracy", "missing_risk", "executability_semantic"):
        if dim_raw not in seen_dims:
            result.issues.append(f"LLM 未评软维度 {dim_raw}，用中性默认分 {DEFAULT_SOFT_SCORE_ON_FAILURE}")
            result.dimension_reasons.setdefault(dim_raw, "(LLM 未评该维度)")

    logger.info(
        "软维度评审: accuracy=%.1f missing_risk=%.1f executability_semantic=%.1f findings=%d issues=%d",
        result.accuracy_score,
        result.missing_risk_score,
        result.executability_semantic_score,
        len(result.findings),
        len(result.issues),
    )
    return result
