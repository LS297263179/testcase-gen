"""V2 Step 4 测试策略引擎 - 纯代码确定性派生 CoverageObligation + TestPoint(provenance=STRATEGY)。

首批三类技术（用户拍板）：
  - BOUNDARY_VALUE：FieldSpec.min/max_value + min/max_length → 六点边界
  - EQUIVALENCE_CLASS：FieldSpec.enum_values/pattern/required/nullable/unique/data_type_format → 抽象类标识
  - PERMISSION_MATRIX：PermissionRule.role × resource × action → 1:1 派生

派生关系：1:N（一个 obligation 派生多个 TestPoint；权限矩阵为 1:1）。
与 Step 3 LLM 派生合流写入同一张 test_points 表，用 provenance + generation_scope 区分。

★ Step 4 修订（用户补充）：
  - Strategy TestPoint 必须：provenance=STRATEGY / generation_scope=STRATEGY / technique!=None / obligation_id!=None
  - Strategy Engine 不生成真实测试数据，只产出抽象类标识（具体数据由 Step 5 TestDataGenerator 生成）
  - fingerprint 公式：sha256(version_id | "strategy" | obligation_id | technique | canonical(strategy_params))
  - 覆盖率双指标分离：
      Strategy Obligation Coverage（硬指标，应=1.0）≠ RequirementItem Coverage（整体软指标）

主入口：
  - `engine.derive_obligations(items, run_id)` → 遍历三策略汇总 obligations
  - `deriver.derive_testpoints_from_obligations(...)` → obligation → TestPoint + fingerprint + 去重
  - `orchestrator.apply_strategy_engine(run_id, version_id)` → 顶层编排（Run 状态机 + 持久化 + add_coverage）
  - `orchestrator.generate_test_points_full(client, version_id, user_id)` → Step 3 + Step 4 一站式
"""

from core.v2.strategy.engine import derive_obligations
from core.v2.strategy.orchestrator import (
    FullGenerationResult,
    StrategyResult,
    apply_strategy_engine,
    generate_test_points_full,
)

__all__ = [
    "derive_obligations",
    "apply_strategy_engine",
    "generate_test_points_full",
    "StrategyResult",
    "FullGenerationResult",
]
