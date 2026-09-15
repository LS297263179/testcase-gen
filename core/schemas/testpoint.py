"""V2 测试点 Schema。设计见 docs/v2/step1-data-model.md §3.3。"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from core.schemas.common import (
    EntityBase,
    EntityStatus,
    Priority,
    Provenance,
    Technique,
    TestDimension,
    Ulid,
)


class TestPoint(EntityBase):
    """测试点 - 关联原子需求项（M:N），可由 LLM 或策略引擎派生"""

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

    model_config = ConfigDict(extra="forbid")
