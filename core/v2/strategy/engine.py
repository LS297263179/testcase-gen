"""V2 Step 4 策略引擎主入口 - 遍历三策略汇总 CoverageObligation。

职责：读 items → 对每个 item 调用三个策略的 derive_*_obligations → 汇总 obligations。
不做 TestPoint 派生（交给 deriver.py）、不做持久化（交给 orchestrator.py）。

首批三策略（用户拍板）：
  - BOUNDARY_VALUE：FieldSpec.min/max_value + min/max_length
  - EQUIVALENCE_CLASS：FieldSpec.enum_values/pattern/required/nullable/unique/data_type_format
  - PERMISSION_MATRIX：PermissionRule.role × resource × action

延后（plan 明确不做）：DECISION_TABLE / STATE_TRANSITION / ERROR_GUESSING / SCENARIO
"""

from __future__ import annotations

import logging

from core.schemas import CoverageObligation, RequirementItem
from core.v2.strategy.boundary import derive_boundary_obligations
from core.v2.strategy.equivalence import derive_equivalence_obligations
from core.v2.strategy.permission import derive_permission_obligations

logger = logging.getLogger("v2.strategy.engine")


def derive_obligations(items: list[RequirementItem], run_id: str) -> list[CoverageObligation]:
    """遍历 items，对每个 item 调用三策略汇总 CoverageObligation。

    obligation 顺序：按 item 顺序，每个 item 内按 boundary → equivalence → permission 顺序。
    该顺序稳定，便于幂等重跑时 fingerprint 一致。
    """
    obligations: list[CoverageObligation] = []
    for item in items:
        obligations.extend(derive_boundary_obligations(item, run_id))
        obligations.extend(derive_equivalence_obligations(item, run_id))
        obligations.extend(derive_permission_obligations(item, run_id))
    logger.info(
        "策略引擎派生 obligation 完成: items=%d obligations=%d (boundary=%d equivalence=%d permission=%d)",
        len(items),
        len(obligations),
        sum(1 for ob in obligations if ob.technique.value == "boundary_value"),
        sum(1 for ob in obligations if ob.technique.value == "equivalence_class"),
        sum(1 for ob in obligations if ob.technique.value == "permission_matrix"),
    )
    return obligations
