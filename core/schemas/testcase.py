"""V2 测试用例 Schema - 结构化步骤 + 生命周期状态机。

设计见 docs/v2/step1-data-model.md §3.4。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.common import (
    ALLOWED_TRANSITIONS,
    ConfidenceLevel,
    EntityBase,
    GenerationMode,
    Priority,
    Provenance,
    TestCaseStatus,
    TestCaseType,
    Ulid,
    can_transition,
)


class DataPlanItem(BaseModel):
    """测试数据计划项 - TestDataPlanner 产物（Step 5）。

    职责拆分：TestPoint → DataPlan → TestCase，而非 TestDataGenerator 直接改 TestCase。
    持久化到 TestCase.data_plan，便于：
      1) 审计数据来源（source/generator）；
      2) 未来 Playwright/API/参数化执行复用测试数据；
      3) Step 9 人工编辑时知道哪些数据是代码生成、哪些是 LLM 生成。
    """

    __test__ = False  # 非 pytest 测试类，禁止被收集

    field: str  # 字段名
    strategy: str  # boundary / equivalence / permission / pattern / ...
    value: str | int | float | bool | None = None  # 具体测试数据
    source: str  # example / default / strategy_params / enum / builtin / llm
    generator: str  # code / llm
    expected_valid: bool = True  # 该数据预期合法还是非法（用于 expected 文案）

    model_config = ConfigDict(extra="forbid")


class TestStep(BaseModel):
    """结构化测试步骤 - 为未来 Playwright/API 自动执行预留 action/data/expected"""

    __test__ = False  # 非 pytest 测试类，禁止被收集

    seq: int = Field(ge=1)
    action: str
    data: str | None = None
    expected: str | None = None

    model_config = ConfigDict(extra="forbid")


class TestCase(EntityBase):
    """测试用例 - 关联测试点（M:N），带状态机与去重指纹"""

    __test__ = False  # 非 pytest 测试类，禁止被收集

    run_id: Ulid
    display_id: str  # TC_001，run 内唯一，仅展示/导出，不作关联键
    test_point_ids: list[Ulid] = Field(default_factory=list)  # 追溯：→ test_case_points
    module: str
    title: str
    precondition: str = ""
    steps: list[TestStep] = Field(default_factory=list)
    expected: str
    priority: Priority = Priority.P1
    type: TestCaseType  # P1-1：枚举，非自由字符串
    remark: str = ""
    fingerprint: str | None = None  # 身份指纹（代码算，基于 TestPoint 来源身份，不依赖 title/steps）
    provenance: Provenance = Provenance.LLM
    status: TestCaseStatus = TestCaseStatus.GENERATED  # P0-6：状态机
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM
    # Step 5 新增字段
    generation_mode: GenerationMode = GenerationMode.LLM  # 生成方式，参与 fingerprint 身份
    content_hash: str | None = None  # 内容哈希（与 fingerprint 分离，判断内容是否变化）
    validation_errors: list[str] = Field(default_factory=list)  # VALIDATION_FAILED 时保存不可修复原因
    data_plan: list[DataPlanItem] = Field(default_factory=list)  # 结构化测试数据计划

    model_config = ConfigDict(extra="forbid")

    def transition_to(self, target: TestCaseStatus) -> None:
        """状态机：执行合法转移，非法转移抛 ValueError（代码强制生命周期）。

        例：用户编辑已确认用例 → EDITED → RE_REVIEW_REQUIRED，
        系统据此知道"这条已不是原 AI Review 结果，必须重审"。
        """
        if not can_transition(self.status, target):
            allowed = sorted(s.value for s in ALLOWED_TRANSITIONS.get(self.status, set()))
            raise ValueError(f"非法状态转移 {self.status.value} → {target.value}（允许：{allowed or '无'}）")
        self.status = target

    def render_steps_text(self) -> str:
        """渲染为 V1 风格换行文本，供 Excel/MD 导出与人工阅读"""
        lines = []
        for step in sorted(self.steps, key=lambda s: s.seq):
            text = f"{step.seq}. {step.action}"
            if step.data:
                text += f"（输入：{step.data}）"
            if step.expected:
                text += f" → {step.expected}"
            lines.append(text)
        return "\n".join(lines)
