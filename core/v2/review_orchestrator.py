"""V2 Step 7 评审编排 - Hard + Soft Review Engine → ReviewReport → TestCase REVIEWED。

对应 PROGRESS.md §9 蓝图 `AI Reviewer(6维)`。职责：
  - `review_test_cases(client, run_id)`：评审某 run 的 VALIDATED 用例，产出结构化多轮 ReviewReport，
    TestCase 状态 VALIDATED→REVIEWED，Run 状态机 →REVIEWING→DONE。

★ Step 7 铁律（用户冻结）：
  - 只发现问题、只标记 auto_fixable，绝不修改/删除/重生成用例（Optimizer=Dedup 是 Step 8）。
  - REVIEWED ≠ 质量合格：仅代表"评审已完成"，低分/多 findings 的用例同样转 REVIEWED。
  - 硬指标（coverage/duplication/consistency/executability 结构层）代码算，确定性可复现；
    软维度（accuracy/missing_risk/executability 语义层）LLM 算，必带 reason + 证据锚定 findings。
  - executability = structural×0.4 + semantic×0.6（加权，非简单平均）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import (
    ExecutabilityDetail,
    ReviewReport,
    ReviewScores,
    ReviewTriggerType,
    RunStatus,
    TargetType,
    TestCaseStatus,
)
from core.v2 import repository as repo
from core.v2.review_hard import run_hard_review
from core.v2.review_prompts import REVIEWER_PROMPT_VERSION
from core.v2.review_soft import run_soft_review

logger = logging.getLogger("v2.review_orchestrator")

# executability 加权（用户冻结：结构 40% + 语义 60%，留 Step 12 Benchmark 验证调整）
EXECUTABILITY_STRUCTURAL_WEIGHT = 0.4
EXECUTABILITY_SEMANTIC_WEIGHT = 0.6


@dataclass
class ReviewResult:
    """Step 7 顶层产物（对齐 Step 3/4/5 的 *Result 约定，携带 report + 诊断）。"""

    run_id: str
    report: ReviewReport | None = None
    reviewed_count: int = 0
    issues: list[str] = field(default_factory=list)


def _augment_reviewer_version(run) -> None:
    """把 Step 7 reviewer prompt 版本写入 GenerationConfig.reviewer_version（审计诚实，幂等）。"""
    cfg = repo.get_generation_config(run.generation_config_id)
    if cfg is None:
        return
    if not cfg.reviewer_version:
        cfg.reviewer_version = REVIEWER_PROMPT_VERSION
        repo.save_generation_config(cfg)
    elif REVIEWER_PROMPT_VERSION not in cfg.reviewer_version:
        cfg.reviewer_version = f"{cfg.reviewer_version},{REVIEWER_PROMPT_VERSION}"
        repo.save_generation_config(cfg)


def review_test_cases(
    client,
    *,
    run_id: str,
    revision: int | None = None,
    trigger_type: ReviewTriggerType = ReviewTriggerType.INITIAL,
    skip_run_status_update: bool = False,
) -> ReviewResult:
    """评审某 run 的 VALIDATED 用例 → 结构化 ReviewReport（多轮 revision）+ TestCase 转 REVIEWED。

    流程：
      1. 拉 Run，status→REVIEWING，追加 reviewer_version 审计
      2. 拉该 run 的 TestCase[]，仅纳入 status=VALIDATED 的（空则返回带 issue 的结果）
      3. 拉 items + test_points + strategy_obligation_coverage
      4. Hard Review Engine（代码）：coverage 双指标 / duplication 两级 / consistency / executability 结构层
      5. Soft Review Engine（LLM）：accuracy / missing_risk / executability 语义层（score+reason+findings）
      6. 合成 ReviewScores（executability=结构×0.4+语义×0.6）+ coverage_detail + executability_detail
         + dimension_reasons + findings（validator+llm）
      7. revision 递增 → 建 ReviewReport(review_target=TestCase[]) → save_review_report（含 ReferentialValidator）
      8. TestCase VALIDATED→REVIEWED（全部转，含低分；REVIEWED≠合格）
      9. Run.status→DONE；返回 ReviewResult
    """
    # 1. 拉 Run
    run = repo.get_run(run_id)
    if run is None:
        return ReviewResult(run_id=run_id, issues=[f"Run 不存在: {run_id}"])
    if not skip_run_status_update:
        run.status = RunStatus.REVIEWING
        repo.save_run(run)
    _augment_reviewer_version(run)

    # 2. 拉 VALIDATED / RE_REVIEW_REQUIRED 用例（Step 9 人工编辑后重评审）
    all_cases = repo.list_test_cases(run_id)
    validated = [c for c in all_cases if c.status in (TestCaseStatus.VALIDATED, TestCaseStatus.RE_REVIEW_REQUIRED)]
    if not validated:
        if not skip_run_status_update:
            run.status = RunStatus.DONE
            repo.save_run(run)
        return ReviewResult(
            run_id=run_id,
            reviewed_count=0,
            issues=[f"Run {run_id} 无 status=VALIDATED/RE_REVIEW_REQUIRED 的用例可评审（共 {len(all_cases)} 条用例）"],
        )

    # 3. 拉上下文
    items = repo.list_items(run.requirement_version_id)
    test_points = repo.list_test_points_by_run(run_id)
    strategy_coverage = repo.obligation_coverage_ratio(run_id)

    # 4. Hard Review Engine（代码，确定性）
    hard = run_hard_review(validated, test_points, items, strategy_coverage)

    # 5. Soft Review Engine（LLM，带 reason + 证据）
    soft = run_soft_review(client, validated, items)

    # 6. 合成 ReviewScores（executability 加权）
    executability_final = round(
        hard.executability_structural_score * EXECUTABILITY_STRUCTURAL_WEIGHT
        + soft.executability_semantic_score * EXECUTABILITY_SEMANTIC_WEIGHT,
        2,
    )
    scores = ReviewScores(
        coverage=hard.coverage_score,
        accuracy=soft.accuracy_score,
        executability=executability_final,
        consistency=hard.consistency_score,
        missing_risk=soft.missing_risk_score,
        duplication=hard.duplication_score,
    )
    executability_detail = ExecutabilityDetail(
        structural_score=hard.executability_structural_score,
        semantic_score=soft.executability_semantic_score,
        structural_weight=EXECUTABILITY_STRUCTURAL_WEIGHT,
        semantic_weight=EXECUTABILITY_SEMANTIC_WEIGHT,
    )
    dimension_reasons = {**hard.dimension_reasons, **soft.dimension_reasons}
    findings = hard.findings + soft.findings
    n_validator = sum(1 for f in findings if f.provenance.value == "validator")
    n_llm = sum(1 for f in findings if f.provenance.value == "llm")

    # 7. revision 递增 + 组装 ReviewReport
    if revision is None:
        latest = repo.get_latest_review_report(run_id)
        revision = (latest.revision + 1) if latest else 1
    summary = (
        f"6 维评审(rev{revision}, {trigger_type.value}): "
        f"coverage={scores.coverage} accuracy={scores.accuracy} executability={scores.executability}"
        f"(结构{executability_detail.structural_score}/语义{executability_detail.semantic_score}) "
        f"consistency={scores.consistency} missing_risk={scores.missing_risk} duplication={scores.duplication}; "
        f"overall={scores.overall()}; findings={len(findings)}(validator={n_validator}, llm={n_llm}); "
        f"评审 {len(validated)} 条用例转 REVIEWED（REVIEWED 仅代表评审完成，非质量合格）"
    )
    report = ReviewReport(
        run_id=run_id,
        revision=revision,
        trigger_type=trigger_type,
        scores=scores,
        overall_score=scores.overall(),
        summary=summary,
        obligation_coverage=strategy_coverage,
        findings=findings,
        review_target_type=TargetType.TESTCASE,
        review_target_ids=[c.id for c in validated],
        coverage_detail=hard.coverage_detail,
        executability_detail=executability_detail,
        dimension_reasons=dimension_reasons,
    )
    repo.save_review_report(report)

    # 8. TestCase VALIDATED→REVIEWED（全部转；transition_to 保证状态机合法；REVIEWED≠合格）
    for c in validated:
        c.transition_to(TestCaseStatus.REVIEWED)
        repo.update_test_case_status(c.id, c.status.value)

    # 9. Run 终态（skip 时由 Runtime 独家控制 —— P0-2）
    if not skip_run_status_update:
        run.status = RunStatus.DONE
        repo.save_run(run)

    logger.info(
        "Step 7 评审完成: run=%s rev=%d cases=%d overall=%.1f findings=%d(validator=%d llm=%d)",
        run_id,
        revision,
        len(validated),
        report.overall_score,
        len(findings),
        n_validator,
        n_llm,
    )
    return ReviewResult(run_id=run_id, report=report, reviewed_count=len(validated), issues=list(soft.issues))
