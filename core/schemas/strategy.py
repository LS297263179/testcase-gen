"""V2 覆盖义务 Schema - 策略引擎（代码）确定性推导的"必须覆盖项"。

设计见 docs/v2/step1-data-model.md §3.5。
P0-5：本实体**不持久化** satisfied_by_points/cases，避免双重事实源；
覆盖关系统一由 obligation_coverage 关系表承载，经 Repository 查询。
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from core.schemas.common import (
    EntityBase,
    ObligationStatus,
    Technique,
    Ulid,
)


class CoverageObligation(EntityBase):
    """覆盖义务：策略引擎按 RequirementItem（FieldSpec/rules/permissions）算出。

    例：字段 age(min=1,max=100) → 边界义务 {0,1,2,99,100,101}；
        权限 admin×unlock=allow → 权限矩阵义务。
    覆盖率 = 已被 obligation_coverage 关联的义务数 / 总义务数（代码算，硬指标）。
    """

    run_id: Ulid
    item_id: Ulid  # 来源需求项
    technique: Technique
    target: str  # 字段名 / 规则名 / 角色-资源
    description: str
    params: dict = Field(default_factory=dict)  # 结构化参数，如 {"min":1,"max":100}
    status: ObligationStatus = ObligationStatus.PENDING

    model_config = ConfigDict(extra="forbid")
