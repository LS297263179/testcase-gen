"""Step 11 S3：确定性匹配层 + Hard Metrics 单测（合成数据，零 LLM / 零 DB / 零 Runtime）。

覆盖：四态 Matching 优先级（match_keys > recomputed fallback > similarity 不计分）、
Critical Scenario 结构化 anchor（禁止关键词误判）、Obligation min_covered_points、
Strategy 条件触发、INVALID / PENDING_REVIEW / Gold MISS 分开统计、
不自动判 ADDITIONAL_VALID、Duplication EXACT/SEMANTIC 分别呈现、
Cost/Latency 归一化、非 COMPLETED 不进质量分母、REVIEWER-BASED 透传。
"""

from types import SimpleNamespace

from core.schemas import (
    CoverageObligation,
    Provenance,
    RequirementItem,
    RequirementItemType,
    ReviewFinding,
    ReviewReport,
    ReviewScores,
    ReviewTriggerType,
    Severity,
    TargetType,
    Technique,
    TestCase,
    TestCaseType,
    TestPoint,
    TestStep,
    new_ulid,
)
from core.v2.eval.matching import (
    ArtifactIndex,
    ItemMatchState,
    ObligationState,
    ScenarioState,
    check_strategy_expectations,
    match_critical_scenarios,
    match_gold_requirements,
    match_obligations,
)
from core.v2.eval.metrics_hard import (
    EvalArtifacts,
    RunObservation,
    StepTiming,
    evaluate_hard_metrics,
)
from core.v2.eval.schema import BenchmarkGold, BenchmarkRunStatus, GoldAuthoring, MetricProvenance
from core.v2.fingerprint import compute_item_identity_fingerprint

_V = new_ulid()  # 合成 version_id（不落库）
_RUN = new_ulid()  # 合成 run_id
_ZERO_FP = "ri_" + "0" * 32


def _fp(statement: str, module: str = "登录", type_: str = "data_field") -> str:
    return compute_item_identity_fingerprint(module=module, type=type_, statement=statement)


def _item(
    statement: str, module: str = "登录", type_: RequirementItemType = RequirementItemType.DATA_FIELD
) -> RequirementItem:
    """runtime RequirementItem（fingerprint 按真实公式计算，模拟 Repository 兜底）"""
    return RequirementItem(
        version_id=_V,
        seq=1,
        type=type_,
        module=module,
        statement=statement,
        fingerprint=_fp(statement, module, type_.value),
    )


def _tp(item_ids, *, technique=None, obligation_id=None, provenance=Provenance.LLM, title="测试点") -> TestPoint:
    return TestPoint(
        item_ids=list(item_ids),
        module="登录",
        subcategory="边界",
        title=title,
        description="d",
        dimension="boundary" if technique == Technique.BOUNDARY_VALUE else "functional",
        technique=technique,
        obligation_id=obligation_id,
        provenance=provenance,
    )


def _tc(
    tp_ids,
    *,
    display_id="TC_001",
    title="用例",
    action="打开登录页并输入手机号",
    expected="提示格式错误",
    content_hash=None,
    validation_errors=None,
) -> TestCase:
    return TestCase(
        run_id=_RUN,
        display_id=display_id,
        test_point_ids=list(tp_ids),
        module="登录",
        title=title,
        steps=[TestStep(seq=1, action=action)],
        expected=expected,
        type=TestCaseType.FUNCTIONAL,
        content_hash=content_hash,
        validation_errors=validation_errors or [],
    )


def _ob(item_id, *, technique=Technique.BOUNDARY_VALUE, target="phone.length") -> CoverageObligation:
    return CoverageObligation(run_id=_RUN, item_id=item_id, technique=technique, target=target, description="d")


def _gr_dict(gold_id, statement, *, module="登录", type_="data_field", match_keys=None) -> dict:
    d = {"gold_id": gold_id, "module": module, "type": type_, "statement": statement}
    if match_keys is not None:
        d["match_keys"] = match_keys
    return d


def _gold(
    *,
    grs=None,
    scenarios=None,
    obligations=None,
    strategies=None,
    authoring=GoldAuthoring.HUMAN_INDEPENDENT,
) -> BenchmarkGold:
    return BenchmarkGold.model_validate(
        {
            "gold_version": "gold-v1",
            "case_id": "bc_t1",
            "authoring_note": "unit-test",
            "gold_authoring": authoring.value,
            "gold_requirements": grs if grs is not None else [_gr_dict("gr-1", "手机号必须为 11 位数字")],
            "critical_scenarios": scenarios or [],
            "obligations_expected": obligations or [],
            "strategy_expectations": strategies or [],
            "reference_cases": [],
            "acceptable_variants": [],
            "forbidden_patterns": [],
        }
    )


def _obs(artifacts: EvalArtifacts, *, gold=None, status=BenchmarkRunStatus.COMPLETED, **kw) -> RunObservation:
    return RunObservation(
        case_id="bc_t1",
        gold=gold or _gold(),
        status=status,
        run_id=_RUN,
        artifacts=artifacts,
        **kw,
    )


# 与 _gold 默认 gr-1 配对的合法 runtime item
_HIT_STATEMENT = "手机号必须为 11 位数字"


# ============================================================
# 四态 Matching（修正 2 优先级）
# ============================================================


class TestRequirementMatching:
    def test_recomputed_fallback_auto_hit(self):
        gold = _gold()
        item = _item(_HIT_STATEMENT)
        report = match_gold_requirements(gold, [item])
        assert report.state_for("gr-1") == ItemMatchState.AUTO_HIT
        assert report.auto["gr-1"] == item.id
        assert "fallback" in report.matches[0].basis

    def test_explicit_match_keys_is_authoritative(self):
        """match_keys 冻结指纹优先：statement 指向 item1，权威指纹指向 item2 → 命中 item2"""
        item1 = _item(_HIT_STATEMENT)
        item2 = _item("手机号需为11位")
        gold = _gold(grs=[_gr_dict("gr-1", _HIT_STATEMENT, match_keys={"identity_fingerprints": [item2.fingerprint]})])
        report = match_gold_requirements(gold, [item1, item2])
        assert report.state_for("gr-1") == ItemMatchState.AUTO_HIT
        assert report.auto["gr-1"] == item2.id

    def test_explicit_match_keys_blocks_recompute(self):
        """重算不得覆盖显式 match_keys：match_keys 指向不存在的指纹，即使 statement 可重算命中 → 非 AUTO_HIT"""
        item = _item(_HIT_STATEMENT)
        gold = _gold(grs=[_gr_dict("gr-1", _HIT_STATEMENT, match_keys={"identity_fingerprints": [_ZERO_FP]})])
        report = match_gold_requirements(gold, [item])
        # similarity=1.0 也只产生 CANDIDATE（similarity 不得自动计分，冻结）
        assert report.state_for("gr-1") == ItemMatchState.CANDIDATE
        assert report.matches[0].similarity == 1.0
        assert not report.auto

    def test_multiple_items_same_fingerprint_ambiguous(self):
        # 同 statement 同公式 → 同指纹；fp_index 命中 2 个不同 id → 一对多歧义
        it1 = _item(_HIT_STATEMENT)
        it2 = _item(_HIT_STATEMENT)
        report = match_gold_requirements(_gold(), [it1, it2])
        assert report.state_for("gr-1") == ItemMatchState.AMBIGUOUS

    def test_conflict_multi_gold_claim_same_item(self):
        item = _item(_HIT_STATEMENT)
        gold = _gold(
            grs=[_gr_dict("gr-1", _HIT_STATEMENT), _gr_dict("gr-2", _HIT_STATEMENT)],
            scenarios=[],
        )
        # acceptable 引用一致性无关；两条 Gold 争抢同一 item → CONFLICT → 双 AMBIGUOUS
        report = match_gold_requirements(gold, [item])
        assert report.state_for("gr-1") == ItemMatchState.AMBIGUOUS
        assert report.state_for("gr-2") == ItemMatchState.AMBIGUOUS
        assert not report.auto

    def test_dissimilar_is_miss(self):
        gold = _gold(grs=[_gr_dict("gr-1", "alpha beta gamma delta")])
        item = _item("用户导出数据报表为xlsx格式文件")
        report = match_gold_requirements(gold, [item])
        assert report.state_for("gr-1") == ItemMatchState.MISS
        assert report.unmatched_item_ids == [item.id]

    def test_summary_counts(self):
        gold = _gold(grs=[_gr_dict("gr-1", _HIT_STATEMENT), _gr_dict("gr-2", "alpha beta gamma delta")])
        item = _item(_HIT_STATEMENT)
        other = _item("用户导出数据报表为xlsx格式文件")
        report = match_gold_requirements(gold, [item, other])
        s = report.summary()
        assert s["auto_hit"] == 1 and s["miss"] == 1
        assert report.unmatched_item_ids == [other.id]


# ============================================================
# Critical Scenario：结构化 anchor（禁止简单关键词命中）
# ============================================================


def _hit_setup():
    """构造 gr-1 AUTO_HIT + TP + TC（step.action 含'输入手机号'，expected 含'提示格式错误'）"""
    item = _item(_HIT_STATEMENT)
    tp = _tp([item.id])
    tc = _tc([tp.id])
    index = ArtifactIndex(items=[item], test_points=[tp], test_cases=[tc], obligations=[])
    gold = _gold()
    report = match_gold_requirements(gold, [item])
    return gold, report, index


class TestScenarioMatching:
    def _scenario(self, **kw):
        base = {"scenario_id": "cs-1", "title": "边界校验", "requirement_gold_ids": ["gr-1"]}
        base.update(kw)
        return base

    def test_covered_via_structured_anchors(self):
        gold, report, index = _hit_setup()
        gold = _gold(
            scenarios=[self._scenario(expected_actions=["输入手机号"], expected_outcomes=["提示格式错误"])],
        )
        # BenchmarkGold 需要嵌套 dict 校验：requirement_gold_ids 已含 gr-1
        outcomes = match_critical_scenarios(gold, report, index)
        assert outcomes[0].state == ScenarioState.COVERED
        assert outcomes[0].anchors["actions"] is True and outcomes[0].anchors["outcomes"] is True

    def test_keyword_in_title_alone_not_covered(self):
        """关键词只出现在 title（非结构化 step/expected 字段）→ 不算命中（修正特别要求）"""
        item = _item(_HIT_STATEMENT)
        tp = _tp([item.id])
        tc = _tc([tp.id], title="输入手机号边界", action="点击按钮", expected="操作成功")
        index = ArtifactIndex(items=[item], test_points=[tp], test_cases=[tc], obligations=[])
        gold = _gold(scenarios=[self._scenario(expected_actions=["输入手机号"])])
        report = match_gold_requirements(gold, [item])
        outcomes = match_critical_scenarios(gold, report, index)
        assert outcomes[0].state == ScenarioState.MISSING
        assert outcomes[0].anchors["actions"] is False

    def test_partial_when_some_anchors_met(self):
        gold, report, index = _hit_setup()
        gold2 = _gold(
            scenarios=[
                self._scenario(expected_techniques=[Technique.BOUNDARY_VALUE.value], expected_actions=["输入手机号"])
            ]
        )
        outcomes = match_critical_scenarios(gold2, report, index)
        assert outcomes[0].state == ScenarioState.PARTIAL  # actions 满足、techniques 无
        assert outcomes[0].anchors["techniques"] is False and outcomes[0].anchors["actions"] is True

    def test_technique_anchor_satisfied_via_obligation(self):
        item = _item(_HIT_STATEMENT)
        ob = _ob(item.id)
        tp = _tp([item.id], technique=Technique.BOUNDARY_VALUE, obligation_id=ob.id, provenance=Provenance.STRATEGY)
        index = ArtifactIndex(items=[item], test_points=[tp], test_cases=[], obligations=[ob])
        gold = _gold(
            scenarios=[
                {
                    "scenario_id": "cs-1",
                    "title": "t",
                    "requirement_gold_ids": ["gr-1"],
                    "expected_techniques": ["boundary_value"],
                }
            ]
        )
        report = match_gold_requirements(gold, [item])
        outcomes = match_critical_scenarios(gold, report, index)
        assert outcomes[0].state == ScenarioState.COVERED

    def test_candidate_reference_makes_scenario_ambiguous(self):
        """引用项仅 CANDIDATE → 场景 AMBIGUOUS 进人工复核，不强行计分"""
        item = _item(_HIT_STATEMENT)
        index = ArtifactIndex(items=[item], test_points=[], test_cases=[], obligations=[])
        gold = _gold(
            grs=[_gr_dict("gr-1", _HIT_STATEMENT, match_keys={"identity_fingerprints": [_ZERO_FP]})],
            scenarios=[{"scenario_id": "cs-1", "title": "t", "requirement_gold_ids": ["gr-1"]}],
        )
        report = match_gold_requirements(gold, [item])
        outcomes = match_critical_scenarios(gold, report, index)
        assert outcomes[0].state == ScenarioState.AMBIGUOUS

    def test_missing_reference_makes_scenario_missing(self):
        gold = _gold(
            grs=[_gr_dict("gr-1", "alpha beta gamma delta")],
            scenarios=[{"scenario_id": "cs-1", "title": "t", "requirement_gold_ids": ["gr-1"]}],
        )
        item = _item("用户导出数据报表为xlsx格式文件")
        index = ArtifactIndex(items=[item], test_points=[], test_cases=[], obligations=[])
        report = match_gold_requirements(gold, [item])
        outcomes = match_critical_scenarios(gold, report, index)
        assert outcomes[0].state == ScenarioState.MISSING


# ============================================================
# Obligation（min_covered_points）与 Strategy（条件触发）
# ============================================================


class TestObligationAndStrategy:
    def _oe(self, min_points=1):
        return {
            "target": "phone.length",
            "technique": "boundary_value",
            "description": "长度边界",
            "requirement_gold_id": "gr-1",
            "min_covered_points": min_points,
        }

    def test_covered_meets_min_covered_points(self):
        item = _item(_HIT_STATEMENT)
        ob = _ob(item.id)
        tps = [
            _tp([item.id], technique=Technique.BOUNDARY_VALUE, obligation_id=ob.id, title=f"tp{i}") for i in range(2)
        ]
        index = ArtifactIndex(items=[item], test_points=tps, test_cases=[], obligations=[ob])
        gold = _gold(obligations=[self._oe(min_points=2)])
        outcomes = match_obligations(gold, index)
        assert outcomes[0].state == ObligationState.COVERED
        assert outcomes[0].covered_points == 2

    def test_under_covered_when_points_short(self):
        item = _item(_HIT_STATEMENT)
        ob = _ob(item.id)
        tps = [_tp([item.id], obligation_id=ob.id)]
        index = ArtifactIndex(items=[item], test_points=tps, test_cases=[], obligations=[ob])
        gold = _gold(obligations=[self._oe(min_points=3)])
        outcomes = match_obligations(gold, index)
        assert outcomes[0].state == ObligationState.UNDER_COVERED
        assert outcomes[0].required_points == 3

    def test_obligation_coverage_table_channel_counts(self):
        """obligation_coverage 关系表（obligation_covered_points）与 TP.obligation_id 回链取并集"""
        item = _item(_HIT_STATEMENT)
        ob = _ob(item.id)
        tp = _tp([item.id])  # 无 obligation_id 回链
        index = ArtifactIndex(
            items=[item],
            test_points=[tp],
            test_cases=[],
            obligations=[ob],
            obligation_covered_points={ob.id: [tp.id]},
        )
        gold = _gold(obligations=[self._oe(min_points=1)])
        outcomes = match_obligations(gold, index)
        assert outcomes[0].state == ObligationState.COVERED

    def test_missing_obligation(self):
        item = _item(_HIT_STATEMENT)
        index = ArtifactIndex(items=[item], test_points=[], test_cases=[], obligations=[])
        gold = _gold(obligations=[self._oe()])
        outcomes = match_obligations(gold, index)
        assert outcomes[0].state == ObligationState.MISSING

    def test_strategy_met_on_hit_item_with_technique(self):
        item = _item(_HIT_STATEMENT)
        ob = _ob(item.id)
        gold = _gold(
            strategies=[{"feature": "length_range", "technique": "boundary_value", "requirement_gold_id": "gr-1"}]
        )
        index = ArtifactIndex(items=[item], test_points=[], test_cases=[], obligations=[ob])
        report = match_gold_requirements(gold, [item])
        outcomes = check_strategy_expectations(gold, report, index)
        assert outcomes[0].met is True

    def test_strategy_unmet_without_technique(self):
        item = _item(_HIT_STATEMENT)
        gold = _gold(
            strategies=[{"feature": "length_range", "technique": "boundary_value", "requirement_gold_id": "gr-1"}]
        )
        index = ArtifactIndex(items=[item], test_points=[], test_cases=[], obligations=[])
        report = match_gold_requirements(gold, [item])
        outcomes = check_strategy_expectations(gold, report, index)
        assert outcomes[0].met is False

    def test_strategy_not_evaluable_when_feature_missed(self):
        """触发特征未 AUTO_HIT → met=None（不伪装成确定性结论、不算错）"""
        item = _item("完全不同的需求内容 xyz")
        gold = _gold(
            strategies=[{"feature": "length_range", "technique": "boundary_value", "requirement_gold_id": "gr-1"}]
        )
        index = ArtifactIndex(items=[item], test_points=[], test_cases=[], obligations=[])
        report = match_gold_requirements(gold, [item])
        outcomes = check_strategy_expectations(gold, report, index)
        assert outcomes[0].met is None


# ============================================================
# Hard Metrics 报告
# ============================================================


def _healthy_artifacts():
    """全绿场景：gr-1 AUTO_HIT + 义务覆盖 + 场景锚点满足 + TC 结构完整"""
    item = _item(_HIT_STATEMENT)
    ob = _ob(item.id)
    tp = _tp([item.id], technique=Technique.BOUNDARY_VALUE, obligation_id=ob.id)
    tc = _tc([tp.id], action="打开登录页并输入手机号", expected="提示格式错误")
    return (
        EvalArtifacts(
            items=[item],
            test_points=[tp],
            test_cases=[tc],
            obligations=[ob],
        ),
        item,
        tp,
        tc,
    )


class TestHardMetricsReport:
    def test_completed_run_all_green(self):
        artifacts, *_ = _healthy_artifacts()
        gold = _gold(
            scenarios=[
                {
                    "scenario_id": "cs-1",
                    "title": "t",
                    "requirement_gold_ids": ["gr-1"],
                    "expected_actions": ["输入手机号"],
                }
            ],
            obligations=[
                {"target": "phone.length", "technique": "boundary_value", "description": "d", "min_covered_points": 1}
            ],
            strategies=[{"feature": "length_range", "technique": "boundary_value", "requirement_gold_id": "gr-1"}],
        )
        report = evaluate_hard_metrics(_obs(artifacts, gold=gold))
        assert report.quality_evaluated is True
        assert report.requirement_coverage.value == 1.0
        assert report.critical_scenario_coverage.value == 1.0
        assert report.obligation_coverage.value == 1.0
        assert report.structural_validity.value == 1.0
        assert report.invalid == []
        assert report.missing_risk["total_open"] == 0
        assert all(
            cell.provenance == MetricProvenance.CODE
            for cell in (report.requirement_coverage, report.structural_validity)
        )

    def test_non_completed_excluded_from_quality_denominator(self):
        """修正/§9.1：非 COMPLETED 质量字段 None，Cost/counts 仍记录"""
        artifacts, *_ = _healthy_artifacts()
        report = evaluate_hard_metrics(
            _obs(
                artifacts,
                status=BenchmarkRunStatus.LLM_FAILURE,
                total_duration_ms=1234,
                total_llm_calls=9,
                reliability_note="heuristic: parser 降级日志命中",
            )
        )
        assert report.quality_evaluated is False
        assert report.requirement_coverage.value is None
        assert report.critical_scenario_coverage.value is None
        assert report.invalid == [] and report.pending_review == []
        assert report.cost_latency.total_duration_ms == 1234
        assert "heuristic" in report.requirement_coverage.detail

    def test_from_pipeline_result_duck_typing(self):
        """RunObservation 输入契约：鸭子类型读取 PipelineResult（不 import runtime，不改 Runtime）"""
        pr = SimpleNamespace(
            run_id="r1",
            steps=[
                SimpleNamespace(step="ir", success=True, duration_ms=100, llm_calls=2, counts={"items": 3}),
                SimpleNamespace(step="testpoints", success=False, duration_ms=50, llm_calls=1, counts={}),
            ],
            total_duration_ms=999,
            total_llm_calls=3,
        )
        obs = RunObservation.from_pipeline_result(
            pr, case_id="bc_t1", gold=_gold(), status=BenchmarkRunStatus.LLM_FAILURE
        )
        assert obs.total_duration_ms == 999 and obs.total_llm_calls == 3
        assert obs.steps[0].counts == {"items": 3}
        report = evaluate_hard_metrics(obs)
        assert report.cost_latency.llm_failure_steps == ["testpoints"]
        assert report.cost_latency.per_step[0].step == "ir"

    def test_cost_normalization_and_zero_guard(self):
        artifacts, *_ = _healthy_artifacts()
        obs = _obs(
            artifacts,
            total_duration_ms=1000,
            total_llm_calls=4,
            steps=[StepTiming(step="ir", duration_ms=400, llm_calls=1)],
        )
        report = evaluate_hard_metrics(obs)
        assert report.cost_latency.duration_ms_per_case == 1000.0  # 1 条 TC
        assert report.cost_latency.llm_calls_per_case == 4.0
        # 无产物 → 归一化 None（不伪造 0 分母结论）
        empty = evaluate_hard_metrics(_obs(EvalArtifacts(items=[_item("别的需求 xyz")]), total_duration_ms=10))
        assert empty.cost_latency.duration_ms_per_case is None

    def test_duplication_exact_and_semantic_separate(self):
        """修正 4：EXACT 与 SEMANTIC 两类分别呈现（复用 review_hard 两级检测）"""
        item = _item(_HIT_STATEMENT)
        tp1, tp2, tp3 = _tp([item.id], title="a"), _tp([item.id], title="b"), _tp([item.id], title="c")
        tc1 = _tc([tp1.id], display_id="TC_001", title="验证手机号长度为10位时提示格式错误输入", content_hash="ch-1")
        tc2 = _tc(
            [tp2.id], display_id="TC_002", title="验证手机号长度为11位时提示格式错误输入", content_hash="ch-2"
        )  # 语义重复
        tc3 = _tc(
            [tp3.id], display_id="TC_003", title="验证手机号长度为10位时提示格式错误输入", content_hash="ch-1"
        )  # 与 TC_001 同 hash → EXACT
        artifacts = EvalArtifacts(items=[item], test_points=[tp1, tp2, tp3], test_cases=[tc1, tc2, tc3])
        report = evaluate_hard_metrics(_obs(artifacts))
        assert len(report.duplication_exact) >= 1
        assert len(report.duplication_semantic) >= 1
        assert report.duplication_score.value is not None and report.duplication_score.value < 1.0

    def test_gold_miss_invalid_pending_are_separate(self):
        """特别要求：Gold MISS、INVALID、PENDING_REVIEW 分开统计"""
        hit = _item(_HIT_STATEMENT)
        stray = _item("alpha beta gamma delta")  # 未命中 Gold 的 runtime item → unsupported candidate
        tp_ok = _tp([hit.id])
        tp_orphan = _tp([])  # trace 断链 → INVALID
        tp_unverified = _tp([stray.id], title="额外点")
        tc_bad = _tc([tp_ok.id], display_id="TC_009", validation_errors=["steps 缺失"])  # 明确判错 → INVALID
        artifacts = EvalArtifacts(
            items=[hit, stray],
            test_points=[tp_ok, tp_orphan, tp_unverified],
            test_cases=[tc_bad],
            obligations=[],
        )
        # gr-1 CANDIDATE（match_keys 冻结为不存在指纹，stray 提供相似度）→ pending_review；gold 里另一项 gr-2 MISS
        gold = _gold(
            grs=[
                _gr_dict("gr-1", _HIT_STATEMENT, match_keys={"identity_fingerprints": [_ZERO_FP]}),
                _gr_dict("gr-2", "审核流程需要两级审批"),
            ]
        )
        report = evaluate_hard_metrics(_obs(artifacts, gold=gold))
        assert "gr-2" in report.missing_risk["gold_miss_ids"]  # MISS 独立
        assert any(f.reason.startswith("validation_errors") for f in report.invalid)  # INVALID 独立
        assert any(f.target_id == tp_orphan.id and "trace 断链" in f.reason for f in report.invalid)
        kinds = {p.kind for p in report.pending_review}
        assert {"match_candidate", "unsupported_item", "unverified_output"} <= kinds  # PENDING_REVIEW 独立
        assert report.requirement_coverage.value == 0.0  # CANDIDATE 不计分

    def test_never_auto_adjudicated_additional_valid(self):
        """修正 3：S3 绝不自动判 ADDITIONAL_VALID，只登记 unverified_output"""
        artifacts, *_ = _healthy_artifacts()
        extra_item = _item("导出报表需支持 xlsx 与 csv 两种格式")
        extra_tp = _tp([extra_item.id], title="额外合理点")
        artifacts.items.append(extra_item)
        artifacts.test_points.append(extra_tp)
        report = evaluate_hard_metrics(_obs(artifacts))
        unverified = [p for p in report.pending_review if p.kind == "unverified_output"]
        assert any(p.ref_id == extra_tp.id for p in unverified)
        assert all(getattr(p, "kind", None) != "additional_valid" for p in report.pending_review)
        assert "additional_valid" not in str(report.missing_risk)

    def test_reviewer_based_snapshot_passthrough(self):
        """双轨：REVIEWER-BASED 只透传 Step 7 产物，标注 MIXED provenance，不与 GOLD-BASED 混算"""
        artifacts, *_ = _healthy_artifacts()
        report_obj = ReviewReport(
            run_id=_RUN,
            revision=1,
            trigger_type=ReviewTriggerType.INITIAL,
            scores=ReviewScores(
                coverage=80, accuracy=90, executability=70, consistency=100, missing_risk=60, duplication=80
            ),
            obligation_coverage=0.9,
        )
        report_obj.findings = [
            # 直构 finding 列表（get_review_report 同型）：一条硬指标 + 一条 LLM 软判断
            _finding(Provenance.VALIDATOR),
            _finding(Provenance.LLM),
        ]
        artifacts.review_report = report_obj
        report = evaluate_hard_metrics(_obs(artifacts))
        assert report.reviewer_based is not None
        assert report.reviewer_based.provenance == MetricProvenance.MIXED
        assert report.reviewer_based.overall_score == report_obj.overall_score
        assert report.reviewer_based.findings_by_provenance == {"validator": 1, "llm": 1}
        # GOLD-BASED 指标不受 reviewer 影响（不混算）
        assert report.requirement_coverage.value == 1.0

    def test_priority_hint_and_counts_observation(self):
        artifacts, *_ = _healthy_artifacts()
        strategy_tp = _tp(
            [artifacts.items[0].id], technique=Technique.BOUNDARY_VALUE, provenance=Provenance.STRATEGY, title="s"
        )
        artifacts.test_points.append(strategy_tp)
        report = evaluate_hard_metrics(_obs(artifacts))
        assert report.counts.strategy_points == 1 and report.counts.llm_points == 1
        assert report.counts.test_points == 2  # 数量只观察，无任何门槛判定逻辑

    def test_test_point_precision_proxy(self):
        hit = _item(_HIT_STATEMENT)
        stray = _item("alpha beta gamma delta")
        tp_linked = _tp([hit.id])
        tp_unlinked = _tp([stray.id], title="u")
        artifacts = EvalArtifacts(items=[hit, stray], test_points=[tp_linked, tp_unlinked])
        report = evaluate_hard_metrics(_obs(artifacts))
        assert report.test_point_precision.value == 0.5
        assert report.requirement_precision.value == 0.5


def _finding(prov: Provenance) -> ReviewFinding:
    return ReviewFinding(
        dimension="coverage",
        severity=Severity.MINOR,
        target_type=TargetType.TESTCASE,
        target_id=new_ulid(),
        issue="t",
        provenance=prov,
    )
