"""V2 设计预留实体 - 🟡 Step 1 只冻结形状，不建表、不接入管线。

设计见 docs/v2/step1-data-model.md §3.9。
这些实体为后续 Step 预留扩展位，防止架构过早复杂化：
  - TestScenario      → Step 5+（复杂业务流：注册→登录→下单→支付→退款）
  - TestCaseRevision  → Step 9/10（用例多版本，PreferenceLearning 的真实数据源）
  - LLMInvocation     → 全程（LLM 调用审计，保存但不作业务数据源）
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from core.schemas.common import EntityBase, Provenance, Ulid


class TestScenario(EntityBase):
    """🟡 复杂业务流场景（只设计）：RequirementItem → TestScenario → TestPoint → TestCase"""

    version_id: Ulid
    item_ids: list[Ulid] = Field(default_factory=list)
    name: str
    flow: list[str] = Field(default_factory=list)  # 有序步骤概要
    test_point_ids: list[Ulid] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class TestCaseRevision(EntityBase):
    """🟡 用例多版本（只设计）：1(LLM) → 2(Optimizer) → 3(Human) → …"""

    test_case_id: Ulid
    revision: int = Field(ge=1)
    snapshot: dict = Field(default_factory=dict)  # 该版本用例完整快照
    changed_fields: list[str] = Field(default_factory=list)  # 相对上版改了哪些字段
    provenance: Provenance = Provenance.LLM

    model_config = ConfigDict(extra="forbid")


class LLMInvocation(EntityBase):
    """🟡 LLM 调用审计（只设计）：复现"输入→Prompt→模型→Raw→Parser→Validator"全链。

    原则：原始响应保存但**不作为业务数据源**，业务数据一律取校验后的结构化结果。
    """

    run_id: Ulid | None = None
    stage: str  # parse / generate / review / optimize / preference
    provider: str
    model: str
    prompt_version: str
    request_json: dict = Field(default_factory=dict)
    response_json: dict | None = None
    latency_ms: int | None = None
    token_usage: dict | None = None  # {prompt, completion, total}
    status: str = "ok"  # ok / error
    error: str | None = None

    model_config = ConfigDict(extra="forbid")
