"""V2 Step 4 策略引擎编排 - Run 状态机 + 持久化 + 覆盖率双指标 + 一站式入口。

职责：
  - `apply_strategy_engine(run_id, version_id)`：复用已有 Run（Step 3 建的），派生 obligations +
    strategy TestPoints，持久化，add_coverage 登记，计算覆盖率双指标，Run.status STRATEGIZING→DONE。
  - `generate_test_points_full(client, version_id, user_id)`：Step 3 + Step 4 一站式入口，
    走完整 Run 状态机 PARSING → GENERATING → STRATEGIZING → DONE。

覆盖率双指标（用户修订，严格分离）：
  - **Strategy Obligation Coverage**（Step 4 内部硬指标）：
      = 被至少一个 strategy TestPoint 覆盖的 obligation 数 / 总 obligation 数
      = repo.obligation_coverage_ratio(run_id)
      验收门槛：== 1.0（每个 obligation 至少被 1 个 TestPoint 覆盖）
  - **RequirementItem Coverage**（整体软指标）：
      = 被至少一个 TestPoint（LLM 或 strategy）引用的 item 数 / 总 item 数
      复用 Step 3 tp_validator.build_coverage_report，传入合流后的 all_points
      软指标：不阻塞入库；纯功能类 item（无 fields/permissions）可能只被 Step 3 LLM 覆盖
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import (
    CoverageObligation,
    RequirementItem,
    RunCounts,
    RunStatus,
    TargetType,
    TestPoint,
)
from core.v2 import repository as repo
from core.v2.strategy.deriver import DeriveResult, derive_testpoints_from_obligations
from core.v2.strategy.engine import derive_obligations
from core.v2.tp_validator import CoverageReport, build_coverage_report

logger = logging.getLogger("v2.strategy.orchestrator")


@dataclass
class StrategyResult:
    """Step 4 策略引擎产物。"""

    run_id: str
    version_id: str
    obligations: list[CoverageObligation] = field(default_factory=list)
    points: list[TestPoint] = field(default_factory=list)
    strategy_obligation_coverage: float = 0.0  # 硬指标，应=1.0
    requirement_item_coverage: CoverageReport | None = None  # 软指标（仅 strategy 覆盖部分）
    issues: list[str] = field(default_factory=list)


@dataclass
class FullGenerationResult:
    """Step 3 + Step 4 一站式产物。"""

    run_id: str
    version_id: str
    llm_points: list[TestPoint] = field(default_factory=list)  # Step 3 产物
    strategy_points: list[TestPoint] = field(default_factory=list)  # Step 4 产物
    all_points: list[TestPoint] = field(default_factory=list)  # 合流
    obligations: list[CoverageObligation] = field(default_factory=list)
    strategy_obligation_coverage: float = 0.0  # Step 4 硬指标
    requirement_item_coverage: CoverageReport | None = None  # 整体软指标（基于 all_points）
    # Step 3 透传
    phase_a_calls: int = 0
    phase_b_calls: int = 0
    warnings: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


# ============================================================
# Step 4 独立入口
# ============================================================


def apply_strategy_engine(run_id: str, version_id: str, skip_run_status_update: bool = False) -> StrategyResult:
    """对已有 Run 应用策略引擎：派生 obligations + strategy TestPoints + add_coverage 登记。

    流程：
      1. 拉 Run，改 status=STRATEGIZING
      2. 拉 items（version_id 下全部 RequirementItem）
      3. engine.derive_obligations(items, run_id) → obligations
      4. 持久化 obligations（repo.save_obligation）
      5. deriver.derive_testpoints_from_obligations → strategy TestPoints（含 fingerprint）
      6. 注入 run_id → 持久化 TestPoints（repo.save_test_point，按 fingerprint upsert 幂等）
      7. add_coverage 登记：每个 obligation 被其派生的 TestPoint 覆盖
      8. 计算 Strategy Obligation Coverage（硬指标，repo.obligation_coverage_ratio）
      9. 计算 RequirementItem Coverage（软指标，仅 strategy 覆盖部分）
      10. 更新 Run.status=DONE + counts 累加（obligations / points）
      11. 返回 StrategyResult
    """
    result_issues: list[str] = []

    # 1. 拉 Run
    run = repo.get_run(run_id)
    if run is None:
        return StrategyResult(run_id=run_id, version_id=version_id, issues=[f"Run 不存在: {run_id}"])
    if run.requirement_version_id != version_id:
        result_issues.append(
            f"Run.requirement_version_id={run.requirement_version_id} 与输入 version_id={version_id} 不一致，以 Run 为准"
        )
        version_id = run.requirement_version_id

    # 改 status=STRATEGIZING（skip 时由 Runtime 独家控制 —— P0-2）
    if not skip_run_status_update:
        run.status = RunStatus.STRATEGIZING
        repo.save_run(run)

    # 2. 拉 items
    items: list[RequirementItem] = repo.list_items(version_id)
    if not items:
        if not skip_run_status_update:
            run.status = RunStatus.DONE
            repo.save_run(run)
        return StrategyResult(
            run_id=run_id,
            version_id=version_id,
            issues=result_issues + [f"version {version_id} 无 items，无法派生 obligation"],
        )
    items_index = {it.id: it for it in items}

    # 3. 派生 obligations
    obligations = derive_obligations(items, run_id)

    # 4. 持久化 obligations（幂等：按 natural key 复用旧 id，保证下游 TestPoint fingerprint 稳定）
    for ob in obligations:
        technique_val = ob.technique.value if hasattr(ob.technique, "value") else str(ob.technique)
        existing = repo.get_obligation_by_natural_key(run_id, ob.item_id, technique_val, ob.target)
        if existing is not None:
            ob.id = existing.id  # 复用旧 ULID
        repo.save_obligation(ob)

    # 5. 派生 TestPoints
    derive_result: DeriveResult = derive_testpoints_from_obligations(obligations, items_index, version_id=version_id)
    result_issues.extend(derive_result.issues)

    # 6. 注入 run_id + 持久化 TestPoints
    for tp in derive_result.points:
        tp.run_id = run_id
        repo.save_test_point(tp)

    # 7. add_coverage 登记：每个 obligation 被其派生的 TestPoint 覆盖
    # 按 obligation_id 分组 TestPoint
    ob_to_tps: dict[str, list[TestPoint]] = {}
    for tp in derive_result.points:
        if tp.obligation_id:
            ob_to_tps.setdefault(tp.obligation_id, []).append(tp)
    for ob in obligations:
        for tp in ob_to_tps.get(ob.id, []):
            repo.add_coverage(ob.id, TargetType.TESTPOINT, tp.id)

    # 8. Strategy Obligation Coverage（硬指标）
    strategy_coverage = repo.obligation_coverage_ratio(run_id)

    # 9. RequirementItem Coverage（软指标，仅 strategy 覆盖部分）
    item_coverage = build_coverage_report(derive_result.points, items)

    # 10. 更新 Run.status=DONE + counts 累加（skip 时由 Runtime 独家写 —— P0-2）
    if not skip_run_status_update:
        run.status = RunStatus.DONE
        # counts 累加：原有 points 数 + 新增 strategy points 数；obligations 数
        existing_points = len(repo.list_test_points_by_run(run_id))
        run.counts = RunCounts(
            items=len(items),
            points=existing_points,  # 已含 Step 3 LLM + Step 4 strategy（因为 list_by_run 查的是 DB 现状）
            obligations=len(obligations),
        )
        repo.save_run(run)

    logger.info(
        "Step 4 策略引擎完成: run=%s version=%s items=%d obligations=%d strategy_points=%d "
        "obligation_coverage=%.2f item_coverage=%.2f",
        run_id,
        version_id,
        len(items),
        len(obligations),
        len(derive_result.points),
        strategy_coverage,
        item_coverage.coverage_ratio,
    )

    return StrategyResult(
        run_id=run_id,
        version_id=version_id,
        obligations=obligations,
        points=derive_result.points,
        strategy_obligation_coverage=strategy_coverage,
        requirement_item_coverage=item_coverage,
        issues=result_issues,
    )


# ============================================================
# Step 3 + Step 4 一站式入口
# ============================================================


def generate_test_points_full(
    client,
    *,
    version_id: str,
    user_id: str | None = None,
    generation_config=None,
    phase_b_batch_threshold: int | None = None,
    run_id: str | None = None,
    skip_run_status_update: bool = False,
) -> FullGenerationResult:
    """Step 3（LLM 派生）+ Step 4（策略引擎）一站式入口。

    走完整 Run 状态机：PARSING → GENERATING → STRATEGIZING → DONE。
    合流后的 all_points 同时含 LLM 与 STRATEGY 两种 provenance 的 TestPoint。

    覆盖率双指标：
      - strategy_obligation_coverage：Step 4 硬指标（应=1.0）
      - requirement_item_coverage：整体软指标（基于 all_points 计算）
    """
    from core.v2.tp_orchestrator import GenerationResult, generate_test_points

    # Step 3：LLM 派生
    step3_result: GenerationResult = generate_test_points(
        client,
        version_id=version_id,
        user_id=user_id,
        generation_config=generation_config,
        phase_b_batch_threshold=phase_b_batch_threshold,
        run_id=run_id,
        skip_run_status_update=skip_run_status_update,
    )
    if not step3_result.run_id:
        # Step 3 失败（version 不存在 / 无 items），直接返回
        return FullGenerationResult(
            run_id="",
            version_id=version_id,
            issues=step3_result.issues,
        )

    # Step 4：策略引擎（复用 Step 3 的 Run）
    step4_result = apply_strategy_engine(step3_result.run_id, version_id, skip_run_status_update=skip_run_status_update)

    # 合流 all_points（从 DB 查，保证含 Step 3 + Step 4 全部）
    all_points = repo.list_test_points_by_run(step3_result.run_id)

    # 整体 RequirementItem Coverage（基于 all_points）
    items = repo.list_items(version_id)
    overall_item_coverage = build_coverage_report(all_points, items)

    logger.info(
        "Step 3+4 一站式完成: run=%s llm_points=%d strategy_points=%d all_points=%d "
        "obligation_coverage=%.2f item_coverage=%.2f",
        step3_result.run_id,
        len(step3_result.points),
        len(step4_result.points),
        len(all_points),
        step4_result.strategy_obligation_coverage,
        overall_item_coverage.coverage_ratio,
    )

    return FullGenerationResult(
        run_id=step3_result.run_id,
        version_id=version_id,
        llm_points=step3_result.points,
        strategy_points=step4_result.points,
        all_points=all_points,
        obligations=step4_result.obligations,
        strategy_obligation_coverage=step4_result.strategy_obligation_coverage,
        requirement_item_coverage=overall_item_coverage,
        phase_a_calls=step3_result.phase_a.calls,
        phase_b_calls=step3_result.phase_b.calls,
        warnings=step3_result.warnings,
        issues=step3_result.issues + step4_result.issues,
    )
