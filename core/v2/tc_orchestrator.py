"""V2 Step 5 测试用例合成编排 - 复用 Run + TestDataPlanner + 合成 + Validator + 持久化。

对应 PROGRESS.md §9 蓝图 `Test Case Generator`。职责：
  - `synthesize_test_cases(client, run_id, version_id)`：复用 Step 3/4 建立的 Run，
    消费合流后的 TestPoint[]（LLM + STRATEGY），1:1 合成 TestCase，持久化。
    Run 状态机：（Step 4 后 DONE）→ GENERATING → DONE。
  - `generate_test_cases_full(client, version_id, user_id)`：Step 3+4+5 一站式入口。

严格边界：
  - 不动 V1 运行时（core/db.py、web/、data.db）。
  - obligation coverage 不双写：TestCase 通过 TestPoint 间接继承（见 plan D2）。
  - 6 维评审是 Step 7；精确/语义去重是 Step 8（此处只做 fingerprint 严格去重）。

generation_mode 判定（plan D6）：
  - STRATEGY TestPoint + 纯代码数据 + 模板 steps → CODE
  - STRATEGY TestPoint + LLM 兜底数据（复杂正则）→ HYBRID
  - LLM TestPoint + LLM steps → LLM
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import (
    CoverageObligation,
    GenerationConfig,
    Provenance,
    RequirementItem,
    RunCounts,
    RunStatus,
    TestCase,
    TestCaseStatus,
    TestPoint,
)
from core.v2 import repository as repo
from core.v2.tc_generator import TestCaseCandidate, synthesize_strategy_template, synthesize_with_llm
from core.v2.tc_prompts import PROMPT_VERSIONS_COMBINED
from core.v2.tc_validator import BuildResult, dedupe_cases, validate_and_build
from core.v2.test_data_planner import DataPlanResult, plan_data_for_testpoint
from core.v2.tp_validator import CoverageReport

logger = logging.getLogger("v2.tc_orchestrator")

# Step 5 生成器版本（用于 GenerationConfig.generator_version 审计）
GENERATOR_VERSION = "tc-synth-v1"


@dataclass
class SynthesisResult:
    """Step 5 顶层产物。"""

    run_id: str
    version_id: str
    cases: list[TestCase] = field(default_factory=list)
    validated_count: int = 0
    failed_count: int = 0
    code_count: int = 0  # generation_mode = CODE
    llm_count: int = 0  # generation_mode = LLM
    hybrid_count: int = 0  # generation_mode = HYBRID
    llm_calls: int = 0  # 合成 + 复杂正则兜底的 LLM 调用总数
    warnings: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class FullSynthesisResult:
    """Step 3 + 4 + 5 一站式产物。"""

    run_id: str
    version_id: str
    all_points: list[TestPoint] = field(default_factory=list)
    obligations: list[CoverageObligation] = field(default_factory=list)
    cases: list[TestCase] = field(default_factory=list)
    strategy_obligation_coverage: float = 0.0  # Step 4 硬指标
    requirement_item_coverage: CoverageReport | None = None  # 整体软指标
    validated_count: int = 0
    failed_count: int = 0
    code_count: int = 0
    llm_count: int = 0
    hybrid_count: int = 0
    llm_calls: int = 0
    warnings: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


# ============================================================
# GenerationConfig 审计更新（追加 Step 5 prompt 版本）
# ============================================================


def _augment_generation_config(run) -> None:
    """把 Step 5 的 prompt/generator 版本追加到 Run 关联的 GenerationConfig（审计诚实）。

    GenerationConfig 由 Step 3 创建（快照测试点生成的 prompt）；Step 5 复用同一 Run，
    追加用例合成的 prompt 版本，使配置反映整条 Run 用到的全部 prompt。幂等：已含则不重复追加。
    """
    cfg: GenerationConfig | None = repo.get_generation_config(run.generation_config_id)
    if cfg is None:
        return
    if PROMPT_VERSIONS_COMBINED not in cfg.prompt_version:
        cfg.prompt_version = f"{cfg.prompt_version},{PROMPT_VERSIONS_COMBINED}"
    if GENERATOR_VERSION not in cfg.generator_version:
        cfg.generator_version = f"{cfg.generator_version},{GENERATOR_VERSION}"
    repo.save_generation_config(cfg)


# ============================================================
# Step 5 主入口
# ============================================================


def synthesize_test_cases(
    client,
    *,
    run_id: str,
    version_id: str | None = None,
) -> SynthesisResult:
    """消费合流后的 TestPoint[]，1:1 合成 TestCase 并持久化（复用 Step 3/4 的 Run）。

    流程：
      1. 拉 Run（缺省 version_id 从 Run 取），改 status=GENERATING，追加 Step 5 prompt 审计
      2. 拉合流 TestPoint[]（repo.list_test_points_by_run，含 LLM + STRATEGY）
      3. 拉 items + obligations 索引（供 DataPlanner 取 FieldSpec / obligation.params）
      4. 逐 TestPoint（按 id 排序保证 display_id 稳定）：
         DataPlanner → 合成（strategy 模板 / LLM）→ validate_and_build
      5. dedupe（按 fingerprint）→ 持久化（repo.save_test_case 幂等 upsert）
      6. 更新 Run.status=DONE + counts.cases
      7. 返回 SynthesisResult
    """
    # 1. 拉 Run
    run = repo.get_run(run_id)
    if run is None:
        return SynthesisResult(run_id=run_id, version_id=version_id or "", issues=[f"Run 不存在: {run_id}"])
    if version_id is None:
        version_id = run.requirement_version_id
    elif run.requirement_version_id != version_id:
        logger.warning(
            "Run.requirement_version_id=%s 与输入 version_id=%s 不一致，以 Run 为准",
            run.requirement_version_id,
            version_id,
        )
        version_id = run.requirement_version_id

    run.status = RunStatus.GENERATING
    repo.save_run(run)
    _augment_generation_config(run)

    # 2. 拉合流 TestPoint[]
    points = repo.list_test_points_by_run(run_id)
    if not points:
        run.status = RunStatus.DONE
        repo.save_run(run)
        return SynthesisResult(
            run_id=run_id, version_id=version_id, issues=[f"Run {run_id} 无 TestPoint，无法合成用例（请先跑 Step 3/4）"]
        )

    # 3. 拉 items + obligations 索引
    items: list[RequirementItem] = repo.list_items(version_id)
    items_index = {it.id: it for it in items}
    obligations = repo.list_obligations(run_id)
    ob_index = {ob.id: ob for ob in obligations}

    # 4. 逐 TestPoint 合成（按 id 排序 → display_id 稳定，跨重跑一致）
    points_sorted = sorted(points, key=lambda p: p.id)
    cases: list[TestCase] = []
    issues: list[str] = []
    warnings: list[str] = []
    llm_calls = 0

    for i, tp in enumerate(points_sorted):
        display_id = f"TC_{i + 1:03d}"
        # 取 obligation（strategy 回链）+ 主 item（首个 item_id，供 FieldSpec 上下文）
        ob = ob_index.get(tp.obligation_id) if tp.obligation_id else None
        item = items_index.get(tp.item_ids[0]) if tp.item_ids else None

        # 4a. TestDataPlanner
        plan: DataPlanResult = plan_data_for_testpoint(tp, obligation=ob, item=item, client=client)
        issues.extend(plan.issues)
        llm_calls += plan.llm_calls

        # 4b. 合成（strategy → 代码模板；LLM → LLM 合成）
        cand: TestCaseCandidate
        if tp.provenance == Provenance.STRATEGY:
            cand = synthesize_strategy_template(tp, plan)
        else:
            cand = synthesize_with_llm(client, tp, item, plan)
            llm_calls += cand.calls
        issues.extend(cand.issues)

        # LLM 合成失败（无 raw）→ 跳过该 TestPoint（记 warning，不产 FAILED 空壳）
        if not cand.raw:
            warnings.append(f"TestPoint {tp.id}（{tp.title}）合成失败，跳过：{cand.issues or '无产出'}")
            continue

        # 4c. 校验/修复/构建
        build: BuildResult = validate_and_build(
            cand, tp=tp, run_id=run_id, version_id=version_id, display_id=display_id
        )
        issues.extend(build.issues)
        if build.test_case is not None:
            cases.append(build.test_case)
            if build.status == TestCaseStatus.VALIDATION_FAILED:
                warnings.append(f"TestCase {display_id}（tp={tp.id}）校验失败：{'; '.join(build.validation_errors)}")

    # 5. 去重（按 fingerprint）+ 持久化
    cases, dedupe_issues = dedupe_cases(cases)
    issues.extend(dedupe_issues)
    for tc in cases:
        repo.save_test_case(tc)

    # 6. Run 终态 + counts.cases
    run.status = RunStatus.DONE
    run.counts = RunCounts(
        items=run.counts.items or len(items),
        points=run.counts.points or len(points),
        obligations=run.counts.obligations or len(obligations),
        cases=len(cases),
    )
    repo.save_run(run)

    # 7. 统计
    validated = sum(1 for c in cases if c.status == TestCaseStatus.VALIDATED)
    failed = sum(1 for c in cases if c.status == TestCaseStatus.VALIDATION_FAILED)
    code_count = sum(1 for c in cases if c.generation_mode.value == "code")
    llm_count = sum(1 for c in cases if c.generation_mode.value == "llm")
    hybrid_count = sum(1 for c in cases if c.generation_mode.value == "hybrid")

    logger.info(
        "Step 5 用例合成完成: run=%s version=%s points=%d cases=%d (validated=%d failed=%d) "
        "mode(code=%d llm=%d hybrid=%d) llm_calls=%d",
        run_id,
        version_id,
        len(points),
        len(cases),
        validated,
        failed,
        code_count,
        llm_count,
        hybrid_count,
        llm_calls,
    )

    return SynthesisResult(
        run_id=run_id,
        version_id=version_id,
        cases=cases,
        validated_count=validated,
        failed_count=failed,
        code_count=code_count,
        llm_count=llm_count,
        hybrid_count=hybrid_count,
        llm_calls=llm_calls,
        warnings=warnings,
        issues=issues,
    )


# ============================================================
# Step 3 + 4 + 5 一站式入口
# ============================================================


def generate_test_cases_full(
    client,
    *,
    version_id: str,
    user_id: str | None = None,
    generation_config: GenerationConfig | None = None,
    phase_b_batch_threshold: int | None = None,
) -> FullSynthesisResult:
    """Step 3（LLM 测试点）+ Step 4（策略引擎）+ Step 5（用例合成）一站式入口。

    走完整 Run 状态机：PARSING → GENERATING → STRATEGIZING → DONE →（Step 5）GENERATING → DONE。
    返回合流后的 all_points / obligations / cases + 覆盖率双指标 + 用例统计。
    """
    from core.v2.strategy.orchestrator import FullGenerationResult, generate_test_points_full

    # Step 3 + 4
    step34: FullGenerationResult = generate_test_points_full(
        client,
        version_id=version_id,
        user_id=user_id,
        generation_config=generation_config,
        phase_b_batch_threshold=phase_b_batch_threshold,
    )
    if not step34.run_id:
        return FullSynthesisResult(run_id="", version_id=version_id, issues=step34.issues)

    # Step 5（复用同一 Run）
    step5 = synthesize_test_cases(client, run_id=step34.run_id, version_id=version_id)

    logger.info(
        "Step 3+4+5 一站式完成: run=%s points=%d obligations=%d cases=%d obligation_coverage=%.2f",
        step34.run_id,
        len(step34.all_points),
        len(step34.obligations),
        len(step5.cases),
        step34.strategy_obligation_coverage,
    )

    return FullSynthesisResult(
        run_id=step34.run_id,
        version_id=version_id,
        all_points=step34.all_points,
        obligations=step34.obligations,
        cases=step5.cases,
        strategy_obligation_coverage=step34.strategy_obligation_coverage,
        requirement_item_coverage=step34.requirement_item_coverage,
        validated_count=step5.validated_count,
        failed_count=step5.failed_count,
        code_count=step5.code_count,
        llm_count=step5.llm_count,
        hybrid_count=step5.hybrid_count,
        llm_calls=step5.llm_calls,
        warnings=step34.warnings + step5.warnings,
        issues=step34.issues + step5.issues,
    )
