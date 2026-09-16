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


class CoverageDetail(BaseModel):
    """coverage 维度双指标明细（Step 7，用户核心设计：不合成模糊的单一 coverage 分）。

    - strategy_obligation_coverage：Step 4 硬指标（结构性覆盖，通常=1.0）
    - requirement_item_coverage：item 覆盖率（反映需求被测试点覆盖的程度）
    - uncovered_item_ids：未被任何 TestPoint 覆盖的 item（Reviewer 据此定位缺口）
    """

    strategy_obligation_coverage: float = Field(ge=0.0, le=1.0)
    requirement_item_coverage: float = Field(ge=0.0, le=1.0)
    uncovered_item_ids: list[Ulid] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ExecutabilityDetail(BaseModel):
    """executability 维度双子分（Step 7，用户核心设计：加权而非简单平均）。

    steps 非空只证明“形式上有步骤”（structural），不证明“真能被测试人员执行”（semantic）。
    最终 executability = structural_score × structural_weight + semantic_score × semantic_weight。
    权重首批 0.4/0.6（经验值），留 Step 12 Benchmark 验证调整。
    """

    structural_score: float = Field(ge=0.0, le=100.0)  # 代码算：steps/action/expected 结构完整性
    semantic_score: float = Field(ge=0.0, le=100.0)  # LLM 算：步骤是否具体可执行
    structural_weight: float = 0.4
    semantic_weight: float = 0.6

    model_config = ConfigDict(extra="forbid")


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
    # Step 7 新增：结构化明细（供 Step 8 消费 + 可解释性）
    #   duplication: {"duplicate_level": "exact"|"semantic", "similarity": float, "counterpart_id": str}
    #   missing_risk: {"rule_name": str, "evidence": str}（接 Step 2 source_ref 证据锤定）
    #   consistency: {"violation": str}
    detail: dict | None = None

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
    # Step 7 新增字段
    review_target_type: TargetType = TargetType.TESTCASE  # 预留：评审对象类型（当前=TESTCASE，未来可 TESTPOINT）
    review_target_ids: list[Ulid] = Field(default_factory=list)  # 预留：被评审对象 id（当前=该 run 的 TestCase[]）
    coverage_detail: CoverageDetail | None = None  # coverage 双指标明细
    executability_detail: ExecutabilityDetail | None = None  # executability 双子分
    dimension_reasons: dict[str, str] = Field(default_factory=dict)  # 各维度评分理由（软维度来自 LLM，硬维度代码填）

    model_config = ConfigDict(extra="forbid")
