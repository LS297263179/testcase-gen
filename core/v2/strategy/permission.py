"""V2 Step 4 权限矩阵策略（PERMISSION_MATRIX）- PermissionRule → 1:1 派生。

派生规则（用户确认 1:1 合理）：
  - 每个 PermissionRule（role × resource × action → allowed/denied）→ 1 个 CoverageObligation → 1 个 TestPoint
  - condition 非空时（如 "仅本人"）：首批不派生额外 TestPoint（condition 是自然语言，代码难以规则化；
    留给 Step 5 用例合成时由 LLM 处理，见 plan Q3）。仅在 description 里提及 condition。

CoverageObligation.params 结构：
  {"role":"admin","resource":"order","action":"refund","allowed":true,"condition":null}

TestPoint.strategy_params 结构（用于 fingerprint 区分）：
  {"role":"admin","resource":"order","action":"refund","allowed":true}
  （不含 condition，避免自然语言参与 fingerprint 计算导致不稳定）
"""

from __future__ import annotations

import logging

from core.schemas import (
    CoverageObligation,
    GenerationScope,
    Priority,
    Provenance,
    RequirementItem,
    Technique,
    TestDimension,
    TestPoint,
)

logger = logging.getLogger("v2.strategy.permission")


# ============================================================
# Obligation 派生
# ============================================================


def derive_permission_obligations(item: RequirementItem, run_id: str) -> list[CoverageObligation]:
    """遍历 item.permissions，每个 PermissionRule 派生 1 个 PERMISSION_MATRIX obligation。"""
    obligations: list[CoverageObligation] = []
    for rule in item.permissions:
        allowed_label = "允许" if rule.allowed else "拒绝"
        target = f"{rule.role}:{rule.resource}:{rule.action}"
        obligations.append(
            CoverageObligation(
                run_id=run_id,
                item_id=item.id,
                technique=Technique.PERMISSION_MATRIX,
                target=target,
                description=f"权限矩阵覆盖：{rule.role} 对 {rule.resource} 执行 {rule.action} → {allowed_label}",
                params={
                    "role": rule.role,
                    "resource": rule.resource,
                    "action": rule.action,
                    "allowed": rule.allowed,
                    "condition": rule.condition,
                },
            )
        )
    return obligations


# ============================================================
# TestPoint 派生（1:1）
# ============================================================


def derive_permission_testpoints(ob: CoverageObligation, item: RequirementItem) -> list[TestPoint]:
    """从 PERMISSION_MATRIX obligation 派生 1 个 TestPoint（1:1 关系）。

    TestPoint 字段规则（对应 plan D5）：
      - provenance=STRATEGY, generation_scope=STRATEGY, technique=PERMISSION_MATRIX, obligation_id=ob.id
      - module=item.module, subcategory="权限矩阵", dimension=PERMISSION
      - priority=item.priority_hint 或 P1
      - item_ids=[item.id]
      - strategy_params 不含 condition（避免自然语言参与 fingerprint）
    """
    if ob.technique != Technique.PERMISSION_MATRIX:
        return []
    params = ob.params or {}
    role = params.get("role", "")
    resource = params.get("resource", "")
    action = params.get("action", "")
    allowed = params.get("allowed", False)
    condition = params.get("condition")

    allowed_label = "允许" if allowed else "拒绝"
    expectation = "预期：操作成功" if allowed else "预期：操作被拒绝并提示权限不足"

    title = f"{role} {action} {resource} {allowed_label}"
    condition_note = f"附加条件：{condition}。" if condition else ""
    description = (
        f"验证权限矩阵：角色「{role}」对资源「{resource}」执行操作「{action}」时应被{allowed_label}。"
        f"{condition_note}{expectation}。"
        f"权限矩阵是确定性覆盖，每个 role×resource×action 组合必须独立验证。"
    )

    # strategy_params：不含 condition（自然语言不参与 fingerprint，避免不稳定）
    strategy_params = {
        "role": role,
        "resource": resource,
        "action": action,
        "allowed": allowed,
    }

    tp = TestPoint(
        run_id=ob.run_id,
        version_id=item.version_id,
        item_ids=[item.id],
        module=item.module,
        subcategory="权限矩阵",
        title=title,
        description=description,
        dimension=TestDimension.PERMISSION,
        technique=Technique.PERMISSION_MATRIX,
        obligation_id=ob.id,
        priority=item.priority_hint or Priority.P1,
        provenance=Provenance.STRATEGY,
        generation_scope=GenerationScope.STRATEGY,
        strategy_params=strategy_params,
    )
    return [tp]
