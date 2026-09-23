"""V2 Step 11 S3 确定性 Hard Metrics（CODE provenance 指标层，零 LLM）。

输入契约（S3 修正 1）—— RunObservation 同时携带四类输入，评价层不再只依赖 Repository：
  1. BenchmarkGold           人工独立标准（GOLD-BASED 轨道）
  2. EvalArtifacts           Repository 只读产物（items / TP / TC / obligations / ReviewReport）
  3. PipelineResult 折算值    total_duration_ms / total_llm_calls / steps（Cost/Latency 数据源，S3 修正 5）
  4. GenerationConfig        模型/Prompt/生成器/评审器版本（环境指纹透传）

统一 RunStatus 输入契约（S3 修正）：评价层只消费 BenchmarkRunStatus（五态在 schema.py 冻结），
真实日志捕获与分类留在 S6 Runner 实现；本层绝不修改 Runtime。

口径冻结（docs/v2/step11-benchmark-evaluation.md §6~§9 + S3 修正 3）：
  - 非 COMPLETED 不进质量分母（quality_evaluated=False，质量字段 None；Cost/counts 仍记录）；
  - Gold MISS / INVALID / PENDING_REVIEW 三者分开统计，不混成一个模糊指标；
  - S3 不判 ADDITIONAL_VALID：Gold 外产物只登记为 pending_review（kind=unverified_output），
    人工裁决后才能成为 ADDITIONAL_VALID；
  - Duplication 复用 Step 7 review_hard.compute_duplication，EXACT 与 SEMANTIC 分别呈现（S3 修正 4）；
  - TP/TC 数量仅观察，不设门槛；
  - REVIEWER-BASED 仅透传 Step 7 产物（并列展示，不与 GOLD-BASED 混算）。

S7 三轨口径（架构裁决 D1/D2/D3/D9/D10，详见设计文档附录 C）：
  - `AUTO_HIT` 正式定义为 **"确定性关联命中"**，不再等同 fingerprint exact；关联由 `rails.associate_gold`
    按 `identity > bridge > anchor` 优先级去重产出，每条命中带 `via` 标注；
  - `requirement_coverage` 分子 = 确定性关联到的 Gold 条数（含 anchor 多义，item_id 不猜）；
  - `identity_match_rate` 是**独立诊断单元**（Identity Rail 仅衡量 parser 身份稳定性），
    严禁把 `identity_match_rate=0` 解读为 requirement coverage=0；
  - scenario / strategy 判定不再以 identity AUTO_HIT 作为准入门；
  - obligation / structural / duplication / reviewer 四项口径**完全不变**。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.schemas import (
    CoverageObligation,
    GenerationConfig,
    Provenance,
    RequirementItem,
    ReviewReport,
    TestCase,
    TestPoint,
)
from core.v2.eval.matching import (
    ArtifactIndex,
    ItemMatchState,
    ObligationState,
    ScenarioState,
    match_obligations,
)
from core.v2.eval.rails import RailPending, associate_gold
from core.v2.eval.schema import BenchmarkGold, BenchmarkRunStatus, MetricProvenance
from core.v2.review_hard import compute_duplication, compute_executability_structural

# ============================================================
# 输入契约
# ============================================================


@dataclass
class EvalArtifacts:
    """Repository 只读产物（S6 Runner 负责从 benchmark DB 载入并填充）。"""

    items: list[RequirementItem] = field(default_factory=list)
    test_points: list[TestPoint] = field(default_factory=list)
    test_cases: list[TestCase] = field(default_factory=list)
    obligations: list[CoverageObligation] = field(default_factory=list)
    obligation_covered_points: dict[str, list[str]] = field(default_factory=dict)  # obligation_id → TP ids
    review_report: ReviewReport | None = None  # REVIEWER-BASED 轨道唯一来源（透传，不重算）

    def to_index(self) -> ArtifactIndex:
        return ArtifactIndex(
            items=self.items,
            test_points=self.test_points,
            test_cases=self.test_cases,
            obligations=self.obligations,
            obligation_covered_points=self.obligation_covered_points,
        )


@dataclass
class StepTiming:
    """单阶段耗时/调用数（从 PipelineStepResult 折算，属性鸭子类型解耦 Runtime）。

    llm_attempts / llm_retries / llm_failures 来自 LLMClient 的阶段增量快照，
    与 llm_calls 并列用于成本对账（老 payload 缺这些字段时按 0 处理）。
    """

    step: str
    success: bool = True
    duration_ms: int = 0
    llm_calls: int = 0
    llm_attempts: int = 0
    llm_retries: int = 0
    llm_failures: int = 0
    counts: dict = field(default_factory=dict)


@dataclass
class RunObservation:
    """一次 case 运行的统一观测输入（RunStatus 输入契约见模块 docstring）。"""

    case_id: str
    gold: BenchmarkGold
    status: BenchmarkRunStatus
    run_id: str | None = None
    artifacts: EvalArtifacts = field(default_factory=EvalArtifacts)
    generation_config: GenerationConfig | None = None
    total_duration_ms: int = 0
    total_llm_calls: int = 0
    steps: list[StepTiming] = field(default_factory=list)
    reliability_note: str = ""  # S6 分类器的判断依据透传（启发式说明，见设计文档 §9.3）

    @classmethod
    def from_pipeline_result(
        cls,
        pipeline_result,
        *,
        case_id: str,
        gold: BenchmarkGold,
        status: BenchmarkRunStatus,
        artifacts: EvalArtifacts | None = None,
        generation_config: GenerationConfig | None = None,
        reliability_note: str = "",
    ) -> RunObservation:
        """从 PipelineResult 折算 RunObservation（S6 Runner 使用；不改 Runtime，鸭子类型读取）。"""
        steps = [
            StepTiming(
                step=s.step,
                success=bool(s.success),
                duration_ms=int(s.duration_ms),
                llm_calls=int(getattr(s, "llm_calls", 0) or 0),
                llm_attempts=int(getattr(s, "llm_attempts", 0) or 0),
                llm_retries=int(getattr(s, "llm_retries", 0) or 0),
                llm_failures=int(getattr(s, "llm_failures", 0) or 0),
                counts=dict(getattr(s, "counts", {}) or {}),
            )
            for s in getattr(pipeline_result, "steps", []) or []
        ]
        return cls(
            case_id=case_id,
            gold=gold,
            status=status,
            run_id=getattr(pipeline_result, "run_id", None),
            artifacts=artifacts or EvalArtifacts(),
            generation_config=generation_config,
            total_duration_ms=int(getattr(pipeline_result, "total_duration_ms", 0) or 0),
            total_llm_calls=int(getattr(pipeline_result, "total_llm_calls", 0) or 0),
            steps=steps,
            reliability_note=reliability_note,
        )


# ============================================================
# 输出结构
# ============================================================


@dataclass
class MetricCell:
    """单个指标单元：分子/分母 + 比值 [0,1]（None=不可评/不计分）+ provenance + 依据。"""

    numerator: int | float | None = None
    denominator: int | float | None = None
    value: float | None = None
    provenance: MetricProvenance = MetricProvenance.CODE
    detail: str = ""


@dataclass
class InvalidFinding:
    """INVALID：代码可明确判错（结构缺陷 / trace 断链 / 校验失败），与 MISS / PENDING_REVIEW 分列。"""

    target_type: str  # testcase / testpoint
    target_id: str
    display_id: str
    reason: str


@dataclass
class PendingReviewItem:
    """PENDING_REVIEW：需人工裁决的条目（S3 不判 ADDITIONAL_VALID，修正 3）。"""

    kind: str  # match_candidate / match_ambiguous / unsupported_item / unverified_output
    ref_id: str
    detail: str


@dataclass
class CostLatency:
    """Cost/Latency（S3 修正 5）：同进程从 PipelineResult 折算 + 归一化观察值。"""

    total_duration_ms: int = 0
    total_llm_calls: int = 0
    per_step: list[StepTiming] = field(default_factory=list)
    duration_ms_per_case: float | None = None  # 归一化：每条 TC 耗时（TC=0 → None，不伪造 0）
    llm_calls_per_case: float | None = None
    llm_failure_steps: list[str] = field(default_factory=list)  # 失败阶段（success=False）观察


@dataclass
class CountsObservation:
    """数量观察（不设门槛，冻结 §3.5/修正）：TP/TC 数量只作观察指标。"""

    items: int = 0
    test_points: int = 0
    test_cases: int = 0
    obligations: int = 0
    strategy_points: int = 0
    llm_points: int = 0


@dataclass
class ReviewerBasedSnapshot:
    """REVIEWER-BASED 轨道：Step 7 产物原样透传（双轨并列展示，不互算，§7）。"""

    report_id: str | None = None
    overall_score: float | None = None
    scores: dict[str, float] = field(default_factory=dict)
    obligation_coverage: float | None = None
    finding_count: int = 0
    findings_by_provenance: dict[str, int] = field(default_factory=dict)
    provenance: MetricProvenance = MetricProvenance.MIXED  # 混合来源（VALIDATOR 硬 + LLM 软）透传标注


@dataclass
class HardMetricsReport:
    """单 case 的确定性评价报告（GOLD-BASED 硬指标 + INVALID/PENDING/MISS 分列 + Cost/双轨透传）。"""

    case_id: str
    run_status: BenchmarkRunStatus
    quality_evaluated: bool  # status == COMPLETED 才进质量口径
    run_id: str | None = None

    # --- GOLD-BASED 核心硬指标 ---
    requirement_coverage: MetricCell = field(default_factory=MetricCell)
    critical_scenario_coverage: MetricCell = field(default_factory=MetricCell)
    obligation_coverage: MetricCell = field(default_factory=MetricCell)
    structural_validity: MetricCell = field(default_factory=MetricCell)
    duplication_score: MetricCell = field(default_factory=MetricCell)
    requirement_precision: MetricCell = field(default_factory=MetricCell)  # 辅助观察
    test_point_precision: MetricCell = field(default_factory=MetricCell)  # 辅助观察（trace 代理口径）

    # --- S7 三轨诊断（裁决 D1：Identity Rail 与 requirement_coverage 并列展示，互不代替）---
    identity_match_rate: MetricCell = field(default_factory=MetricCell)  # fingerprint 精确命中率（仅诊断）
    via_counts: dict = field(default_factory=dict)  # {identity, bridge, anchor, candidate, ambiguous, miss}
    identity_diagnostics: dict = field(default_factory=dict)  # parser 身份稳定性明细
    anchor_scope: str = ""  # anchor haystack 口径（裁决 D9）

    # --- 三态分列（MISS / INVALID / PENDING_REVIEW 不混算）---
    requirement_matches: list = field(default_factory=list)  # ItemMatch 明细
    scenario_outcomes: list = field(default_factory=list)  # ScenarioOutcome 明细
    obligation_outcomes: list = field(default_factory=list)  # ObligationOutcome 明细
    strategy_outcomes: list = field(default_factory=list)  # StrategyOutcome 明细
    missing_risk: dict = field(default_factory=dict)  # Gold MISS / 义务缺口 / 场景缺口 / 策略未满足
    invalid: list[InvalidFinding] = field(default_factory=list)
    pending_review: list[PendingReviewItem] = field(default_factory=list)
    duplication_exact: list[dict] = field(default_factory=list)  # S3 修正 4：EXACT 单列
    duplication_semantic: list[dict] = field(default_factory=list)  # SEMANTIC 单列

    # --- 可靠性/成本/观察 ---
    cost_latency: CostLatency = field(default_factory=CostLatency)
    counts: CountsObservation = field(default_factory=CountsObservation)
    reviewer_based: ReviewerBasedSnapshot | None = None  # 双轨并列（REVIEWER-BASED）


# ============================================================
# 计算入口
# ============================================================


def _ratio(numerator: int, denominator: int) -> MetricCell:
    """安全比值：分母 0 → value=None（不伪造满分/零分）。"""
    if denominator == 0:
        return MetricCell(value=None, detail="分母为 0，不可评")
    return MetricCell(numerator=numerator, denominator=denominator, value=round(numerator / denominator, 4))


def _collect_invalid(obs: RunObservation) -> list[InvalidFinding]:
    """INVALID（代码可明确判错）三路：结构缺陷（复用 review_hard 结构层）+ trace 断链 + 校验失败。"""
    findings: list[InvalidFinding] = []
    tc_by_id = {tc.id: tc for tc in obs.artifacts.test_cases}
    tp_ids = {tp.id for tp in obs.artifacts.test_points}

    # 1) 结构缺陷：steps 空 / action 空 / expected 空（复用 Step 7 代码口径，不另写一套）
    for f in compute_executability_structural(obs.artifacts.test_cases)[1]:
        tc = tc_by_id.get(f.target_id)
        findings.append(InvalidFinding("testcase", f.target_id, tc.display_id if tc else "?", f"结构缺陷：{f.issue}"))

    for tc in obs.artifacts.test_cases:
        # 2) 校验失败（状态机/验证器判定，代码已定格）
        if tc.validation_errors:
            findings.append(
                InvalidFinding(
                    "testcase", tc.id, tc.display_id, "validation_errors：" + "；".join(tc.validation_errors)
                )
            )
        # 3) trace 断链：无 TP 来源或来源不存在
        if not tc.test_point_ids:
            findings.append(InvalidFinding("testcase", tc.id, tc.display_id, "trace 断链：test_point_ids 为空"))
        else:
            dangling = [pid for pid in tc.test_point_ids if pid not in tp_ids]
            if dangling:
                findings.append(
                    InvalidFinding("testcase", tc.id, tc.display_id, f"trace 断链：test_point_ids 悬空 {dangling}")
                )

    ob_ids = {ob.id for ob in obs.artifacts.obligations}
    for tp in obs.artifacts.test_points:
        # 4) TP 追溯断链：无 item 来源（strategy TP 走 obligation 校验）
        if not tp.item_ids:
            findings.append(InvalidFinding("testpoint", tp.id, tp.title, "trace 断链：item_ids 为空"))
        if tp.obligation_id is not None and tp.obligation_id not in ob_ids:
            findings.append(InvalidFinding("testpoint", tp.id, tp.title, f"obligation_id 悬空：{tp.obligation_id}"))
    return findings


def _collect_pending(obs: RunObservation, mr, rail_pending: tuple[RailPending, ...] = ()) -> list[PendingReviewItem]:
    """PENDING_REVIEW 收集（修正 3：Gold 外生成物只登记，不判 ADDITIONAL_VALID）。

    `rail_pending` 为 S7 三轨关联层新增的待裁决条目（裁决 D10：anchor 多义 / 不可反查、场景不可判），
    与 S3 既有 kind 并列，不覆盖、不互算。
    """
    pending: list[PendingReviewItem] = []
    for m in mr.matches:
        if m.state == ItemMatchState.CANDIDATE:
            pending.append(
                PendingReviewItem("match_candidate", m.gold_id, f"similarity={m.similarity}（不计分）：{m.basis}")
            )
        elif m.state == ItemMatchState.AMBIGUOUS:
            pending.append(PendingReviewItem("match_ambiguous", m.gold_id, f"候选 items={m.item_ids}：{m.basis}"))
    # 未命中 Gold 的 runtime items → unsupported 候选（是否合理新增，人工裁决后才可能成为 ADDITIONAL_VALID）
    for item_id in mr.unmatched_item_ids:
        pending.append(
            PendingReviewItem("unsupported_item", item_id, "未被任何 Gold AUTO_HIT；待人工裁决（不自动判错/判对）")
        )
    # Gold 外生成物（TP 未 trace 到任何 AUTO_HIT item）→ 仅登记
    hit_item_ids = set(mr.consumed_item_ids)
    for tp in obs.artifacts.test_points:
        if tp.item_ids and not (set(tp.item_ids) & hit_item_ids):
            pending.append(
                PendingReviewItem(
                    "unverified_output",
                    tp.id,
                    f"TestPoint 未关联任何 Gold 命中项（title={tp.title}）：仅登记，不判 ADDITIONAL_VALID",
                )
            )
    for rp in rail_pending:
        pending.append(PendingReviewItem(rp.kind, rp.ref_id, rp.detail))
    return pending


def evaluate_hard_metrics(obs: RunObservation) -> HardMetricsReport:
    """单 case 确定性评价入口（全 CODE provenance，除 reviewer_based 透传外无 LLM）。

    非 COMPLETED：只记录运行观测（cost/counts/可靠性注记），质量口径整体置 None 不进分母。
    """
    report = HardMetricsReport(
        case_id=obs.case_id,
        run_status=obs.status,
        quality_evaluated=obs.status == BenchmarkRunStatus.COMPLETED,
        run_id=obs.run_id,
    )

    # ---- Cost / Latency 与 counts：无论运行是否 COMPLETED 都记录（修正 5 + 可靠性/质量分离）----
    n_cases = len(obs.artifacts.test_cases)
    report.counts = CountsObservation(
        items=len(obs.artifacts.items),
        test_points=len(obs.artifacts.test_points),
        test_cases=n_cases,
        obligations=len(obs.artifacts.obligations),
        strategy_points=sum(1 for tp in obs.artifacts.test_points if tp.provenance == Provenance.STRATEGY),
        llm_points=sum(1 for tp in obs.artifacts.test_points if tp.provenance == Provenance.LLM),
    )
    report.cost_latency = CostLatency(
        total_duration_ms=obs.total_duration_ms,
        total_llm_calls=obs.total_llm_calls,
        per_step=list(obs.steps),
        duration_ms_per_case=round(obs.total_duration_ms / n_cases, 1) if n_cases else None,
        llm_calls_per_case=round(obs.total_llm_calls / n_cases, 2) if n_cases else None,
        llm_failure_steps=[s.step for s in obs.steps if not s.success],
    )

    # ---- REVIEWER-BASED 双轨：Step 7 产物透传（即使质量轨道未开也可保留？非 COMPLETED 无评审意义，仅 COMPLETED 呈现）----
    if obs.artifacts.review_report is not None and report.quality_evaluated:
        rr = obs.artifacts.review_report
        by_prov: dict[str, int] = {}
        for f in rr.findings:
            key = f.provenance.value if hasattr(f.provenance, "value") else str(f.provenance)
            by_prov[key] = by_prov.get(key, 0) + 1
        report.reviewer_based = ReviewerBasedSnapshot(
            report_id=rr.id,
            overall_score=rr.overall_score,
            scores=rr.scores.model_dump(),
            obligation_coverage=rr.obligation_coverage,
            finding_count=len(rr.findings),
            findings_by_provenance=by_prov,
        )

    if not report.quality_evaluated:
        skip = MetricCell(
            value=None, detail=f"非 COMPLETED（{obs.status.value}），不进质量分母（§9.1）；{obs.reliability_note}"
        )
        for name in (
            "requirement_coverage",
            "critical_scenario_coverage",
            "obligation_coverage",
            "structural_validity",
            "duplication_score",
            "requirement_precision",
            "test_point_precision",
            "identity_match_rate",
        ):
            setattr(report, name, MetricCell(**skip.__dict__))
        return report

    # ================== GOLD-BASED 硬指标 ==================
    index = obs.artifacts.to_index()
    rail = associate_gold(obs.gold, index)  # S7 三轨关联（identity > bridge > anchor，裁决 D1/D3）
    mr = rail.matches
    report.requirement_matches = mr.matches
    report.scenario_outcomes = rail.scenario_outcomes
    report.obligation_outcomes = match_obligations(obs.gold, index)
    report.strategy_outcomes = rail.strategy_outcomes
    report.via_counts = dict(rail.via_counts)
    report.identity_diagnostics = rail.diagnostics.to_dict()
    report.anchor_scope = rail.anchor_scope

    # 1) Requirement Coverage（分母 = Gold 项数；分子 = 三轨确定性关联条数，裁决 D1）
    total_gold = len(obs.gold.gold_requirements)
    hits = len(rail.associated_gold_ids)
    report.requirement_coverage = _ratio(hits, total_gold)
    report.requirement_coverage.detail = (
        f"确定性关联 {hits}/{total_gold}；via={rail.via_counts}；identity 四态 {mr.summary()}"
    )

    # 1b) Identity Rail 诊断单元（裁决 D1：与 requirement_coverage 并列，严禁互相替代解读）
    diag = rail.diagnostics
    report.identity_match_rate = MetricCell(
        numerator=diag.auto_hit_identity_only,
        denominator=diag.gold_total or None,
        value=diag.identity_match_rate,
        provenance=MetricProvenance.CODE,
        detail="Identity Rail（fingerprint 精确）仅诊断 parser 身份稳定性；为 0 不代表需求覆盖为 0",
    )

    # 2) Critical Scenario Coverage（COVERED 才计分；AMBIGUOUS/PARTIAL 明细另列）
    state_count: dict[str, int] = {}
    for so in report.scenario_outcomes:
        state_count[so.state.value] = state_count.get(so.state.value, 0) + 1
    report.critical_scenario_coverage = _ratio(
        state_count.get(ScenarioState.COVERED.value, 0), len(report.scenario_outcomes)
    )
    report.critical_scenario_coverage.detail = f"anchor 状态计数 {state_count}"

    # 3) Obligation Coverage（尊重 min_covered_points：仅 COVERED 计分）
    ob_state = {}
    for oo in report.obligation_outcomes:
        ob_state[oo.state.value] = ob_state.get(oo.state.value, 0) + 1
    report.obligation_coverage = _ratio(ob_state.get(ObligationState.COVERED.value, 0), len(report.obligation_outcomes))
    report.obligation_coverage.detail = f"义务状态计数 {ob_state}（含 min_covered_points 判定）"

    # 4) Structural Validity（复用 Step 7 结构层口径）
    struct_score, struct_findings, struct_reason = compute_executability_structural(obs.artifacts.test_cases)
    report.structural_validity = MetricCell(
        numerator=len(obs.artifacts.test_cases) - len({f.target_id for f in struct_findings}),
        denominator=len(obs.artifacts.test_cases) or None,
        value=round(struct_score / 100, 4) if obs.artifacts.test_cases else None,
        detail=struct_reason,
    )

    # 5) Duplication（修正 4：EXACT / SEMANTIC 两类分别呈现）
    dup_score, dup_findings, dup_reason = compute_duplication(obs.artifacts.test_cases)
    for f in dup_findings:
        entry = {
            "target_id": f.target_id,
            "counterpart_id": (f.detail or {}).get("counterpart_id"),
            "similarity": (f.detail or {}).get("similarity"),
            "issue": f.issue,
        }
        if (f.detail or {}).get("duplicate_level") == "exact":
            report.duplication_exact.append(entry)
        else:
            report.duplication_semantic.append(entry)
    report.duplication_score = MetricCell(
        value=round(dup_score / 100, 4) if obs.artifacts.test_cases else None,
        detail=dup_reason,
    )

    # 6) INVALID / PENDING_REVIEW / MISS 三态分列
    report.invalid = _collect_invalid(obs)
    report.pending_review = _collect_pending(obs, mr, tuple(rail.pending))
    report.missing_risk = {
        "gold_miss_ids": [m.gold_id for m in mr.matches if m.state == ItemMatchState.MISS],
        "scenario_open_ids": [so.scenario_id for so in report.scenario_outcomes if so.state != ScenarioState.COVERED],
        "obligation_open_ids": [
            f"{oo.technique.value}:{oo.target}"
            for oo in report.obligation_outcomes
            if oo.state != ObligationState.COVERED
        ],
        "strategy_unmet_ids": [
            f"{st.feature}:{st.technique.value}" for st in report.strategy_outcomes if st.met is False
        ],
        "total_open": 0,
    }
    report.missing_risk["total_open"] = sum(len(v) for k, v in report.missing_risk.items() if k != "total_open")

    # 7) 辅助观察（Precision 不作唯一核心依据；TP precision = trace 到 AUTO_HIT 项的 TP 占比）
    report.requirement_precision = _ratio(len(mr.consumed_item_ids), len(obs.artifacts.items))
    linked_tps = sum(1 for tp in obs.artifacts.test_points if set(tp.item_ids) & mr.consumed_item_ids)
    report.test_point_precision = _ratio(linked_tps, len(obs.artifacts.test_points))
    report.test_point_precision.detail = "代理口径：trace 到 AUTO_HIT 需求项的 TP 占比（观察指标）"
    return report
