"""V2 测试点生成编排 - Phase A + Phase B → Validator → Repository（Step 3 顶层入口）。

对应 PROGRESS.md §2 蓝图 `Requirement IR → Test Point Generator`。
严格边界：
  - 只做 provenance=LLM 的 TestPoint；CoverageObligation 与 provenance=STRATEGY 的 TestPoint 是 Step 4。
  - 不动 V1 运行时（web/data.py、core/generator.py、data.db）。
  - 不接 API/前端（沿用 Step 2 边界）。

幂等身份：TestPoint.fingerprint（见 core/v2/fingerprint.py），同一 version_id + scope + item_ids +
module + subcategory + normalize(title) 的多次生成复用同一 ULID。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.config import get_model_config
from core.schemas import (
    GenerationConfig,
    RequirementItem,
    Run,
    RunCounts,
    RunStatus,
    TestPoint,
)
from core.v2 import repository as repo
from core.v2.tp_generator import GeneratorResult, complete_cross_item, generate_for_item
from core.v2.tp_prompts import PROMPT_VERSIONS_COMBINED
from core.v2.tp_validator import (
    CoverageReport,
    build_coverage_report,
    dedupe_points,
    validate_phase_a,
    validate_phase_b,
    warn_semantic_duplicates,
)

logger = logging.getLogger("v2.tp_orchestrator")

# 生成器版本（用于 GenerationConfig.generator_version 审计）
GENERATOR_VERSION = "tp-gen-v1"


@dataclass
class PhaseStats:
    """单阶段统计（调试/审计用）。"""

    calls: int = 0
    raw_count: int = 0
    valid_count: int = 0
    dropped_count: int = 0
    issues: list[str] = field(default_factory=list)


@dataclass
class GenerationResult:
    """Step 3 顶层产物。"""

    run_id: str
    version_id: str
    points: list[TestPoint] = field(default_factory=list)
    coverage: CoverageReport | None = None
    phase_a: PhaseStats = field(default_factory=PhaseStats)
    phase_b: PhaseStats = field(default_factory=PhaseStats)
    warnings: list[str] = field(default_factory=list)  # 语义相似等软提示
    issues: list[str] = field(default_factory=list)  # 全局问题（Run/Version 层面）


# ============================================================
# GenerationConfig 构造
# ============================================================


def _build_generation_config() -> GenerationConfig:
    """从 config.yaml 的 generate 段读取模型配置，构造 GenerationConfig 快照。

    Step 3 只涉及生成器（不涉及评审器），reviewer_version 留空（Step 7 才用）。
    """
    try:
        cfg = get_model_config().get("generate", {}) or {}
    except Exception:
        logger.exception("读取模型配置失败，使用兜底默认值")
        cfg = {}
    return GenerationConfig(
        model_provider=str(cfg.get("api_type") or "openai"),
        model_name=str(cfg.get("model") or "unknown"),
        temperature=float(cfg.get("temperature") or 0.3),
        max_tokens=int(cfg["max_tokens"]) if cfg.get("max_tokens") else None,
        enable_thinking=bool(cfg["enable_thinking"]) if cfg.get("enable_thinking") is not None else None,
        prompt_version=PROMPT_VERSIONS_COMBINED,
        generator_version=GENERATOR_VERSION,
        reviewer_version=None,
    )


# ============================================================
# 顶层入口
# ============================================================


def generate_test_points(
    client,
    *,
    version_id: str,
    user_id: str | None = None,
    generation_config: GenerationConfig | None = None,
    phase_b_batch_threshold: int | None = None,
) -> GenerationResult:
    """Step 3 顶层入口：给定 RequirementVersion，两阶段生成 TestPoint 并持久化。

    流程：
      1. 拉 Version 与 items（items 为空则直接返回）
      2. 建 GenerationConfig + Run（status=PARSING → GENERATING）
      3. Phase A：逐 item 调 LLM → validate_phase_a → 汇总
      4. Phase B：全量 items + Phase A 摘要 → complete_cross_item → validate_phase_b → 汇总
      5. dedupe（按 fingerprint）+ 语义相似 warning
      6. 注入 run_id → upsert TestPoint（Repository 层按 fingerprint 幂等）
      7. 覆盖率报告 → 更新 Run.status=DONE + counts
      8. 返回 GenerationResult

    - client 需实现 .chat(system_prompt, user_prompt, images=None) -> str（V1 LLMClient 接口）。
    - user_id 缺省时从 Version → Doc → user_id 反查。
    - generation_config 缺省时从 config.yaml 构造快照。
    - phase_b_batch_threshold 缺省用 tp_generator.PHASE_B_BATCH_THRESHOLD。
    """
    result_issues: list[str] = []

    # 1. 拉 Version 与 items
    version = repo.get_version(version_id)
    if version is None:
        return GenerationResult(run_id="", version_id=version_id, issues=[f"RequirementVersion 不存在: {version_id}"])
    items: list[RequirementItem] = repo.list_items(version_id)
    if not items:
        return GenerationResult(
            run_id="",
            version_id=version_id,
            issues=[f"RequirementVersion {version_id} 无 items，无法生成测试点"],
        )

    # 2. user_id 兜底：从 Doc 反查
    if not user_id:
        doc = repo.get_doc(version.doc_id)
        if doc is None:
            return GenerationResult(
                run_id="",
                version_id=version_id,
                issues=[f"RequirementDoc 不存在: {version.doc_id}"],
            )
        user_id = doc.user_id

    # 3. 建 GenerationConfig + Run
    cfg = generation_config or _build_generation_config()
    repo.save_generation_config(cfg)
    run = Run(
        user_id=user_id,
        doc_id=version.doc_id,
        requirement_version_id=version_id,
        generation_config_id=cfg.id,
        status=RunStatus.PARSING,
    )
    repo.save_run(run)

    # 4. Phase A：逐 item 生成
    run.status = RunStatus.GENERATING
    repo.save_run(run)

    phase_a_points: list[TestPoint] = []
    phase_a_stats = PhaseStats()
    items_index = {it.id: it for it in items}
    for item in items:
        gen_res: GeneratorResult = generate_for_item(client, item)
        phase_a_stats.calls += gen_res.calls
        phase_a_stats.raw_count += len(gen_res.raw_points)
        phase_a_stats.issues.extend(gen_res.issues)
        val_res = validate_phase_a(gen_res.raw_points, item=item, items_index=items_index)
        phase_a_points.extend(val_res.points)
        phase_a_stats.issues.extend(val_res.issues)
    phase_a_stats.valid_count = len(phase_a_points)
    phase_a_stats.dropped_count = phase_a_stats.raw_count - phase_a_stats.valid_count

    # 5. Phase B：跨项补漏
    phase_b_points: list[TestPoint] = []
    phase_b_stats = PhaseStats()
    if items and phase_a_points:
        # Phase B 需要看到 Phase A 全貌；若 Phase A 完全空则跳过（无参照系）
        kwargs = {}
        if phase_b_batch_threshold is not None:
            kwargs["batch_threshold"] = phase_b_batch_threshold
        gen_res_b = complete_cross_item(client, items, phase_a_points, **kwargs)
        phase_b_stats.calls = gen_res_b.calls
        phase_b_stats.raw_count = len(gen_res_b.raw_points)
        phase_b_stats.issues.extend(gen_res_b.issues)
        val_res_b = validate_phase_b(gen_res_b.raw_points, items=items, version_id=version_id)
        phase_b_points.extend(val_res_b.points)
        phase_b_stats.issues.extend(val_res_b.issues)
        phase_b_stats.valid_count = len(phase_b_points)
        phase_b_stats.dropped_count = phase_b_stats.raw_count - phase_b_stats.valid_count
    elif not phase_a_points:
        phase_b_stats.issues.append("Phase A 无产出，跳过 Phase B（无参照系）")

    # 6. 去重 + 语义相似 warning
    all_points = phase_a_points + phase_b_points
    deduped, dedupe_issues = dedupe_points(all_points)
    warnings = warn_semantic_duplicates(deduped)

    # 7. 注入 run_id + 持久化
    for tp in deduped:
        tp.run_id = run.id
        # run_id 不参与 fingerprint 计算（同 version 下不同 run 应视为同一业务身份，幂等复用）
        repo.save_test_point(tp)

    # 8. 覆盖率报告 + Run 终态
    coverage = build_coverage_report(deduped, items)
    run.status = RunStatus.DONE
    run.counts = RunCounts(items=len(items), points=len(deduped))
    repo.save_run(run)

    logger.info(
        "Step 3 测试点生成完成: version=%s run=%s items=%d points=%d (A=%d B=%d 去重=%d) 覆盖率=%.2f",
        version_id,
        run.id,
        len(items),
        len(deduped),
        phase_a_stats.valid_count,
        phase_b_stats.valid_count,
        len(dedupe_issues),
        coverage.coverage_ratio,
    )

    return GenerationResult(
        run_id=run.id,
        version_id=version_id,
        points=deduped,
        coverage=coverage,
        phase_a=phase_a_stats,
        phase_b=phase_b_stats,
        warnings=warnings,
        issues=result_issues + dedupe_issues,
    )


# ============================================================
# 便捷入口：文件 → IR → TestPoint 一步到位
# ============================================================


def generate_test_points_from_files(
    client,
    *,
    user_id: str,
    title: str,
    text: str | None = None,
    paths: list[str] | None = None,
    source_type: str = "text",
) -> GenerationResult:
    """Step 2 ingest_and_build_ir + Step 3 generate_test_points 一步到 TestPoint。

    - source_type 传给 Step 2 的 SourceType（默认 text）。
    - 返回 GenerationResult；若 IR 构建失败则返回带 issues 的空结果。
    """
    from core.schemas import SourceType
    from core.v2.ir import ingest_and_build_ir

    try:
        st = SourceType(source_type)
    except ValueError:
        st = SourceType.TEXT

    ir_result = ingest_and_build_ir(
        client,
        user_id=user_id,
        title=title,
        text=text,
        paths=paths,
        source_type=st,
    )
    if not ir_result.items:
        return GenerationResult(
            run_id="",
            version_id=ir_result.version.id,
            issues=[f"IR 构建未产出 items: {ir_result.parse.issues if ir_result.parse else '未知'}"],
        )
    return generate_test_points(client, version_id=ir_result.version.id, user_id=user_id)
