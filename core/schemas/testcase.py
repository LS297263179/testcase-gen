"""V2 测试用例 Schema - 结构化步骤 + 生命周期状态机。

设计见 docs/v2/step1-data-model.md §3.4。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.common import (
    ALLOWED_TRANSITIONS,
    ConfidenceLevel,
    EntityBase,
    Priority,
    Provenance,
    TestCaseStatus,
    TestCaseType,
    Ulid,
    can_transition,
)


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
    fingerprint: str | None = None  # 去重指纹（代码算，Step 8）
    provenance: Provenance = Provenance.LLM
    status: TestCaseStatus = TestCaseStatus.GENERATED  # P0-6：状态机
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM

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
