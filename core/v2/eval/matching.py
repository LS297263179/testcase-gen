"""V2 Step 11 S3 匹配层 - Gold ↔ V2 产物的确定性匹配（纯代码、零 LLM、零 DB 写入）。

匹配协议冻结（docs/v2/step11-benchmark-evaluation.md §4/§5/§8 + S3 修正 2）：
  - AUTO_HIT 只来自 identity fingerprint，优先级：
      Gold 显式 match_keys.identity_fingerprints（权威源）
      > statement 重算（仅无 match_keys 时的兼容 fallback，不得覆盖显式 match_keys）
      > similarity（只产生 CANDIDATE/辅助诊断，任何相似度都不自动计分）
  - CANDIDATE / AMBIGUOUS → pending_review，不自动计分；
  - TestPoint / TestCase 不使用文本相似度判 Gold，
    依赖 obligation / traceability 结构化关系（item_ids / obligation_id / test_point_ids）；
  - Critical Scenario 按结构化 anchor 判定（step.action / tc.expected 结构字段），
    不做全文关键词命中；无法可靠判断 → AMBIGUOUS 进人工复核。

相似度实现复用 core/v2/change_impact._similarity（normalize_text + difflib），不造第二套。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.schemas import CoverageObligation, RequirementItem, Technique, TestCase, TestPoint
from core.v2.change_impact import _similarity  # 复用 Step 6 相似度口径（只读；normalize+difflib）
from core.v2.eval.schema import (
    BenchmarkGold,
    StrategyExpectation,
)
from core.v2.fingerprint import compute_item_identity_fingerprint, normalize_text

# CANDIDATE 提示下限：低于 Step 6 MODIFIED 判定阈值（0.6），仅用于"值得人工看一眼"的候选。
# 注意：达到任何相似度都只产生 CANDIDATE，绝不产生 AUTO_HIT（冻结）。
CANDIDATE_SIMILARITY_FLOOR = 0.35
# 最优候选并列容差：并列高分 → 无法可靠归属 → AMBIGUOUS
AMBIGUOUS_TIE_TOLERANCE = 0.05


class ItemMatchState(StrEnum):
    """RequirementItem 四态匹配（设计文档 §4.1；CONFLICT 并入 AMBIGUOUS）。"""

    AUTO_HIT = "auto_hit"  # fingerprint 精确命中（计分的唯一来源）
    CANDIDATE = "candidate"  # 相似度候选：不计分，进 pending_review
    MISS = "miss"  # 无任何对应：计缺失
    AMBIGUOUS = "ambiguous"  # 一对多/多对一歧义：不自动计分，进 pending_review


class ScenarioState(StrEnum):
    """Critical Scenario 结构化 anchor 判定结果（设计文档 §8）。"""

    COVERED = "covered"  # 核心 requirement anchor + 全部声明的 sub-anchor 满足
    PARTIAL = "partial"  # requirement anchor 命中，部分 sub-anchor 满足
    MISSING = "missing"  # requirement anchor 缺失或 sub-anchor 全部不满足
    AMBIGUOUS = "ambiguous"  # 引用项为 CANDIDATE/AMBIGUOUS，无法可靠判断 → 人工复核


class ObligationState(StrEnum):
    """期望义务判定结果：覆盖数按 Gold min_covered_points 判定（S3 修正）。"""

    COVERED = "covered"  # 义务存在且覆盖点数 ≥ min_covered_points
    UNDER_COVERED = "under_covered"  # 义务存在但覆盖点数不足
    MISSING = "missing"  # 产物中不存在该 technique × target 义务


def _en(value: object) -> str:
    """枚举或字符串 → 字符串值（与 change_impact._en 同语义，一行工具不整段复制）。"""
    return value.value if hasattr(value, "value") else str(value)


def _gold_fp(gr: object) -> str:
    """GoldRequirement → 重算 ri_ 指纹（fallback 专用；有 match_keys 时不走这里）。"""
    return compute_item_identity_fingerprint(module=gr.module, type=_en(gr.type), statement=gr.statement)


def _runtime_fp(item: RequirementItem) -> str:
    """Runtime item 的 identity fingerprint（缺失则按同一公式兜底计算）。"""
    return item.fingerprint or compute_item_identity_fingerprint(
        module=item.module, type=_en(item.type), statement=item.statement
    )


# ============================================================
# RequirementItem 匹配
# ============================================================


@dataclass
class ItemMatch:
    """单条 GoldRequirement 的匹配结果（四态之一）。"""

    gold_id: str
    state: ItemMatchState
    item_id: str | None = None  # AUTO_HIT 唯一命中的 runtime item id
    item_ids: list[str] = field(default_factory=list)  # CANDIDATE/AMBIGUOUS 的候选集
    similarity: float | None = None  # 仅诊断用途（CANDIDATE/AMBIGUOUS），不参与计分
    basis: str = ""  # 判定依据说明（可解释性）


@dataclass
class RequirementMatchReport:
    """整批 GoldRequirement 的匹配报告。"""

    matches: list[ItemMatch] = field(default_factory=list)
    auto: dict[str, str] = field(default_factory=dict)  # gold_id → item_id（仅 AUTO_HIT）
    consumed_item_ids: set[str] = field(default_factory=set)  # 被 AUTO_HIT 占用的 item id
    unmatched_item_ids: list[str] = field(default_factory=list)  # 未被任何 AUTO_HIT 占用的 runtime items

    def state_for(self, gold_id: str) -> ItemMatchState:
        """按 gold_id 取匹配状态（未登记视为 MISS，防御性）。"""
        for m in self.matches:
            if m.gold_id == gold_id:
                return m.state
        return ItemMatchState.MISS

    def summary(self) -> dict[str, int]:
        """四态计数（Gold MISS 独立统计，不与 INVALID / PENDING_REVIEW 混算）。"""
        counts = {s.value: 0 for s in ItemMatchState}
        for m in self.matches:
            counts[m.state.value] += 1
        return counts


def match_gold_requirements(
    gold: BenchmarkGold,
    items: list[RequirementItem],
) -> RequirementMatchReport:
    """GoldRequirement ↔ RequirementItem 四态匹配（确定性，冻结优先级见模块 docstring）。

    流程：
      1. runtime items 按 ri_ fingerprint 建索引；
      2. 有 match_keys → 只用显式 identity_fingerprints 查索引（重算不得覆盖）；
         无 match_keys → 用 statement 重算指纹作兼容 fallback；
      3. 指纹命中 1 个 → AUTO_HIT；命中多个或被多个 Gold 争抢同一 item → AMBIGUOUS(CONFLICT)；
      4. 未命中 → 对未占用 items 求相似度：最优 ≥ CANDIDATE_SIMILARITY_FLOOR → CANDIDATE
         （并列高分超容差 → AMBIGUOUS）；否则 MISS。similarity 永不自动计分。
    """
    fp_index: dict[str, list[RequirementItem]] = {}
    for it in items:
        fp_index.setdefault(_runtime_fp(it), []).append(it)

    # 第一遍：收集每条 Gold 的指纹命中（不判状态，先解决 CONFLICT）
    hits_by_gold: dict[str, tuple[list[RequirementItem], str]] = {}
    for gr in gold.gold_requirements:
        if gr.match_keys is not None:
            fps = list(gr.match_keys.identity_fingerprints)
            basis = "match_keys.identity_fingerprints（Gold 冻结权威源）"
        else:
            fps = [_gold_fp(gr)]
            basis = "statement 重算指纹（无 match_keys 的兼容 fallback）"
        hits: list[RequirementItem] = []
        seen: set[str] = set()
        for fp in fps:
            for it in fp_index.get(fp, []):
                if it.id not in seen:
                    seen.add(it.id)
                    hits.append(it)
        hits_by_gold[gr.gold_id] = (hits, basis)

    # 争抢检测：同一 runtime item 被多条 Gold 的指纹命中 → 相关 Gold 全部 AMBIGUOUS
    claimants: dict[str, list[str]] = {}
    for gid, (hits, _basis) in hits_by_gold.items():
        for it in hits:
            claimants.setdefault(it.id, []).append(gid)

    report = RequirementMatchReport()
    for gr in gold.gold_requirements:
        hits, basis = hits_by_gold[gr.gold_id]
        if len(hits) > 1:
            report.matches.append(
                ItemMatch(
                    gr.gold_id,
                    ItemMatchState.AMBIGUOUS,
                    item_ids=[h.id for h in hits],
                    basis=f"{basis}：同一指纹命中多个 item",
                )
            )
            continue
        if len(hits) == 1:
            if len(claimants[hits[0].id]) > 1:
                rivals = [g for g, (h, _b) in hits_by_gold.items() if any(x.id == hits[0].id for x in h)]
                report.matches.append(
                    ItemMatch(
                        gr.gold_id,
                        ItemMatchState.AMBIGUOUS,
                        item_ids=[hits[0].id],
                        basis=f"{basis}：item 被多条 Gold 争抢（CONFLICT: {rivals}）",
                    )
                )
                continue
            report.matches.append(
                ItemMatch(gr.gold_id, ItemMatchState.AUTO_HIT, item_id=hits[0].id, item_ids=[hits[0].id], basis=basis)
            )
            report.auto[gr.gold_id] = hits[0].id
            report.consumed_item_ids.add(hits[0].id)
            continue
        # 指纹未命中 → 相似度仅产 CANDIDATE
        sims = [(_similarity(gr.statement, it.statement), it) for it in items if it.id not in report.consumed_item_ids]
        best = max((r for r, _it in sims), default=0.0)
        if best >= CANDIDATE_SIMILARITY_FLOOR:
            top_ids = [it.id for r, it in sims if r >= best - AMBIGUOUS_TIE_TOLERANCE]
            if len(top_ids) > 1:
                report.matches.append(
                    ItemMatch(
                        gr.gold_id,
                        ItemMatchState.AMBIGUOUS,
                        item_ids=top_ids,
                        similarity=round(best, 4),
                        basis=f"指纹未命中；相似度并列高分（{round(best, 4)}）无法可靠归属",
                    )
                )
            else:
                report.matches.append(
                    ItemMatch(
                        gr.gold_id,
                        ItemMatchState.CANDIDATE,
                        item_ids=top_ids,
                        similarity=round(best, 4),
                        basis=f"指纹未命中；similarity={round(best, 4)} 仅为候选，不计分",
                    )
                )
        else:
            report.matches.append(
                ItemMatch(
                    gr.gold_id,
                    ItemMatchState.MISS,
                    similarity=round(best, 4) if sims else None,
                    basis="指纹与相似度均无对应",
                )
            )

    report.unmatched_item_ids = sorted(it.id for it in items if it.id not in report.consumed_item_ids)
    return report


# ============================================================
# 产物结构化索引（TestPoint / TestCase / Obligation 关系）
# ============================================================


@dataclass
class ArtifactIndex:
    """V2 产物的只读结构化索引（traceability / obligation 关系，非文本相似度）。"""

    items: list[RequirementItem] = field(default_factory=list)
    test_points: list[TestPoint] = field(default_factory=list)
    test_cases: list[TestCase] = field(default_factory=list)
    obligations: list[CoverageObligation] = field(default_factory=list)
    obligation_covered_points: dict[str, list[str]] = field(
        default_factory=dict
    )  # obligation_id → TP ids（obligation_coverage 表口径）

    def __post_init__(self) -> None:
        self._tps_by_item: dict[str, set[str]] = {}
        for tp in self.test_points:
            for item_id in tp.item_ids:
                self._tps_by_item.setdefault(item_id, set()).add(tp.id)
        self._tp_by_id = {tp.id: tp for tp in self.test_points}
        self._ob_by_id = {ob.id: ob for ob in self.obligations}

    def test_point_ids_for_items(self, item_ids: set[str]) -> set[str]:
        """trace 到指定需求项的 TestPoint id 集合（结构化关系，冻结 §4.1 规则 5）。"""
        out: set[str] = set()
        for item_id in item_ids:
            out |= self._tps_by_item.get(item_id, set())
        return out

    def techniques_for_items(self, item_ids: set[str]) -> set[Technique]:
        """指定需求项产物中出现的 technique：TP.technique ∪ 其 obligations.technique。"""
        techniques: set[Technique] = set()
        for tp_id in self.test_point_ids_for_items(item_ids):
            tp = self._tp_by_id.get(tp_id)
            if tp is not None and tp.technique is not None:
                techniques.add(tp.technique)
        for ob in self.obligations:
            if ob.item_id in item_ids:
                techniques.add(ob.technique)
        return techniques

    def test_cases_for_points(self, tp_ids: set[str]) -> list[TestCase]:
        """经 test_point_ids 追溯到指定 TestPoints 的 TestCase 列表（稳定顺序）。"""
        return [tc for tc in self.test_cases if set(tc.test_point_ids) & tp_ids]

    def covered_point_ids(self, obligation_id: str) -> set[str]:
        """义务的实际覆盖点数：TP.obligation_id 回链 ∪ obligation_coverage 关系表。"""
        out = {tp.id for tp in self.test_points if tp.obligation_id == obligation_id}
        out |= set(self.obligation_covered_points.get(obligation_id, []))
        return out

    def obligation_by_target(self, technique: Technique, target: str) -> CoverageObligation | None:
        """按 (technique, target) 精确定位 runtime obligation（首个匹配；target 全等，不做模糊匹配）。"""
        for ob in self.obligations:
            if ob.technique == technique and ob.target == target:
                return ob
        return None


def _anchor_satisfied(anchor: str, haystacks: list[str]) -> bool:
    """结构锚点判定：归一化后必须是某个**结构化字段**（step.action / tc.expected）的组成部分。

    只在结构化字段内比对，不扫描自由全文（设计文档 §8：禁止关键词撞到即覆盖）。
    """
    needle = normalize_text(anchor)
    if not needle:
        return False
    return any(needle in normalize_text(text) for text in haystacks if text)


@dataclass
class ScenarioOutcome:
    """单条 CriticalScenario 的 anchor 判定结果（逐 anchor 呈现，可解释）。"""

    scenario_id: str
    state: ScenarioState
    anchors: dict[str, bool] = field(default_factory=dict)  # 声明的 anchor → 是否满足
    detail: str = ""


def match_critical_scenarios(
    gold: BenchmarkGold,
    match_report: RequirementMatchReport,
    index: ArtifactIndex,
) -> list[ScenarioOutcome]:
    """Critical Scenario 结构化 anchor 判定（设计文档 §8，指标 #1）。

    核心锚点 = 引用的 GoldRequirement 全部 AUTO_HIT；
    其后再按声明的 expected_techniques / expected_actions / expected_outcomes 逐项判定。
    引用项为 CANDIDATE/AMBIGUOUS → 场景 AMBIGUOUS（人工复核，不强行计分）。
    """
    outcomes: list[ScenarioOutcome] = []
    for sc in gold.critical_scenarios:
        states = {gid: match_report.state_for(gid) for gid in sc.requirement_gold_ids}
        unresolved = {
            gid: st for gid, st in states.items() if st in (ItemMatchState.CANDIDATE, ItemMatchState.AMBIGUOUS)
        }
        missed = [gid for gid, st in states.items() if st == ItemMatchState.MISS]

        if missed:
            outcomes.append(
                ScenarioOutcome(
                    sc.scenario_id, ScenarioState.MISSING, {"requirement_items": False}, f"核心锚点缺失: {missed}"
                )
            )
            continue
        if unresolved:
            outcomes.append(
                ScenarioOutcome(
                    sc.scenario_id,
                    ScenarioState.AMBIGUOUS,
                    {"requirement_items": False},
                    f"引用项未确定性命中（pending_review）: {unresolved}",
                )
            )
            continue

        item_ids = {match_report.auto[gid] for gid in sc.requirement_gold_ids}
        anchors: dict[str, bool] = {"requirement_items": True}
        tp_ids = index.test_point_ids_for_items(item_ids)
        tcs = index.test_cases_for_points(tp_ids)

        if sc.expected_techniques:
            have = index.techniques_for_items(item_ids)
            missing_tech = [t.value for t in sc.expected_techniques if t not in have]
            anchors["techniques"] = not missing_tech
        if sc.expected_actions:
            action_fields = [step.action for tc in tcs for step in tc.steps]
            anchors["actions"] = all(_anchor_satisfied(a, action_fields) for a in sc.expected_actions)
        if sc.expected_outcomes:
            expected_fields = [tc.expected for tc in tcs]
            anchors["outcomes"] = all(_anchor_satisfied(a, expected_fields) for a in sc.expected_outcomes)

        sub = [v for k, v in anchors.items() if k != "requirement_items"]
        if all(anchors.values()):
            state = ScenarioState.COVERED
            detail = "全部结构化锚点满足"
        elif any(sub):
            state = ScenarioState.PARTIAL
            detail = "部分锚点满足：" + str(anchors)
        else:
            state = ScenarioState.MISSING
            detail = "核心锚点外的结构锚点全部不满足：" + str(anchors)
        outcomes.append(ScenarioOutcome(sc.scenario_id, state, anchors, detail))
    return outcomes


@dataclass
class ObligationOutcome:
    """单条 ObligationExpected 的判定结果（含 min_covered_points 尊重逻辑）。"""

    target: str
    technique: Technique
    state: ObligationState
    covered_points: int = 0
    required_points: int = 1
    obligation_id: str | None = None
    detail: str = ""


def match_obligations(gold: BenchmarkGold, index: ArtifactIndex) -> list[ObligationOutcome]:
    """期望义务判定：存在性（technique × target 精确）+ 覆盖点数 ≥ min_covered_points。"""
    outcomes: list[ObligationOutcome] = []
    for oe in gold.obligations_expected:
        ob = index.obligation_by_target(oe.technique, oe.target)
        if ob is None:
            outcomes.append(
                ObligationOutcome(
                    oe.target,
                    oe.technique,
                    ObligationState.MISSING,
                    0,
                    oe.min_covered_points,
                    detail="产物中不存在该 technique × target 义务",
                )
            )
            continue
        covered = len(index.covered_point_ids(ob.id))
        state = ObligationState.COVERED if covered >= oe.min_covered_points else ObligationState.UNDER_COVERED
        outcomes.append(
            ObligationOutcome(
                oe.target,
                oe.technique,
                state,
                covered,
                oe.min_covered_points,
                ob.id,
                f"覆盖点数 {covered}/{oe.min_covered_points}",
            )
        )
    return outcomes


@dataclass
class StrategyOutcome:
    """单条 StrategyExpectation 的条件触发判定（met=None 表示触发特征未确定性命中，不可判）。"""

    feature: str
    technique: Technique
    requirement_gold_id: str
    met: bool | None
    detail: str = ""


def check_strategy_expectations(
    gold: BenchmarkGold,
    match_report: RequirementMatchReport,
    index: ArtifactIndex,
) -> list[StrategyOutcome]:
    """条件触发策略（设计文档 §5）：命中需求项的产物中出现对应 technique → met=True。

    触发条件本身未 AUTO_HIT（MISS/CANDIDATE/AMBIGUOUS）→ met=None（不可判，不算错，
    该信息汇入 missing/pending 视图，不伪装成确定性结论）。
    """
    outcomes: list[StrategyOutcome] = []
    se: StrategyExpectation
    for se in gold.strategy_expectations:
        state = match_report.state_for(se.requirement_gold_id)
        if state != ItemMatchState.AUTO_HIT:
            outcomes.append(
                StrategyOutcome(
                    se.feature.value,
                    se.technique,
                    se.requirement_gold_id,
                    None,
                    f"触发特征未确定性命中（{state.value}），不可判",
                )
            )
            continue
        item_ids = {match_report.auto[se.requirement_gold_id]}
        have = index.techniques_for_items(item_ids)
        met = se.technique in have
        outcomes.append(
            StrategyOutcome(
                se.feature.value,
                se.technique,
                se.requirement_gold_id,
                met,
                f"产物 technique={sorted(t.value for t in have)}",
            )
        )
    return outcomes
