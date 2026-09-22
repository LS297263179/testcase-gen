"""V2 Step 11 S7 三轨关联层（架构裁决 D1/D2/D3/D9/D10）。

定位：把"Gold ↔ runtime 产物"的关联从**单一 fingerprint 身份匹配**扩展为三轨，
使质量评价不再被 Step 2 parser 的文本改写与 module 重命名一票否决。

三轨（裁决 5）：
  - **Identity Rail**（诊断）：`matching.match_gold_requirements` 原样复用，fingerprint 精确相等。
    实测两个模型下 statement 逐字率均为 0/10 → 该轨**只用于度量 parser 身份稳定性**，
    严禁把 `identity_match_rate=0` 读作"需求覆盖为 0"（裁决 2/3）。
  - **Semantic Rail · bridge**（核心）：`Gold.obligations_expected.requirement_gold_id`
    → runtime `CoverageObligation` 的 `(technique, target)` 精确匹配 → `obligation.item_id`。
    全链路结构化、零相似度、跨模型稳定（实测两模型同为 5/10 且桥到同一批 item）。
  - **Semantic Rail · anchor**（核心）：`Gold.critical_scenarios` 的 `expected_actions/expected_outcomes`
    只在 **结构化字段** `step.action` / `tc.expected` 内做子串判定（裁决 3；不扫 title/precondition/remark，
    不用 similarity/fuzzy 计分），命中后经 `requirement_gold_ids` 形成明确的 Gold 映射。
  - Structural Rail 仍由 `metrics_hard` 的 structural_validity / duplication 承担，本模块不涉及。

冻结语义：
  - `AUTO_HIT` 按裁决 D1 正式重定义为 **"确定性关联命中"**（不再等同 fingerprint exact），
    每条命中必须带 `via ∈ {identity, bridge, anchor}`；
  - 关联优先级与去重：`identity > bridge > anchor`（裁决 3），同一 Gold 只计一次；
  - CANDIDATE / AMBIGUOUS / MISS 一律**不自动计分**，照旧进 pending_review 由人工裁决；
  - anchor 命中多个 runtime item 时（裁决 D10）：覆盖率**计入**，但 `item_id=None` 不猜、
    保留 `item_ids`、登记 pending、不向 S5 提供错误 trace；
  - anchor 判定的 haystack = **全量非 ARCHIVED TestCase**（裁决 D9，`index.test_cases` 已由 S6
    装配层排除 ARCHIVED），避免重新引入 identity 前置依赖。

边界：本模块**不修改** `matching.py` 的 identity matching 语义，不改 Gold、不改 parser、
不改 Runtime；只读消费 `ArtifactIndex`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.v2.eval.matching import (
    ArtifactIndex,
    ItemMatch,
    ItemMatchState,
    RequirementMatchReport,
    ScenarioOutcome,
    ScenarioState,
    StrategyOutcome,
    match_gold_requirements,
)
from core.v2.eval.schema import BenchmarkGold
from core.v2.eval.textops import anchor_satisfied, statement_similarity
from core.v2.fingerprint import normalize_text

# via 取值（裁决 D1：每个 AUTO_HIT 必须可追溯来源）
VIA_IDENTITY = "identity"
VIA_BRIDGE = "bridge"
VIA_ANCHOR = "anchor"
VIA_VALUES = (VIA_IDENTITY, VIA_BRIDGE, VIA_ANCHOR)

# 追加的 pending kind（与 S3 既有 kind 并列，由 metrics_hard 合并进 pending_review）
PENDING_ANCHOR_ITEM_AMBIGUOUS = "anchor_item_ambiguous"
PENDING_ANCHOR_ITEM_UNRESOLVED = "anchor_item_unresolved"


@dataclass
class RailMatch(ItemMatch):
    """带来源标注的匹配结果。`state=AUTO_HIT` 表示"确定性关联命中"（裁决 D1 重定义）。

    继承 S3 `ItemMatch` 而非新增枚举：`metrics_soft` 只鸭子类型读取 `state`/`item_id`/`gold_id`，
    因此 S5 **零改动**即可消费三轨关联结果。
    """

    via: str = VIA_IDENTITY  # identity | bridge | anchor；非 AUTO_HIT 时为空串


@dataclass
class RailPending:
    """关联层新增的待人工裁决条目（结构对齐 S3 PendingReviewItem，避免循环 import）。"""

    kind: str
    ref_id: str
    detail: str


@dataclass
class IdentityDiagnostics:
    """Identity Rail 诊断（裁决 3：衡量 parser 输出的身份稳定性，不参与质量计分）。"""

    gold_total: int = 0
    auto_hit_identity_only: int = 0
    identity_match_rate: float | None = None  # 分母 0 → None（不伪造 0 分）
    module_consistency: int = 0  # 与"最相似 runtime item"的 module 全等条数
    type_consistency: int = 0
    statement_verbatim: int = 0  # 归一化后逐字相等条数（实测两模型均为 0）
    best_similarity_mean: float | None = None
    runtime_modules: list[str] = field(default_factory=list)
    gold_modules: list[str] = field(default_factory=list)
    identity_miss_gold_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "gold_total": self.gold_total,
            "auto_hit_identity_only": self.auto_hit_identity_only,
            "identity_match_rate": self.identity_match_rate,
            "module_consistency": f"{self.module_consistency}/{self.gold_total}",
            "type_consistency": f"{self.type_consistency}/{self.gold_total}",
            "statement_verbatim": f"{self.statement_verbatim}/{self.gold_total}",
            "best_similarity_mean": self.best_similarity_mean,
            "runtime_modules": list(self.runtime_modules),
            "gold_modules": list(self.gold_modules),
            "identity_miss_gold_ids": list(self.identity_miss_gold_ids),
            "note": "Identity Rail 仅诊断 parser 身份稳定性；identity_match_rate=0 不得解读为需求覆盖为 0（裁决 D1/D3）",
        }


@dataclass
class RailReport:
    """三轨关联结果（`metrics_hard` 的唯一关联入口）。"""

    matches: RequirementMatchReport  # 复用 S3 结构；matches 元素为 RailMatch
    scenario_outcomes: list[ScenarioOutcome] = field(default_factory=list)
    strategy_outcomes: list[StrategyOutcome] = field(default_factory=list)
    diagnostics: IdentityDiagnostics = field(default_factory=IdentityDiagnostics)
    associated_gold_ids: list[str] = field(default_factory=list)  # 覆盖率分子（含 anchor 多义，裁决 D10）
    via_counts: dict[str, int] = field(default_factory=dict)
    pending: list[RailPending] = field(default_factory=list)
    anchor_scope: str = "all_non_archived_testcases"  # 裁决 D9


# ============================================================
# Identity Rail 诊断
# ============================================================


def build_identity_diagnostics(
    gold: BenchmarkGold, index: ArtifactIndex, identity_matches: list[ItemMatch]
) -> IdentityDiagnostics:
    """统计 parser 身份稳定性：module/type 一致数、statement 逐字数、最佳相似度均值。

    ★ 相似度在此**仅用于挑选诊断对照项**（找"最像的那条 runtime item"），不参与任何计分，
      不产生 AUTO_HIT（设计文档 §4.1 规则 3 继续有效）。
    """
    golds = list(gold.gold_requirements)
    diag = IdentityDiagnostics(
        gold_total=len(golds),
        auto_hit_identity_only=sum(1 for m in identity_matches if m.state == ItemMatchState.AUTO_HIT),
        identity_miss_gold_ids=[m.gold_id for m in identity_matches if m.state != ItemMatchState.AUTO_HIT],
        runtime_modules=sorted({it.module for it in index.items}),
        gold_modules=sorted({g.module for g in golds}),
    )
    diag.identity_match_rate = round(diag.auto_hit_identity_only / len(golds), 4) if golds else None
    if not index.items or not golds:
        return diag

    sim_sum = 0.0
    for gr in golds:
        best = max(index.items, key=lambda it: statement_similarity(gr.statement, it.statement))
        sim_sum += statement_similarity(gr.statement, best.statement)
        diag.module_consistency += int(gr.module == best.module)
        diag.type_consistency += int(gr.type.value == best.type.value)
        diag.statement_verbatim += int(normalize_text(gr.statement) == normalize_text(best.statement))
    diag.best_similarity_mean = round(sim_sum / len(golds), 4)
    return diag


# ============================================================
# Semantic Rail · bridge
# ============================================================


def bridge_associations(gold: BenchmarkGold, index: ArtifactIndex) -> dict[str, tuple[str, str]]:
    """obligation 桥接：gold_id → (item_id, 证据串)。只认 `(technique, target)` 精确相等的 runtime 义务。

    这是唯一"零相似度、跨模型稳定"的确定性关联通道：Gold 侧声明 `requirement_gold_id`，
    runtime 侧 `CoverageObligation.item_id` 给出需求项归属。
    """
    out: dict[str, tuple[str, str]] = {}
    for oe in gold.obligations_expected:
        gid = oe.requirement_gold_id
        if not gid:
            continue  # 无 Gold 回链的义务无法形成映射（裁决 3 第 4 条）
        ob = index.obligation_by_target(oe.technique, oe.target)
        if ob is None:
            continue
        evidence = f"obligation_bridge:{oe.technique.value}:{oe.target}"
        if gid not in out:  # 同一 gold 被多条义务支持时保留首条证据（去重，不覆盖）
            out[gid] = (ob.item_id, evidence)
    return out


# ============================================================
# Semantic Rail · anchor
# ============================================================


def _per_tc_anchor_ok(sc, tc) -> bool:
    """单条 TestCase 是否满足该场景声明的 action/outcome 锚点（只看结构化字段，裁决 3）。"""
    if sc.expected_actions and not all(
        anchor_satisfied(a, [step.action for step in tc.steps]) for a in sc.expected_actions
    ):
        return False
    return not sc.expected_outcomes or all(anchor_satisfied(o, [tc.expected]) for o in sc.expected_outcomes)


def _scenario_items(sc, index: ArtifactIndex, associated_items: set[str]) -> set[str]:
    """COVERED 场景 → 反查其证据所在的 runtime item（供 S5 trace；不猜，宁缺勿滥）。

    - 有 action/outcome 锚点：取"逐条满足锚点的 TC" → test_point_ids → TP.item_ids；
    - 仅有 technique 锚点：取该 technique 的 runtime 义务 → obligation.item_id；
    - 两者皆无：回退到该场景引用 Gold 已关联的 item（不会凭空扩大）。
    """
    tp_by_id = {tp.id: tp for tp in index.test_points}
    items: set[str] = set()
    if sc.expected_actions or sc.expected_outcomes:
        for tc in index.test_cases:
            if not _per_tc_anchor_ok(sc, tc):
                continue
            for pid in tc.test_point_ids:
                tp = tp_by_id.get(pid)
                if tp is not None:
                    items |= set(tp.item_ids)
    if not items and sc.expected_techniques:
        wanted = set(sc.expected_techniques)
        items |= {ob.item_id for ob in index.obligations if ob.technique in wanted}
    return items or set(associated_items)


def judge_scenarios(
    gold: BenchmarkGold, index: ArtifactIndex, by_gid: dict[str, RailMatch]
) -> tuple[list[ScenarioOutcome], dict[str, set[str]], list[RailPending]]:
    """场景锚点判定（解耦 identity 前置门）+ COVERED 场景的 Gold 关联。

    与 S3 `match_critical_scenarios` 的差别（均为裁决要求，非随意变更）：
      1. **不再以 identity AUTO_HIT 作为准入门**：引用项未 identity 命中不再直接判 MISSING/AMBIGUOUS，
         而是照常按结构化锚点判定；identity 是否命中降级为 `anchors["requirement_items"]` 诊断位；
      2. 锚点 haystack = 全量非 ARCHIVED TC（裁决 D9），而非"仅追溯到已命中 item 的 TC"；
      3. 未声明任何结构化锚点的场景 → AMBIGUOUS（不可判，进 pending），不强行计分。
    """
    action_fields = [step.action for tc in index.test_cases for step in tc.steps]
    expected_fields = [tc.expected for tc in index.test_cases]
    outcomes: list[ScenarioOutcome] = []
    assoc: dict[str, set[str]] = {}
    pending: list[RailPending] = []

    for sc in gold.critical_scenarios:
        ident_linked = all(
            (m := by_gid.get(gid)) is not None and m.via == VIA_IDENTITY for gid in sc.requirement_gold_ids
        )
        # 该场景引用 Gold 当前已关联到的 item（identity/bridge），用于 technique 锚点与兜底
        linked_items: set[str] = set()
        for gid in sc.requirement_gold_ids:
            m = by_gid.get(gid)
            if m is not None and m.state == ItemMatchState.AUTO_HIT:
                linked_items.add(m.item_id) if m.item_id else linked_items.update(m.item_ids)

        anchors: dict[str, bool] = {}
        tech_scope = ""
        if sc.expected_techniques:
            if linked_items:
                have = index.techniques_for_items(linked_items)
                tech_scope = "traced"
            else:
                have = {tp.technique for tp in index.test_points if tp.technique is not None} | {
                    ob.technique for ob in index.obligations
                }
                tech_scope = "run"
            missing = [t.value for t in sc.expected_techniques if t not in have]
            anchors["techniques"] = not missing
        if sc.expected_actions:
            anchors["actions"] = all(anchor_satisfied(a, action_fields) for a in sc.expected_actions)
        if sc.expected_outcomes:
            anchors["outcomes"] = all(anchor_satisfied(o, expected_fields) for o in sc.expected_outcomes)

        declared = list(anchors.values())
        if not declared:
            outcomes.append(
                ScenarioOutcome(
                    sc.scenario_id,
                    ScenarioState.AMBIGUOUS,
                    {"requirement_items": ident_linked},
                    "未声明任何结构化锚点（techniques/actions/outcomes 全空），不可判 → 人工复核",
                )
            )
            pending.append(RailPending("scenario_unjudgeable", sc.scenario_id, "场景未声明结构化锚点，不计分"))
            continue

        if all(declared):
            state, detail = ScenarioState.COVERED, "全部声明锚点满足"
        elif any(declared):
            state, detail = ScenarioState.PARTIAL, "部分锚点满足"
        else:
            state, detail = ScenarioState.MISSING, "声明锚点全部不满足"
        detail += f"：{anchors}"
        if sc.expected_techniques:
            detail += f"（technique 判定范围={tech_scope}）"
        detail += f"；identity_linked={ident_linked}（仅诊断，不作准入）"
        outcomes.append(ScenarioOutcome(sc.scenario_id, state, {"requirement_items": ident_linked, **anchors}, detail))

        if state != ScenarioState.COVERED:
            continue
        items = _scenario_items(sc, index, linked_items)
        for gid in sc.requirement_gold_ids:
            assoc.setdefault(gid, set()).update(items)
    return outcomes, assoc, pending


# ============================================================
# Semantic Rail · strategy
# ============================================================


def judge_strategy(gold: BenchmarkGold, report: RequirementMatchReport, index: ArtifactIndex) -> list[StrategyOutcome]:
    """条件触发策略判定：与 S3 同口径，但对"anchor 多义关联（item_id=None）"诚实返回不可判。

    S3 版本用 `match_report.auto[gid]`（唯一 item 映射）取产物 technique；
    anchor 多义时该 gold 不在 `auto` 内 → 此处判 `met=None`（不可判），**不伪造 False**。
    """
    outcomes: list[StrategyOutcome] = []
    for se in gold.strategy_expectations:
        gid = se.requirement_gold_id
        if report.state_for(gid) != ItemMatchState.AUTO_HIT:
            outcomes.append(
                StrategyOutcome(
                    se.feature.value,
                    se.technique,
                    gid,
                    None,
                    f"触发特征未确定性关联（{report.state_for(gid).value}），不可判",
                )
            )
            continue
        item_id = report.auto.get(gid)
        if item_id is None:
            outcomes.append(
                StrategyOutcome(se.feature.value, se.technique, gid, None, "anchor 关联无唯一 item，technique 不可判")
            )
            continue
        have = index.techniques_for_items({item_id})
        outcomes.append(
            StrategyOutcome(
                se.feature.value,
                se.technique,
                gid,
                se.technique in have,
                f"产物 technique={sorted(t.value for t in have)}",
            )
        )
    return outcomes


# ============================================================
# 顶层入口
# ============================================================


def associate_gold(gold: BenchmarkGold, index: ArtifactIndex) -> RailReport:
    """三轨关联主入口：identity → bridge → anchor，按优先级去重后产出统一报告。

    返回的 `matches` 与 S3 `RequirementMatchReport` 同构（元素为 `RailMatch`），
    因此 `metrics_hard` 的 precision / pending / consumed 计算无需重写。
    """
    identity_report = match_gold_requirements(gold, index.items)
    by_gid: dict[str, RailMatch] = {}
    for m in identity_report.matches:
        by_gid[m.gold_id] = RailMatch(
            gold_id=m.gold_id,
            state=m.state,
            item_id=m.item_id,
            item_ids=list(m.item_ids),
            similarity=m.similarity,
            basis=m.basis,
            via=VIA_IDENTITY if m.state == ItemMatchState.AUTO_HIT else "",
        )
    diagnostics = build_identity_diagnostics(gold, index, identity_report.matches)

    # ② bridge（identity 优先，已命中者不覆盖）
    for gid, (item_id, evidence) in bridge_associations(gold, index).items():
        cur = by_gid.get(gid)
        if cur is None or cur.state == ItemMatchState.AUTO_HIT:
            continue
        by_gid[gid] = RailMatch(
            gold_id=gid,
            state=ItemMatchState.AUTO_HIT,
            item_id=item_id,
            item_ids=[item_id],
            similarity=cur.similarity,
            basis=evidence,
            via=VIA_BRIDGE,
        )

    # ③ anchor（裁决 D9：全量非 ARCHIVED TC；D10：多义不猜 item_id）
    scenario_outcomes, anchor_assoc, pending = judge_scenarios(gold, index, by_gid)
    for gr in gold.gold_requirements:
        gid = gr.gold_id
        cur = by_gid.get(gid)
        if cur is None or cur.state == ItemMatchState.AUTO_HIT or gid not in anchor_assoc:
            continue
        ids = sorted(anchor_assoc[gid])
        by_gid[gid] = RailMatch(
            gold_id=gid,
            state=ItemMatchState.AUTO_HIT,
            item_id=ids[0] if len(ids) == 1 else None,
            item_ids=ids,
            similarity=cur.similarity,
            basis=f"scenario_anchor:{len(ids)} item(s)",
            via=VIA_ANCHOR,
        )
        if not ids:
            pending.append(
                RailPending(PENDING_ANCHOR_ITEM_UNRESOLVED, gid, "场景锚点满足但无法反查 runtime item，不提供 S5 trace")
            )
        elif len(ids) > 1:
            pending.append(
                RailPending(
                    PENDING_ANCHOR_ITEM_AMBIGUOUS,
                    gid,
                    f"场景锚点满足但反查到 {len(ids)} 个 runtime item，覆盖率计入、item_id 不猜（裁决 D10）",
                )
            )

    # 重建 S3 同构报告（auto/consumed 仅收"唯一 item 映射"，供 precision 与 S5 trace 使用）
    matches = [by_gid[gr.gold_id] for gr in gold.gold_requirements if gr.gold_id in by_gid]
    auto = {m.gold_id: m.item_id for m in matches if m.state == ItemMatchState.AUTO_HIT and m.item_id}
    consumed = set(auto.values())
    report = RequirementMatchReport(
        matches=matches,
        auto=auto,
        consumed_item_ids=consumed,
        unmatched_item_ids=sorted(it.id for it in index.items if it.id not in consumed),
    )
    associated = [m.gold_id for m in matches if m.state == ItemMatchState.AUTO_HIT]
    via_counts = {v: 0 for v in VIA_VALUES}
    for m in matches:
        if m.state == ItemMatchState.AUTO_HIT:
            via_counts[m.via] = via_counts.get(m.via, 0) + 1
        else:
            via_counts[m.state.value] = via_counts.get(m.state.value, 0) + 1
    return RailReport(
        matches=report,
        scenario_outcomes=scenario_outcomes,
        strategy_outcomes=judge_strategy(gold, report, index),
        diagnostics=diagnostics,
        associated_gold_ids=associated,
        via_counts=via_counts,
        pending=pending,
    )


__all__ = [
    "PENDING_ANCHOR_ITEM_AMBIGUOUS",
    "PENDING_ANCHOR_ITEM_UNRESOLVED",
    "VIA_ANCHOR",
    "VIA_BRIDGE",
    "VIA_IDENTITY",
    "IdentityDiagnostics",
    "RailMatch",
    "RailPending",
    "RailReport",
    "associate_gold",
    "bridge_associations",
    "build_identity_diagnostics",
    "judge_scenarios",
    "judge_strategy",
]
