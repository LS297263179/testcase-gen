"""V2 Step 4 TestPoint 派生器 - obligation → TestPoint + fingerprint 计算 + 去重。

职责：
  - 按 obligation.technique 分派到对应策略的 derive_*_testpoints
  - 统一计算 fingerprint（compute_strategy_testpoint_fingerprint）
  - 按 fingerprint 严格去重（同一 obligation 下 strategy_params 相同的点只保留首个）
  - 不做持久化、不做 add_coverage 登记（交给 orchestrator.py）

分派表：
  - BOUNDARY_VALUE     → boundary.derive_boundary_testpoints
  - EQUIVALENCE_CLASS  → equivalence.derive_equivalence_testpoints
  - PERMISSION_MATRIX  → permission.derive_permission_testpoints
  - 其他 technique     → 记入 issues，跳过（首批不支持）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import CoverageObligation, RequirementItem, Technique, TestPoint
from core.v2.fingerprint import compute_strategy_testpoint_fingerprint
from core.v2.strategy.boundary import derive_boundary_testpoints
from core.v2.strategy.equivalence import derive_equivalence_testpoints
from core.v2.strategy.permission import derive_permission_testpoints

logger = logging.getLogger("v2.strategy.deriver")

# technique → 派生函数分派表
_DISPATCH = {
    Technique.BOUNDARY_VALUE: derive_boundary_testpoints,
    Technique.EQUIVALENCE_CLASS: derive_equivalence_testpoints,
    Technique.PERMISSION_MATRIX: derive_permission_testpoints,
}


@dataclass
class DeriveResult:
    """派生产物：合法 TestPoint 列表 + 问题记录。"""

    points: list[TestPoint] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def derive_testpoints_from_obligations(
    obligations: list[CoverageObligation],
    items_index: dict[str, RequirementItem],
    version_id: str | None = None,
) -> DeriveResult:
    """从 obligation 列表派生 TestPoint，统一计算 fingerprint 并去重。

    - items_index: dict[item_id, RequirementItem]，用于查找 obligation.item_id 对应的 item
    - version_id: 显式指定 TestPoint.version_id；缺省时从 item.version_id 取
    - 派生流程：
        1. 按 obligation.technique 分派到对应策略函数
        2. 为每个 TestPoint 计算 fingerprint（compute_strategy_testpoint_fingerprint）
        3. 注入 version_id（若显式给定）
        4. 按 fingerprint 严格去重（保留首个，后续记入 issues）
    """
    result = DeriveResult()
    seen_fps: set[str] = set()

    for ob in obligations:
        # 查找来源 item
        item = items_index.get(ob.item_id)
        if item is None:
            result.issues.append(f"obligation {ob.id} 的 item_id={ob.item_id} 不在 items_index，跳过")
            continue

        # 按 technique 分派
        derive_fn = _DISPATCH.get(ob.technique)
        if derive_fn is None:
            result.issues.append(
                f"obligation {ob.id} 的 technique={ob.technique.value} 首批不支持，跳过（DECISION_TABLE/STATE_TRANSITION/ERROR_GUESSING/SCENARIO 延后）"
            )
            continue

        # 派生 TestPoint
        raw_points = derive_fn(ob, item)

        # 统一计算 fingerprint + 注入 version_id + 去重
        for tp in raw_points:
            if version_id is not None:
                tp.version_id = version_id
            tp.fingerprint = compute_strategy_testpoint_fingerprint(
                version_id=tp.version_id,
                obligation_id=ob.id,
                technique=ob.technique.value if hasattr(ob.technique, "value") else str(ob.technique),
                strategy_params=tp.strategy_params,
            )
            if tp.fingerprint in seen_fps:
                result.issues.append(
                    f"重复 fingerprint 丢弃: obligation={ob.id} title={tp.title} params={tp.strategy_params}"
                )
                continue
            seen_fps.add(tp.fingerprint)
            result.points.append(tp)

    logger.info(
        "TestPoint 派生完成: obligations=%d points=%d issues=%d",
        len(obligations),
        len(result.points),
        len(result.issues),
    )
    return result
