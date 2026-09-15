"""V2 评审 Schema - 结构化六维评审 + 多轮版本。

设计见 docs/v2/step1-data-model.md §3.6。
P0-4：评审是多轮过程（initial → after_optimizer → after_human_edit）。
P1-2：ReviewScores 固定六维模型，取代自由 dict。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.common import (
    EntityBase,
    Provenance,
    ReviewDimension,
    ReviewTriggerType,
    Severity,
    TargetType,
    Ulid,
    new_ulid,
)


class ReviewScores(BaseModel):
    """六维打分（P1-2）：固定字段，杜绝 {coverage:90, foobar:100} 这类脏数据"""

    coverage: float = Field(ge=0, le=100)
    accuracy: float = Field(ge=0, le=100)
    executability: float = Field(ge=0, le=100)
    consistency: float = Field(ge=0, le=100)
    missing_risk: float = Field(ge=0, le=100)
    duplication: float = Field(ge=0, le=100)

    model_config = ConfigDict(extra="forbid")

    def overall(self) -> float:
        """总分（六维均值，可按需加权）"""
        values = [
            self.coverage,
            self.accuracy,
            self.executability,
            self.consistency,
            self.missing_risk,
            self.duplication,
        ]
        return round(sum(values) / len(values), 2)


class ReviewFinding(BaseModel):
    """评审发现 - target 为多态引用（P1-5，无 DB 外键，由 Domain Validator 校验）"""

    id: Ulid = Field(default_factory=new_ulid)
    dimension: ReviewDimension
    severity: Severity
    target_type: TargetType
    target_id: Ulid
    issue: str
    suggestion: str | None = None
    provenance: Provenance  # validator(代码硬指标) / llm(软判断) / human
    auto_fixable: bool = False

    model_config = ConfigDict(extra="forbid")


class ReviewReport(EntityBase):
    """一轮评审报告 - (run_id, revision) 唯一"""

    run_id: Ulid
    revision: int = Field(ge=1)  # P0-4：1,2,3…
    trigger_type: ReviewTriggerType
    scores: ReviewScores
    overall_score: float = Field(default=0.0, ge=0.0, le=100.0)  # 总分（可加权，默认取 scores.overall()）
    summary: str = ""
    obligation_coverage: float = Field(default=0.0, ge=0.0, le=1.0)  # 覆盖率硬指标（代码算）
    findings: list[ReviewFinding] = Field(default_factory=list)  # 持久化时拆到 review_findings 表

    model_config = ConfigDict(extra="forbid")
