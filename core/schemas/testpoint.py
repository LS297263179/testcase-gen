"""V2 测试点 Schema。设计见 docs/v2/step1-data-model.md §3.3；Step 3 补充 generation_scope + fingerprint。"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from core.schemas.common import (
    EntityBase,
    EntityStatus,
    GenerationScope,
    Priority,
    Provenance,
    Technique,
    TestDimension,
    Ulid,
)


class TestPoint(EntityBase):
    """测试点 - 关联原子需求项（M:N），可由 LLM 或策略引擎派生。

    Step 3 新增字段：
      - generation_scope：标记本测试点是 Phase A（逐 item）还是 Phase B（跨 item）产物；
        Validator 据此分别校验 item_ids 长度（ITEM=1 / CROSS_ITEM≥2）。
      - fingerprint：业务确定性指纹，用于 DB 层唯一约束与 upsert 幂等身份；
        由代码计算（core/v2/fingerprint.py），Repository.save_test_point 入库前若为空会自动兜底计算。
        Step 3 公式：sha256(version_id | generation_scope | sorted(item_ids) | module | subcategory | normalize(title))

    Step 4 新增字段：
      - strategy_params：策略引擎派生的结构化参数（仅 provenance=STRATEGY 时非空）。
        用于 fingerprint 区分同一 obligation 下的多个 TestPoint（1:N 派生），
        避免仅靠 title 区分不够可靠的问题。
        示例：
          边界值：{"boundary_type": "min_minus_1", "value": 0}
          等价类：{"class": "valid_enum_value", "value": "active"}
          权限矩阵：{"role": "admin", "resource": "order", "action": "refund", "allowed": true}
        Step 4 fingerprint 公式：sha256(version_id | "strategy" | obligation_id | technique | canonical(strategy_params))

    Step 3 硬性约束（Validator 强制，Step 4 策略引擎才会打破）：
      - provenance = LLM
      - technique = None
      - obligation_id = None
    Step 4 硬性约束（与 Step 3 相反）：
      - provenance = STRATEGY
      - generation_scope = STRATEGY
      - technique != None
      - obligation_id != None
    """

    __test__ = False  # 非 pytest 测试类，禁止被收集

    run_id: Ulid | None = None
    version_id: Ulid | None = None  # 冗余：便于按需求版本查询测试点
    item_ids: list[Ulid] = Field(default_factory=list)  # 追溯：→ test_point_items
    module: str
    subcategory: str
    title: str
    description: str
    dimension: TestDimension
    technique: Technique | None = None
    obligation_id: Ulid | None = None  # 若为覆盖某义务而生，回链
    priority: Priority = Priority.P1
    provenance: Provenance = Provenance.LLM
    status: EntityStatus = EntityStatus.DRAFT
    generation_scope: GenerationScope = GenerationScope.ITEM
    fingerprint: str | None = None  # 代码算的业务指纹；Repository 入库前会兜底计算
    strategy_params: dict | None = None  # Step 4 策略引擎结构化参数；LLM 派生时为 None

    model_config = ConfigDict(extra="forbid")
