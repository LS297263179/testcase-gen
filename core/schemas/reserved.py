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
    """用例多版本（Step 9 激活）：1(LLM) → 2(Human) → 3(Human) → …

    ★ revision_no 语义：TestCase 内容版本（区别于 ReviewReport.revision 评审版本）。
    snapshot 保存修改前完整 TestCase，changed_fields 记录哪些字段被修改（Step 10 Preference Learning 数据源）。
    """

    test_case_id: Ulid
    revision_no: int = Field(ge=1)  # 内容版本（test_case_revision_no，非 review_revision）
    snapshot: dict = Field(default_factory=dict)  # 修改前完整 TestCase 快照
    changed_fields: list[str] = Field(default_factory=list)  # 哪些字段被修改
    provenance: Provenance = Provenance.LLM  # 这一版本是谁产生/修改的
    changed_by: str = "system"  # user / system / optimizer
    change_source: str = ""  # human_edit / llm_generation / optimizer_archive

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
