"""S4 Final QA：bc_02 正/负向 Synthetic Sanity（零 LLM / 零 DB / 不碰真实 Runtime）。

目的不是断言业务答案，而是证明 bench-v0.1 的 #02 Gold 能真实触发 S3 的
正向判定（A）与缺失判定（B 需求缺失 / C 场景锚点缺失 / D 义务覆盖不足）。
运行时对象完全由 Gold 文件数据驱动构造。
"""

from pathlib import Path

import pytest

from core.schemas import (
    CoverageObligation,
    Provenance,
    RequirementItem,
    TestCase,
    TestCaseType,
    TestPoint,
    TestStep,
    new_ulid,
)
from core.v2.eval.matching import (
    ItemMatchState,
    ObligationState,
    ScenarioState,
    match_gold_requirements,
)
from core.v2.eval.metrics_hard import EvalArtifacts, RunObservation, evaluate_hard_metrics
from core.v2.eval.schema import BenchmarkGold, BenchmarkRunStatus, load_benchmark_suite

_BENCH = Path(__file__).resolve().parents[1] / "benchmark"
_V = new_ulid()
_R = new_ulid()


@pytest.fixture(scope="module")
def gold() -> BenchmarkGold:
    return load_benchmark_suite(_BENCH).golds["bc_02_refund_order"]


def _ideal_runtime(
    gold: BenchmarkGold,
    *,
    omit_gr: str | None = None,
    drop_outcomes_for_scenario: str | None = None,
    covered_override: dict[str, int] | None = None,
) -> EvalArtifacts:
    """从 Gold 数据构造理想 runtime 产物；三个参数制造 B/C/D 负向情形。"""
    items, tps, tcs, obligations, covered = [], [], [], [], {}
    item_of: dict[str, str] = {}
    for gr in gold.gold_requirements:
        if gr.gold_id == omit_gr:
            continue
        it = RequirementItem(
            version_id=_V,
            seq=len(items) + 1,
            type=gr.type,
            module=gr.module,
            statement=gr.statement,
            fingerprint=gr.match_keys.identity_fingerprints[0],
        )
        items.append(it)
        item_of[gr.gold_id] = it.id

    def _add_tp(item_ids, title, **kw):
        tp = TestPoint(
            item_ids=list(item_ids),
            module="退款",
            subcategory="synthetic",
            title=title,
            description="d",
            dimension="functional",
            **kw,
        )
        tps.append(tp)
        return tp

    # 1) 每个场景一条 TC：steps 承载 expected_actions，expected 承载 expected_outcomes（均由 Gold 决定）
    for sc in gold.critical_scenarios:
        ref = [item_of[g] for g in sc.requirement_gold_ids if g in item_of]
        if not ref:
            continue
        outcomes = [] if sc.scenario_id == drop_outcomes_for_scenario else list(sc.expected_outcomes)
        steps = [TestStep(seq=i + 1, action=a) for i, a in enumerate(sc.expected_actions)] or [
            TestStep(seq=1, action="执行相关操作")
        ]
        tp = _add_tp(ref, f"tp-{sc.scenario_id}")
        tcs.append(
            TestCase(
                run_id=_R,
                display_id=f"TC_{len(tcs) + 1:03d}",
                test_point_ids=[tp.id],
                module="退款",
                title=f"tc-{sc.scenario_id}",
                steps=steps,
                expected="；".join(outcomes) if outcomes else "操作结果正常",
                type=TestCaseType.FUNCTIONAL,
            )
        )
        for tech in sc.expected_techniques:
            _add_tp(ref, f"tp-tech-{sc.scenario_id}", technique=tech)

    # 2) 每条期望义务 + 覆盖点（数量 = min_covered_points，可被 covered_override 削弱）
    for oe in gold.obligations_expected:
        tgt = item_of.get(oe.requirement_gold_id)
        if tgt is None:
            continue
        ob = CoverageObligation(run_id=_R, item_id=tgt, technique=oe.technique, target=oe.target, description="d")
        obligations.append(ob)
        n = (covered_override or {}).get(oe.target, oe.min_covered_points)
        tp_ids = []
        for k in range(n):
            tp = _add_tp(
                [tgt],
                f"tp-{oe.target}-{k}",
                technique=oe.technique,
                obligation_id=ob.id,
                provenance=Provenance.STRATEGY,
                generation_scope="strategy",
            )
            tp_ids.append(tp.id)
        covered[ob.id] = tp_ids

    return EvalArtifacts(
        items=items,
        test_points=tps,
        test_cases=tcs,
        obligations=obligations,
        obligation_covered_points=covered,
    )


def _evaluate(gold: BenchmarkGold, artifacts: EvalArtifacts):
    obs = RunObservation(
        case_id=gold.case_id,
        gold=gold,
        status=BenchmarkRunStatus.COMPLETED,
        run_id=_R,
        artifacts=artifacts,
    )
    return evaluate_hard_metrics(obs)


class TestSyntheticSanity:
    def test_A_ideal_runtime_all_green(self, gold):
        """A：完整 Gold + 完整 runtime → 需求/场景/义务 100%，策略全 met"""
        report = _evaluate(gold, _ideal_runtime(gold))
        assert report.quality_evaluated is True
        assert report.requirement_coverage.value == 1.0
        assert report.critical_scenario_coverage.value == 1.0
        assert report.obligation_coverage.value == 1.0
        assert gold.strategy_expectations and all(st.met is True for st in report.strategy_outcomes)
        assert report.missing_risk["total_open"] == 0
        assert report.invalid == []
        assert report.pending_review == []

    def test_B_missing_item_breaks_auto_hit(self, gold):
        """B：删掉一条 runtime item → 对应 Gold 不再 AUTO_HIT，Coverage 下降，关联场景非 COVERED"""
        artifacts = _ideal_runtime(gold, omit_gr="gr-amount-upper")
        mr = match_gold_requirements(gold, artifacts.items)
        assert mr.state_for("gr-amount-upper") != ItemMatchState.AUTO_HIT
        report = _evaluate(gold, artifacts)
        n = len(gold.gold_requirements)
        assert report.requirement_coverage.value == round((n - 1) / n, 4)
        by_id = {so.scenario_id: so.state for so in report.scenario_outcomes}
        assert by_id["cs-over-amount-rejected"] != ScenarioState.COVERED

    def test_C_missing_anchor_breaks_scenario(self, gold):
        """C：故意去掉一个场景的 outcome 锚点 → 该场景非 COVERED，其余不受影响"""
        artifacts = _ideal_runtime(gold, drop_outcomes_for_scenario="cs-time-window")
        report = _evaluate(gold, artifacts)
        by_id = {so.scenario_id: so.state for so in report.scenario_outcomes}
        assert by_id["cs-time-window"] != ScenarioState.COVERED
        assert report.critical_scenario_coverage.value < 1.0
        assert "cs-time-window" in report.missing_risk["scenario_open_ids"]

    def test_D_under_covered_obligation(self, gold):
        """D：金额边界义务只给 1 个覆盖点（min=3）→ UNDER_COVERED，义务 Coverage < 1"""
        artifacts = _ideal_runtime(gold, covered_override={"refund_amount": 1})
        report = _evaluate(gold, artifacts)
        amount = next(oo for oo in report.obligation_outcomes if oo.target == "refund_amount")
        assert amount.state == ObligationState.UNDER_COVERED
        assert amount.covered_points == 1 and amount.required_points == 3
        assert report.obligation_coverage.value < 1.0
        assert "boundary_value:refund_amount" in report.missing_risk["obligation_open_ids"]


def _formal_gold_map():
    suite = load_benchmark_suite(_BENCH)
    return {cid: g for cid, g in suite.golds.items() if suite.cases[cid].benchmark_version == "bench-v0.1"}


class TestGenericSyntheticContract:
    """通用接线验证：任一正式 Gold + 由其自身生成的理想 runtime → S3 必须给出满分契约。

    非业务断言（runtime 由 Gold 派生），目的是证明每个正式 Gold 的 match_keys /
    scenario anchor / obligation target / strategy 触发链在 S3 中全部可消费、无结构性死路。
    """

    @pytest.mark.parametrize("cid", sorted(_formal_gold_map()))
    def test_ideal_runtime_full_contract(self, cid):
        gold = _formal_gold_map()[cid]
        report = _evaluate(gold, _ideal_runtime(gold))
        assert report.quality_evaluated is True
        assert report.requirement_coverage.value == 1.0, f"{cid}: AUTO_HIT 链断裂"
        assert all(m.state == ItemMatchState.AUTO_HIT for m in report.requirement_matches)
        if gold.critical_scenarios:
            assert report.critical_scenario_coverage.value == 1.0, f"{cid}: scenario anchor 链断裂"
        if gold.obligations_expected:
            assert report.obligation_coverage.value == 1.0, f"{cid}: obligation target 链断裂"
        if gold.strategy_expectations:
            assert all(st.met is True for st in report.strategy_outcomes), f"{cid}: strategy 触发链断裂"
        assert report.invalid == [], f"{cid}: 理想产物不应产生 INVALID"
        assert report.pending_review == [], f"{cid}: 理想产物不应产生 PENDING_REVIEW"
        assert report.missing_risk["total_open"] == 0
