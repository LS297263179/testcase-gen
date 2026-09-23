"""V2 顶层 Runtime - Pipeline Orchestrator（Step 10.2）。

把 Step 2~8 从"人工依次调用多个库函数"变成"单一入口 run_v2_pipeline 一次跑通"。

★ 4 条 P0 架构约束（用户冻结）：
  P0-1 Runtime 创建唯一 Run；底层 orchestrator 在 Runtime 模式下复用传入 run_id，绝不二次创建。
  P0-2 Runtime 是唯一 Run.status 控制者；底层 orchestrator 传 skip_run_status_update=True，不改跨阶段状态。
  P0-3 任一步失败原子记录 status=FAILED + failed_step + error_message 并停止后续步骤。
  P0-4 每阶段记录 started_at / finished_at / duration_ms / artifact_ids。

诚实状态：Run 在 Step 2（IR）之后才出生（Run.requirement_version_id NOT NULL 外键），故不伪造 INGESTING；
  起始 PARSING → GENERATING → STRATEGIZING → GENERATING → REVIEWING → OPTIMIZING → DONE。
  Run.status = 阶段类别；PipelineStepResult.step = 具体阶段，两者严格区分、不混用。

阶段分离：Runtime 自动完成 Step 2~8；Human Edit / Re-review（Step 9）是用户后续主动操作，不在此链内。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.schemas import Run, RunCounts, RunStatus, SourceType
from core.v2 import repository as repo
from core.v2.client_factory import build_llm_client
from core.v2.ir import ingest_and_build_ir
from core.v2.optimizer import OptimizerResult
from core.v2.optimizer_orchestrator import optimize_duplicates
from core.v2.parser import classify_parse_issue
from core.v2.review_orchestrator import review_test_cases
from core.v2.strategy.orchestrator import apply_strategy_engine
from core.v2.tc_orchestrator import synthesize_test_cases
from core.v2.tp_orchestrator import _build_generation_config, generate_test_points

logger = logging.getLogger("v2.runtime")

# 阶段顺序（stop_after / failed_step 取值）
STEP_ORDER = ("ir", "testpoints", "strategy", "testcases", "review", "optimizer")


@dataclass
class PipelineStepResult:
    """单阶段执行记录（P0-4：started_at/finished_at/duration_ms/artifact_ids）。

    step = 具体阶段（ir/testpoints/...）；与 Run.status（阶段类别）严格区分，不混用。
    """

    step: str
    success: bool
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    llm_calls: int = 0
    # LLM 用量对账字段（纯观测，来自 LLMClient.llm_stats() 的阶段增量）：
    # llm_attempts=底层请求次数（含重试），llm_retries=attempts-calls，llm_failures=以异常结束的调用数。
    llm_attempts: int = 0
    llm_retries: int = 0
    llm_failures: int = 0
    error: str | None = None
    artifact_ids: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)  # P1：每步产物计数（前端免重算）


@dataclass
class PipelineResult:
    """run_v2_pipeline 顶层产物。"""

    run_id: str | None
    success: bool
    failed_step: str | None = None
    error_message: str | None = None
    steps: list[PipelineStepResult] = field(default_factory=list)
    total_duration_ms: int = 0
    total_llm_calls: int = 0
    doc_id: str | None = None
    version_id: str | None = None
    test_point_count: int = 0
    test_case_count: int = 0
    review_report_id: str | None = None
    optimizer_result: OptimizerResult | None = None


def _ms(t0: float) -> int:
    """自 t0（perf_counter）以来的毫秒数。"""
    return int((time.perf_counter() - t0) * 1000)


def run_v2_pipeline(
    *,
    user_id: str,
    title: str,
    text: str | None = None,
    paths: list[str] | None = None,
    source_type: SourceType = SourceType.TEXT,
    client=None,
    stop_after: str | None = None,
) -> PipelineResult:
    """V2 全链路顶层入口（Step 2→3+4+5→7→8），一次调用跑通。

    - client 显式传入（测试 / CLI）→ 全链复用同一 client；缺省 → 用 client_factory 按用途构建。
    - stop_after ∈ STEP_ORDER：跑到该阶段后 set DONE 返回（"ir" 阶段 Run 未出生，直接返回）。
    - 4 条 P0 约束见模块 docstring。
    """
    pipeline_t0 = time.perf_counter()
    steps: list[PipelineStepResult] = []
    total_llm_calls = 0

    explicit_client = client is not None
    gen_client = client or build_llm_client("generate")
    review_client = gen_client if explicit_client else build_llm_client("review")

    def _usage(client_obj) -> dict:
        """LLMClient 用量快照；非插桩客户端（测试 FakeClient）一律按 0 处理，不影响业务判定。"""
        stats = getattr(client_obj, "llm_stats", None)
        if not callable(stats):
            return {"calls": 0, "attempts": 0, "successes": 0, "failures": 0}
        return dict(stats())

    def _usage_fields(client_obj, before: dict, reported: int | None = None) -> dict:
        """阶段级 LLM 用量增量。reported 非空时保留该阶段原有的自报口径，只额外补对账字段。"""
        after = _usage(client_obj)
        calls = after["calls"] - before["calls"]
        attempts = after["attempts"] - before["attempts"]
        return {
            "llm_calls": calls if reported is None else reported,
            "llm_attempts": attempts,
            "llm_retries": max(0, attempts - calls),
            "llm_failures": after["failures"] - before["failures"],
        }

    run: Run | None = None
    doc_id: str | None = None
    version_id: str | None = None
    optimizer_result: OptimizerResult | None = None

    if stop_after is not None and stop_after not in STEP_ORDER:
        logger.warning("stop_after=%s 非法（合法值 %s），忽略并跑完整链", stop_after, STEP_ORDER)
        stop_after = None

    # ---- 内部工具（均为只读闭包，读取外层最新状态）----
    def _result(success: bool, failed_step: str | None = None, error_message: str | None = None) -> PipelineResult:
        latest = repo.get_latest_review_report(run.id) if run else None
        return PipelineResult(
            run_id=run.id if run else None,
            success=success,
            failed_step=failed_step,
            error_message=error_message,
            steps=steps,
            total_duration_ms=int((time.perf_counter() - pipeline_t0) * 1000),
            total_llm_calls=total_llm_calls,
            doc_id=doc_id,
            version_id=version_id,
            test_point_count=len(repo.list_test_points_by_run(run.id)) if run else 0,
            test_case_count=len(repo.list_test_cases(run.id)) if run else 0,
            review_report_id=latest.id if latest else None,
            optimizer_result=optimizer_result,
        )

    def _fail(step: str, started: datetime, t0: float, exc: Exception) -> PipelineResult:
        """P0-3：原子记录失败步骤 + Run.FAILED（Run 已出生时）+ failed_step + error_message，随后停止。"""
        msg = f"{type(exc).__name__}: {exc}"
        steps.append(PipelineStepResult(step, False, started, datetime.now(UTC), _ms(t0), error=msg))
        if run is not None:  # ir 阶段 Run 未出生 → 仅 PipelineResult 记 failed_step（诚实）
            run.status = RunStatus.FAILED
            run.failed_step = step
            run.error_message = msg
            repo.save_run(run)
        logger.exception("V2 pipeline 失败于 step=%s", step)
        return _result(False, failed_step=step, error_message=msg)

    def _set_status(status: RunStatus) -> None:
        """P0-2：Run.status 仅由 Runtime 写。"""
        if run is not None:
            run.status = status
            repo.save_run(run)

    def _finish() -> PipelineResult:
        """正常收尾：set DONE + 聚合写 Run.counts（Runtime 独家写 status/counts）。"""
        if run is not None:
            run.status = RunStatus.DONE
            run.counts = RunCounts(
                items=len(repo.list_items(version_id)),
                points=len(repo.list_test_points_by_run(run.id)),
                cases=len(repo.list_test_cases(run.id)),
                obligations=len(repo.list_obligations(run.id)),
            )
            repo.save_run(run)
        return _result(True)

    # ================= Step 2：IR（Run 尚未出生）=================
    started, t0 = datetime.now(UTC), time.perf_counter()
    ir_before = _usage(gen_client)
    try:
        ir = ingest_and_build_ir(
            gen_client, user_id=user_id, title=title, text=text, paths=paths, source_type=source_type
        )
    except Exception as exc:
        return _fail("ir", started, t0, exc)
    ir_usage = _usage_fields(gen_client, ir_before)
    total_llm_calls += ir_usage["llm_calls"]
    ir_parse = getattr(ir, "parse", None)
    ir_issues = list(getattr(ir_parse, "issues", []) or []) if ir_parse is not None else []
    issue_kinds = sorted({classify_parse_issue(x) for x in ir_issues})
    doc_id, version_id = ir.doc.id, ir.version.id
    if not ir.items:
        steps.append(
            PipelineStepResult(
                "ir",
                False,
                started,
                datetime.now(UTC),
                _ms(t0),
                error="IR 未产出 items",
                artifact_ids={"doc_id": doc_id, "version_id": version_id},
                counts={"items": 0, "parse_issues": len(ir_issues), "parse_issue_kinds": issue_kinds},
                **ir_usage,
            )
        )
        return _result(False, failed_step="ir", error_message="IR 未产出 items，无法生成测试点")
    steps.append(
        PipelineStepResult(
            "ir",
            True,
            started,
            datetime.now(UTC),
            _ms(t0),
            artifact_ids={"doc_id": doc_id, "version_id": version_id},
            counts={"items": len(ir.items), "parse_issues": len(ir_issues), "parse_issue_kinds": issue_kinds},
            **ir_usage,
        )
    )
    if stop_after == "ir":
        return _result(True)  # 仅到 IR：Run 未出生

    # ================= 创建唯一 Run（P0-1）=================
    cfg = _build_generation_config()
    repo.save_generation_config(cfg)
    run = Run(
        user_id=user_id,
        doc_id=doc_id,
        requirement_version_id=version_id,
        generation_config_id=cfg.id,
        status=RunStatus.PARSING,
    )
    repo.save_run(run)

    # ================= Step 3：TestPoints（GENERATING）=================
    _set_status(RunStatus.GENERATING)
    started, t0 = datetime.now(UTC), time.perf_counter()
    tp_before = _usage(gen_client)
    try:
        tp_res = generate_test_points(
            gen_client, version_id=version_id, user_id=user_id, run_id=run.id, skip_run_status_update=True
        )
    except Exception as exc:
        return _fail("testpoints", started, t0, exc)
    tp_calls = tp_res.phase_a.calls + tp_res.phase_b.calls
    tp_usage = _usage_fields(gen_client, tp_before, reported=tp_calls)
    total_llm_calls += tp_calls
    steps.append(
        PipelineStepResult(
            "testpoints",
            True,
            started,
            datetime.now(UTC),
            _ms(t0),
            artifact_ids={"run_id": run.id},
            counts={"llm": len(tp_res.points)},
            **tp_usage,
        )
    )
    if stop_after == "testpoints":
        return _finish()

    # ================= Step 4：Strategy（STRATEGIZING）=================
    _set_status(RunStatus.STRATEGIZING)
    started, t0 = datetime.now(UTC), time.perf_counter()
    st_before = _usage(gen_client)
    try:
        st_res = apply_strategy_engine(run.id, version_id, skip_run_status_update=True)
    except Exception as exc:
        return _fail("strategy", started, t0, exc)
    steps.append(
        PipelineStepResult(
            "strategy",
            True,
            started,
            datetime.now(UTC),
            _ms(t0),
            artifact_ids={"run_id": run.id},
            counts={"strategy": len(st_res.points), "obligations": len(st_res.obligations)},
            **_usage_fields(gen_client, st_before),
        )
    )
    if stop_after == "strategy":
        return _finish()

    # ================= Step 5：TestCases（GENERATING）=================
    _set_status(RunStatus.GENERATING)
    started, t0 = datetime.now(UTC), time.perf_counter()
    tc_before = _usage(gen_client)
    try:
        tc_res = synthesize_test_cases(gen_client, run_id=run.id, version_id=version_id, skip_run_status_update=True)
    except Exception as exc:
        return _fail("testcases", started, t0, exc)
    total_llm_calls += tc_res.llm_calls
    steps.append(
        PipelineStepResult(
            "testcases",
            True,
            started,
            datetime.now(UTC),
            _ms(t0),
            artifact_ids={"run_id": run.id},
            counts={"cases": len(tc_res.cases), "validated": tc_res.validated_count, "failed": tc_res.failed_count},
            **_usage_fields(gen_client, tc_before, reported=tc_res.llm_calls),
        )
    )
    if stop_after == "testcases":
        return _finish()

    # ================= Step 7：Review（REVIEWING）=================
    _set_status(RunStatus.REVIEWING)
    started, t0 = datetime.now(UTC), time.perf_counter()
    rv_before = _usage(review_client)
    try:
        rv_res = review_test_cases(review_client, run_id=run.id, skip_run_status_update=True)
    except Exception as exc:
        return _fail("review", started, t0, exc)
    rv_usage = _usage_fields(review_client, rv_before)
    total_llm_calls += rv_usage["llm_calls"]
    steps.append(
        PipelineStepResult(
            "review",
            True,
            started,
            datetime.now(UTC),
            _ms(t0),
            artifact_ids={"run_id": run.id},
            counts={"reviewed": rv_res.reviewed_count},
            **rv_usage,
        )
    )
    if stop_after == "review":
        return _finish()

    # ================= Step 8：Optimizer（OPTIMIZING）=================
    _set_status(RunStatus.OPTIMIZING)
    started, t0 = datetime.now(UTC), time.perf_counter()
    op_before = _usage(gen_client)
    try:
        op_res = optimize_duplicates(gen_client, run_id=run.id, skip_run_status_update=True)
    except Exception as exc:
        return _fail("optimizer", started, t0, exc)
    op_usage = _usage_fields(gen_client, op_before)
    total_llm_calls += op_usage["llm_calls"]
    optimizer_result = op_res.optimizer_result
    steps.append(
        PipelineStepResult(
            "optimizer",
            True,
            started,
            datetime.now(UTC),
            _ms(t0),
            artifact_ids={"run_id": run.id},
            counts={"archived": optimizer_result.archived_cases if optimizer_result else 0},
            **op_usage,
        )
    )

    # ================= DONE + counts =================
    return _finish()
