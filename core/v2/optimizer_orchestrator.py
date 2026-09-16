"""V2 Step 8 Optimizer Orchestrator - 编排去重归档 + 有条件触发 after_optimizer 重评审。

对应 PROGRESS.md §9 蓝图 `Optimizer(自动优化/去重)`。职责：
  - 拉 latest ReviewReport（Step 7 产物）
  - 执行去重（optimizer.deduplicate_by_findings）
  - 有实际归档（archived_cases > 0）才触发重评审（避免无意义重评审）
  - Run 状态机 → DONE

★ 职责分离（用户冻结）：
  - Optimizer（optimizer.py）= 执行动作（去重归档）
  - Orchestrator（本文件）= 负责流程编排（拉 report → 执行 → 触发重评审）
  - 不由 Optimizer 自己"触发"完整 Review Engine

★ Step 8 完成后的系统变化：
  生成(Step5) → 验证(Step5) → 评审(Step7) → 发现重复 → 自动优化(Step8) → 再次评审(Step7 AFTER_OPTIMIZER)
  这是第一个真正的 AI 测试质量闭环。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import ReviewTriggerType, RunStatus
from core.v2 import repository as repo
from core.v2.optimizer import OptimizerResult, deduplicate_by_findings
from core.v2.review_orchestrator import ReviewResult, review_test_cases

logger = logging.getLogger("v2.optimizer_orchestrator")


@dataclass
class OptimizeResult:
    """Step 8 顶层产物（对齐 Step 3/4/5/6/7 的 *Result 约定）。

    包含：
      - optimizer_result: 去重动作明细（OptimizerResult）
      - review_result: 重评审结果（若有归档则触发，否则 None）
      - issues: 诊断信息（如 no_review_report）
    """

    run_id: str
    optimizer_result: OptimizerResult | None = None
    review_result: ReviewResult | None = None
    issues: list[str] = field(default_factory=list)


def optimize_duplicates(client, *, run_id: str) -> OptimizeResult:
    """Step 8 顶层入口：去重归档 + 有条件触发 after_optimizer 重评审。

    流程（用户冻结版）：
      1. 拉 latest ReviewReport（get_latest_review_report）
      2. Filter: auto_fixable=True + dimension=DUPLICATION（optimizer 内部处理）
      3. Canonicalize duplicate pairs（optimizer 内部处理）
      4. 双边状态检查 + Determine survivor + Archive loser（optimizer 内部处理）
      5. OptimizerResult（actions, archived_cases, skipped_findings）
      6. 有实际归档（archived_cases > 0）？
         - 否 → Run.status=DONE，返回 OptimizeResult（不触发重评审）
         - 是 → review_test_cases(trigger_type=AFTER_OPTIMIZER) → revision+1 → Run.status=DONE

    ★ 幂等：多次运行结果一致（已归档的不再动）。
    ★ 不自动无限循环：有归档才重评审，重评审后不再 optimize。
    """
    # 1. 拉 latest ReviewReport
    report = repo.get_latest_review_report(run_id)
    if report is None:
        logger.warning("Step 8 优化: run=%s 无 ReviewReport，skip", run_id)
        return OptimizeResult(
            run_id=run_id,
            issues=[f"Run {run_id} 无 ReviewReport（请先执行 Step 7 评审）"],
        )

    # 2. 执行去重（optimizer.py）
    optimizer_result = deduplicate_by_findings(run_id, report.findings)

    # 3. 有归档才触发重评审（避免无意义重评审）
    review_result: ReviewResult | None = None
    if optimizer_result.archived_cases > 0:
        logger.info(
            "Step 8 优化: run=%s 归档 %d 条用例，触发 AFTER_OPTIMIZER 重评审",
            run_id,
            optimizer_result.archived_cases,
        )
        review_result = review_test_cases(
            client,
            run_id=run_id,
            trigger_type=ReviewTriggerType.AFTER_OPTIMIZER,
        )
        # review_orchestrator 内部自动 revision+1（get_latest_review_report+1）
    else:
        logger.info(
            "Step 8 优化: run=%s 无实际归档（processed=%d skipped=%d），不触发重评审",
            run_id,
            optimizer_result.processed_findings,
            optimizer_result.skipped_findings,
        )

    # 4. Run 终态
    run = repo.get_run(run_id)
    if run is not None:
        run.status = RunStatus.DONE
        repo.save_run(run)

    logger.info(
        "Step 8 优化完成: run=%s archived=%d skipped=%d review_triggered=%s",
        run_id,
        optimizer_result.archived_cases,
        optimizer_result.skipped_findings,
        review_result is not None,
    )
    return OptimizeResult(
        run_id=run_id,
        optimizer_result=optimizer_result,
        review_result=review_result,
    )
