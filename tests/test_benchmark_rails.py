"""Step 11 S7 三轨关联层单测（裁决 D1/D2/D3/D8/D9/D10）。

零 LLM / 零 DB（除末尾"真实 smoke 库回归"一组，库不存在则 skip）。覆盖：
  - textops 公共 helper 语义 + 与 change_impact._similarity 的**防漂移**恒等断言（裁决 D8）
  - Identity Rail 不回退：纯 identity 夹具下 rails 与 S3 matching 逐项相等
  - Semantic Rail bridge：正例 / 三类负例 / identity 优先级
  - Semantic Rail anchor：covered-partial-missing、只扫 step.action+tc.expected（不扫自由文本）、
    无锚点场景 AMBIGUOUS、ARCHIVED 已由装配层排除故不参与判定
  - 裁决 D10：anchor 多义 → 覆盖率计入 / item_id=None / 保留 item_ids / 登记 pending / 不进 S5 trace
  - 裁决 D3：去重与优先级 identity>bridge>anchor；candidate/miss 永不自动计分
  - 裁决 D1：identity_match_rate=0 时 requirement_coverage 仍 >0（机器化守卫）
  - S5 零改动接线：rails 关联结果直接喂 evaluate_soft_metrics → tc_batch>0
  - 真实 smoke 库回归：G1 夹具数字（deepseek 对照 / qwen baseline）
"""

from pathlib import Path

import pytest

from core.schemas import (
    CoverageObligation,
    Provenance,
    RequirementItem,
    RequirementItemType,
    Technique,
    TestCase,
    TestCaseType,
    TestPoint,
    TestStep,
    new_ulid,
)
from core.v2.change_impact import _similarity
from core.v2.eval.matching import ArtifactIndex, ItemMatchState, match_gold_requirements
from core.v2.eval.metrics_hard import EvalArtifacts, HardMetricsReport, RunObservation, evaluate_hard_metrics
from core.v2.eval.metrics_soft import evaluate_soft_metrics
from core.v2.eval.rails import (
    PENDING_ANCHOR_ITEM_AMBIGUOUS,
    VIA_ANCHOR,
    VIA_BRIDGE,
    VIA_IDENTITY,
    associate_gold,
    bridge_associations,
    build_identity_diagnostics,
)
from core.v2.eval.schema import BenchmarkGold, BenchmarkRunStatus, GoldAuthoring
from core.v2.eval.textops import anchor_satisfied, statement_similarity
from core.v2.fingerprint import compute_item_identity_fingerprint, normalize_text

_ROOT = Path(__file__).resolve().parents[1]
_V = new_ulid()
_RUN = new_ulid()
_STMT = "手机号必须为 11 位数字"


def _fp(statement=_STMT, module="登录", type_="data_field"):
    return compute_item_identity_fingerprint(module=module, type=type_, statement=statement)


def _item(statement=_STMT, *, module="登录", type_=RequirementItemType.DATA_FIELD, version_id=_V):
    return RequirementItem(
        version_id=version_id,
        seq=1,
        type=type_,
        module=module,
        statement=statement,
        fingerprint=compute_item_identity_fingerprint(module=module, type=type_.value, statement=statement),
    )


def _tp(item_ids, *, title="测试点", technique=None, obligation_id=None, provenance=Provenance.LLM):
    return TestPoint(
        run_id=_RUN,
        version_id=_V,
        item_ids=list(item_ids),
        module="登录",
        subcategory="边界",
        title=title,
        description="d",
        dimension="functional",
        technique=technique,
        obligation_id=obligation_id,
        provenance=provenance,
    )


def _tc(tp_ids, *, display_id="TC_001", action="输入手机号并提交", expected="提示格式错误", title="用例"):
    return TestCase(
        run_id=_RUN,
        display_id=display_id,
        test_point_ids=list(tp_ids),
        module="登录",
        title=title,
        precondition="已打开登录页",
        steps=[TestStep(seq=1, action=action)],
        expected=expected,
        type=TestCaseType.FUNCTIONAL,
        remark="备注里含有 提示格式错误 也不该被扫描",
    )


def _ob(item_id, *, technique=Technique.BOUNDARY_VALUE, target="phone.length"):
    return CoverageObligation(run_id=_RUN, item_id=item_id, technique=technique, target=target, description="d")


def _gr(gold_id, statement=_STMT, *, module="登录", type_="data_field", with_keys=True):
    d = {"gold_id": gold_id, "module": module, "type": type_, "statement": statement}
    if with_keys:
        d["match_keys"] = {"identity_fingerprints": [_fp(statement, module, type_)]}
    return d


def _gold(*, grs=None, scenarios=None, obligations=None, strategies=None) -> BenchmarkGold:
    return BenchmarkGold.model_validate(
        {
            "gold_version": "gold-rails-test",
            "case_id": "bc_rails",
            "authoring_note": "rails 单测夹具",
            "gold_authoring": GoldAuthoring.HUMAN_INDEPENDENT.value,
            "gold_requirements": grs if grs is not None else [_gr("gr-1")],
            "critical_scenarios": scenarios or [],
            "obligations_expected": obligations or [],
            "strategy_expectations": strategies or [],
            "reference_cases": [],
            "acceptable_variants": [],
            "forbidden_patterns": [],
        }
    )


def _index(items=(), tps=(), tcs=(), obs=(), covered=None) -> ArtifactIndex:
    arts = EvalArtifacts(
        items=list(items),
        test_points=list(tps),
        test_cases=list(tcs),
        obligations=list(obs),
        obligation_covered_points=dict(covered or {}),
    )
    return arts.to_index()


# ============================================================
# textops（裁决 D8）
# ============================================================


class TestTextops:
    def test_anchor_satisfied_substring_and_normalize(self):
        assert anchor_satisfied("提示格式错误", ["系统提示格式错误，请重试"])
        assert anchor_satisfied("连续输错密码 5 次", ["连续输错密码  5  次后锁定"])  # 空白折叠
        assert anchor_satisfied("ABC", ["xx abc yy"])  # 大小写归一

    def test_anchor_satisfied_empty_and_absent(self):
        assert anchor_satisfied("", ["任意内容"]) is False  # 空锚点恒 False
        assert anchor_satisfied("不存在短语", ["输入手机号并提交"]) is False
        assert anchor_satisfied("x", []) is False

    @pytest.mark.parametrize(
        "a,b",
        [
            ("手机号必须为 11 位数字", "手机号必须为 11 位数字"),
            ("手机号必须为 11 位数字", "手机号必须为11位数字"),
            ("验证码 6 位", "完全不同的表述"),
            ("", "非空"),
            ("A b C", "a B c"),
        ],
    )
    def test_no_drift_against_change_impact_similarity(self, a, b):
        """防漂移：textops 与 Step 6 相似度口径必须恒等（避免两处实现各自演化）"""
        assert statement_similarity(a, b) == _similarity(a, b)


# ============================================================
# Identity Rail 不回退
# ============================================================


class TestIdentityRailUnchanged:
    def test_pure_identity_equals_s3_matching(self):
        """无 obligation / 无 scenario 时，rails 结果必须与 S3 matching 逐项相等"""
        item = _item()
        gold = _gold()
        index = _index(items=[item], tps=[_tp([item.id])], tcs=[_tc([_tp([item.id]).id])])
        s3 = match_gold_requirements(gold, index.items)
        r = associate_gold(gold, index)
        assert [(m.gold_id, m.state, m.item_id) for m in r.matches.matches] == [
            (m.gold_id, m.state, m.item_id) for m in s3.matches
        ]
        assert r.matches.auto == s3.auto
        assert r.matches.consumed_item_ids == s3.consumed_item_ids
        assert r.matches.unmatched_item_ids == s3.unmatched_item_ids
        assert all(m.via == VIA_IDENTITY for m in r.matches.matches if m.state == ItemMatchState.AUTO_HIT)
        assert r.via_counts["identity"] == 1 and r.diagnostics.identity_match_rate == 1.0

    def test_identity_diagnostics_counts(self):
        """诊断：module/type/verbatim 三项分别统计（parser 身份稳定性度量）"""
        renamed = _item("手机号必须为11位数字", module="用户登录")  # 改写 + 改名
        gold = _gold(grs=[_gr("gr-1", module="登录"), _gr("gr-2", "密码至少 8 位", module="注册")])
        diag = associate_gold(gold, _index(items=[renamed])).diagnostics
        assert diag.gold_total == 2
        assert diag.auto_hit_identity_only == 0 and diag.identity_match_rate == 0.0
        assert diag.module_consistency == 0  # 用户登录 != 登录 / 注册
        assert diag.statement_verbatim == 0
        assert set(diag.gold_modules) == {"登录", "注册"} and diag.runtime_modules == ["用户登录"]
        assert sorted(diag.identity_miss_gold_ids) == ["gr-1", "gr-2"]

    def test_diagnostics_empty_inputs(self):
        """无 runtime items 时：identity_match_rate=0（分母存在）、相似度均值为 None（不伪造）"""
        d = build_identity_diagnostics(_gold(), _index(), [])
        assert d.gold_total == 1 and d.identity_match_rate == 0.0
        assert d.best_similarity_mean is None and d.module_consistency == 0


# ============================================================
# Semantic Rail · bridge
# ============================================================


class TestBridgeRail:
    def _fixture(
        self,
        *,
        target="phone.length",
        technique=Technique.BOUNDARY_VALUE,
        gold_target=None,
        gold_technique="boundary_value",
        with_gold_ref=True,
    ):
        item = _item("手机号必填且为 11 位")  # 与 Gold statement 不同 → identity 必然不命中
        ob = _ob(item.id, technique=technique, target=target)
        tp = _tp([item.id], technique=technique, obligation_id=ob.id, provenance=Provenance.STRATEGY)
        gold = _gold(
            grs=[_gr("gr-phone", "手机号必填，须为 11 位中国大陆号码")],
            obligations=[
                {
                    "target": gold_target or target,
                    "technique": gold_technique,
                    "description": "d",
                    "requirement_gold_id": "gr-phone" if with_gold_ref else None,
                    "min_covered_points": 1,
                }
            ],
        )
        return gold, _index(items=[item], tps=[tp], tcs=[_tc([tp.id])], obs=[ob], covered={ob.id: [tp.id]}), ob, item

    def test_bridge_associates_without_identity(self):
        gold, index, ob, item = self._fixture()
        r = associate_gold(gold, index)
        m = r.matches.matches[0]
        assert m.state == ItemMatchState.AUTO_HIT and m.via == VIA_BRIDGE
        assert m.item_id == item.id and ob.target in m.basis
        assert r.associated_gold_ids == ["gr-phone"] and r.via_counts["bridge"] == 1
        assert r.diagnostics.identity_match_rate == 0.0  # identity 仍为 0，但覆盖率不为 0（裁决 D1）

    def test_bridge_negative_wrong_target(self):
        gold, index, *_ = self._fixture(gold_target="phone.len")
        r = associate_gold(gold, index)
        assert r.associated_gold_ids == [] and r.via_counts["bridge"] == 0

    def test_bridge_negative_wrong_technique(self):
        """Gold 义务 technique 与 runtime 义务不同 → (technique,target) 精确匹配失败 → 不桥接"""
        gold, index, *_ = self._fixture(gold_technique="equivalence_class")
        r = associate_gold(gold, index)
        assert r.via_counts["bridge"] == 0
        assert r.associated_gold_ids == []

    def test_bridge_negative_missing_gold_ref(self):
        """裁决 D3 第 4 条：无 requirement_gold_id 的义务不得形成映射"""
        gold, index, *_ = self._fixture(with_gold_ref=False)
        assert bridge_associations(gold, index) == {}
        assert associate_gold(gold, index).associated_gold_ids == []

    def test_bridge_negative_no_runtime_obligation(self):
        gold, _index_full, ob, item = self._fixture()
        empty = _index(items=[item], tps=[_tp([item.id])], tcs=[_tc([_tp([item.id]).id])], obs=[])
        assert associate_gold(gold, empty).associated_gold_ids == []

    def test_identity_wins_over_bridge(self):
        """裁决 D3 第 6 条：identity > bridge，同一 Gold 只计一次且 via=identity"""
        item = _item()  # statement 与 Gold 完全一致 → identity 命中
        ob = _ob(item.id)
        tp = _tp([item.id], obligation_id=ob.id, technique=Technique.BOUNDARY_VALUE)
        gold = _gold(
            obligations=[
                {
                    "target": "phone.length",
                    "technique": "boundary_value",
                    "description": "d",
                    "requirement_gold_id": "gr-1",
                    "min_covered_points": 1,
                }
            ]
        )
        r = associate_gold(gold, _index(items=[item], tps=[tp], tcs=[_tc([tp.id])], obs=[ob], covered={ob.id: [tp.id]}))
        assert r.via_counts["identity"] == 1 and r.via_counts["bridge"] == 0
        assert r.matches.matches[0].via == VIA_IDENTITY
        assert r.associated_gold_ids == ["gr-1"]  # 不重复计数


# ============================================================
# Semantic Rail · anchor
# ============================================================


class TestAnchorRail:
    def _gold_with_scenario(self, *, actions=None, outcomes=None, techniques=None):
        return _gold(
            grs=[_gr("gr-lock", "连续输错密码 5 次后账号锁定 15 分钟")],
            scenarios=[
                {
                    "scenario_id": "cs-lock",
                    "title": "锁定",
                    "requirement_gold_ids": ["gr-lock"],
                    "expected_techniques": [t.value for t in (techniques or [])],
                    "expected_actions": actions or [],
                    "expected_outcomes": outcomes or [],
                }
            ],
        )

    def test_anchor_covered_associates_gold(self):
        """outcome 锚点命中 tc.expected → COVERED 且关联 Gold（identity 为 0 也成立）"""
        item = _item("账号锁定规则")
        tp = _tp([item.id])
        tc = _tc([tp.id], expected="系统提示：账号已被锁定，请 15 分钟后再试")
        gold = self._gold_with_scenario(outcomes=["账号已被锁定，请 15 分钟后再试"])
        r = associate_gold(gold, _index(items=[item], tps=[tp], tcs=[tc]))
        assert r.scenario_outcomes[0].state.value == "covered"
        assert r.associated_gold_ids == ["gr-lock"]
        assert r.matches.matches[0].via == VIA_ANCHOR and r.matches.matches[0].item_id == item.id
        assert r.diagnostics.identity_match_rate == 0.0

    def test_anchor_partial_and_missing_do_not_associate(self):
        item = _item("账号锁定规则")
        tp = _tp([item.id])
        tc = _tc([tp.id], action="连续输错密码 5 次", expected="无关提示")
        gold = self._gold_with_scenario(actions=["连续输错密码 5 次"], outcomes=["账号已被锁定"])
        r = associate_gold(gold, _index(items=[item], tps=[tp], tcs=[tc]))
        assert r.scenario_outcomes[0].state.value == "partial"  # action 过、outcome 不过
        assert r.associated_gold_ids == []
        gold2 = self._gold_with_scenario(actions=["完全不存在的动作"], outcomes=["也不存在的结果"])
        r2 = associate_gold(gold2, _index(items=[item], tps=[tp], tcs=[tc]))
        assert r2.scenario_outcomes[0].state.value == "missing" and r2.associated_gold_ids == []

    def test_anchor_ignores_free_text_fields(self):
        """裁决 D3 第 2/3 条：只扫 step.action 与 tc.expected，title/precondition/remark 一律不算"""
        item = _item("账号锁定规则")
        tp = _tp([item.id])
        tc = _tc(
            [tp.id],
            action="随便操作",
            expected="无关结果",
            title="账号已被锁定，请 15 分钟后再试",  # 锚点只在自由文本里
        )
        tc.precondition = "账号已被锁定，请 15 分钟后再试"
        tc.remark = "账号已被锁定，请 15 分钟后再试"
        gold = self._gold_with_scenario(outcomes=["账号已被锁定，请 15 分钟后再试"])
        r = associate_gold(gold, _index(items=[item], tps=[tp], tcs=[tc]))
        assert r.scenario_outcomes[0].state.value == "missing"
        assert r.associated_gold_ids == []

    def test_anchor_not_matched_by_similarity(self):
        """裁决 D3 第 4 条：高度相似但非子串 → 不得计分"""
        item = _item("账号锁定规则")
        tp = _tp([item.id])
        tc = _tc([tp.id], expected="账号会被锁定十五分钟后再试")  # 语义相近、非子串
        gold = self._gold_with_scenario(outcomes=["账号已被锁定，请 15 分钟后再试"])
        r = associate_gold(gold, _index(items=[item], tps=[tp], tcs=[tc]))
        assert r.scenario_outcomes[0].state.value == "missing" and r.associated_gold_ids == []

    def test_scenario_without_any_anchor_is_ambiguous(self):
        item = _item("账号锁定规则")
        tp = _tp([item.id])
        gold = self._gold_with_scenario()  # actions/outcomes/techniques 全空
        r = associate_gold(gold, _index(items=[item], tps=[tp], tcs=[_tc([tp.id])]))
        assert r.scenario_outcomes[0].state.value == "ambiguous"
        assert r.associated_gold_ids == []
        assert any(p.kind == "scenario_unjudgeable" for p in r.pending)

    def test_anchor_uses_only_index_testcases(self):
        """裁决 D9：判定范围 = 传入的非 ARCHIVED 评价集合；被排除的用例不参与"""
        item = _item("账号锁定规则")
        tp = _tp([item.id])
        archived_like = _tc([tp.id], expected="账号已被锁定，请 15 分钟后再试")
        gold = self._gold_with_scenario(outcomes=["账号已被锁定，请 15 分钟后再试"])
        # 装配层已排除 ARCHIVED → index 里没有它 → 不得判 covered
        r = associate_gold(gold, _index(items=[item], tps=[tp], tcs=[]))
        assert r.scenario_outcomes[0].state.value == "missing"
        r2 = associate_gold(gold, _index(items=[item], tps=[tp], tcs=[archived_like]))
        assert r2.scenario_outcomes[0].state.value == "covered"

    def test_anchor_technique_scope_run_when_unlinked(self):
        """无 identity/bridge 关联时，technique 锚点退化到 run 范围判定（detail 标注 scope）"""
        item = _item("权限规则")
        tp = _tp([item.id], technique=Technique.PERMISSION_MATRIX)
        gold = self._gold_with_scenario(techniques=[Technique.PERMISSION_MATRIX])
        r = associate_gold(gold, _index(items=[item], tps=[tp], tcs=[_tc([tp.id])]))
        assert r.scenario_outcomes[0].state.value == "covered"
        assert "technique 判定范围=run" in r.scenario_outcomes[0].detail


# ============================================================
# 裁决 D10：anchor 多义映射
# ============================================================


class TestAnchorAmbiguity:
    def _two_item_fixture(self):
        i1, i2 = _item("锁定规则 A"), _item("锁定规则 B")
        tp1, tp2 = _tp([i1.id], title="p1"), _tp([i2.id], title="p2")
        tc = _tc([tp1.id, tp2.id], expected="账号已被锁定，请 15 分钟后再试")  # 一条 TC 追溯两个 item
        gold = _gold(
            grs=[_gr("gr-lock", "连续输错密码 5 次后账号锁定 15 分钟")],
            scenarios=[
                {
                    "scenario_id": "cs-lock",
                    "title": "锁定",
                    "requirement_gold_ids": ["gr-lock"],
                    "expected_techniques": [],
                    "expected_actions": [],
                    "expected_outcomes": ["账号已被锁定，请 15 分钟后再试"],
                }
            ],
        )
        return gold, _index(items=[i1, i2], tps=[tp1, tp2], tcs=[tc]), i1, i2

    def test_multi_item_counts_but_does_not_guess(self):
        gold, index, i1, i2 = self._two_item_fixture()
        r = associate_gold(gold, index)
        m = r.matches.matches[0]
        assert m.state == ItemMatchState.AUTO_HIT and m.via == VIA_ANCHOR
        assert m.item_id is None  # 不猜
        assert sorted(m.item_ids) == sorted([i1.id, i2.id])  # 保留候选
        assert r.associated_gold_ids == ["gr-lock"]  # 覆盖率仍计入
        assert m.gold_id not in r.matches.auto  # 不进唯一映射 → 不给 S5 trace
        pend = [p for p in r.pending if p.kind == PENDING_ANCHOR_ITEM_AMBIGUOUS]
        assert len(pend) == 1 and pend[0].ref_id == "gr-lock"

    def test_strategy_unjudgeable_when_anchor_ambiguous(self):
        """多义 anchor 关联下 strategy 判 None（不可判），绝不伪造 False"""
        _g0, index, *_ = self._two_item_fixture()
        g2 = _gold(
            grs=[_gr("gr-lock", "连续输错密码 5 次后账号锁定 15 分钟")],
            scenarios=[
                {
                    "scenario_id": "cs-lock",
                    "title": "锁定",
                    "requirement_gold_ids": ["gr-lock"],
                    "expected_techniques": [],
                    "expected_actions": [],
                    "expected_outcomes": ["账号已被锁定，请 15 分钟后再试"],
                }
            ],
            strategies=[
                {"feature": "required_field", "technique": "equivalence_class", "requirement_gold_id": "gr-lock"}
            ],
        )
        r2 = associate_gold(g2, index)
        assert r2.matches.matches[0].via == VIA_ANCHOR  # 已确定性关联（覆盖率计入）
        assert r2.strategy_outcomes[0].met is None  # 但无唯一 item → 不可判，不伪造 False
        assert "不可判" in r2.strategy_outcomes[0].detail


# ============================================================
# 裁决 D3：candidate/miss 永不自动计分
# ============================================================


class TestNoFuzzyScoring:
    def test_high_similarity_without_fingerprint_is_not_associated(self):
        item = _item("手机号必须为 11 位数字，且唯一")  # 与 Gold 极相似但指纹不同
        gold = _gold(grs=[_gr("gr-1", "手机号必须为 11 位数字")])
        r = associate_gold(gold, _index(items=[item], tps=[_tp([item.id])], tcs=[_tc([_tp([item.id]).id])]))
        assert r.associated_gold_ids == []
        assert r.matches.matches[0].state in (ItemMatchState.CANDIDATE, ItemMatchState.MISS)
        assert r.via_counts.get("identity", 0) == 0

    def test_via_counts_shape(self):
        item = _item()
        r = associate_gold(_gold(), _index(items=[item]))
        for key in ("identity", "bridge", "anchor"):
            assert key in r.via_counts


# ============================================================
# metrics_hard 集成（Step B 前的行为基线）+ S5 零改动接线
# ============================================================


class TestDownstreamWiring:
    def _associated_report(self):
        """bridge 关联的 HardMetricsReport（手工装配，Step B 前用于验证 S5 接线）"""
        item = _item("手机号必填且为 11 位")
        ob = _ob(item.id)
        tp = _tp([item.id], obligation_id=ob.id, technique=Technique.BOUNDARY_VALUE)
        tc = _tc([tp.id])
        arts = EvalArtifacts(items=[item], test_points=[tp], test_cases=[tc], obligations=[ob])
        arts.obligation_covered_points = {ob.id: [tp.id]}
        gold = _gold(
            grs=[_gr("gr-phone", "手机号必填，须为 11 位中国大陆号码")],
            obligations=[
                {
                    "target": "phone.length",
                    "technique": "boundary_value",
                    "description": "d",
                    "requirement_gold_id": "gr-phone",
                    "min_covered_points": 1,
                }
            ],
        )
        r = associate_gold(gold, arts.to_index())
        hard = HardMetricsReport(
            case_id="bc_rails",
            run_status=BenchmarkRunStatus.COMPLETED,
            quality_evaluated=True,
            run_id=_RUN,
            requirement_matches=r.matches.matches,
        )
        return gold, arts, r, hard

    def test_s5_consumes_rail_matches_without_modification(self):
        """裁决 D1 的接线验证：S5 只读 state/item_id → 三轨关联后 tc_batch>0（metrics_soft 零改动）"""
        gold, arts, r, hard = self._associated_report()
        obs = RunObservation(
            case_id="bc_rails", gold=gold, status=BenchmarkRunStatus.COMPLETED, run_id=_RUN, artifacts=arts
        )

        class StubLLM:
            def __init__(self):
                self.calls = []

            def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
                self.calls.append((system_prompt, user_prompt))
                return (
                    '{"results":[{"display_id":"TC_001","verdict":"correct","confidence":0.9,'
                    '"reason":"ok","evidence_refs":[],"forbidden_pattern_id":null}],'
                    '"additional_valid_candidates":[]}'
                )

        stub = StubLLM()
        soft = evaluate_soft_metrics(obs, hard, stub)
        kinds = [b.kind for b in soft.batches]
        detail = (
            f"kinds={kinds} failures={soft.failures} llm_calls={soft.llm_calls} "
            f"judgments={[(j.display_id, j.verdict.value, j.pending_reason) for j in soft.judgments]} "
            f"matches={[(m.gold_id, m.state.value, m.via, m.item_id) for m in hard.requirement_matches]}"
        )
        assert "tc_batch" in kinds, f"S5 未送评任何 TC：{detail}"
        assert soft.llm_calls == len(stub.calls) > 0, detail
        assert any(j.verdict.value == "correct" for j in soft.judgments), detail

    def test_hard_metrics_still_runs_on_rail_matches(self):
        """关联层产物喂给现有 S3 引擎不得抛异常（Step B 接线前的兼容性证明）"""
        gold, arts, r, hard = self._associated_report()
        obs = RunObservation(
            case_id="bc_rails", gold=gold, status=BenchmarkRunStatus.COMPLETED, run_id=_RUN, artifacts=arts
        )
        rep = evaluate_hard_metrics(obs)
        assert rep.quality_evaluated is True


# ============================================================
# 真实 smoke 库回归（G1 夹具数字；库缺失则 skip）
# ============================================================

_BC01 = _ROOT / "benchmark"
_SMOKES = {
    "deepseek": (_ROOT / "benchmark/data/smoke_benchmark_v2.db", "01M31H8VYCTX89A9FSVFH7J30C"),
    "qwen": (_ROOT / "benchmark/data/smoke_qwen_benchmark_v2.db", "01M33B6CSCY4QECF1CZRDK3ZK0"),
}


@pytest.fixture
def suite():
    from core.v2.eval.schema import load_benchmark_suite

    return load_benchmark_suite(_BC01)


def _load_rail(db_path: Path, run_id: str, gold):
    from core.v2 import db as v2db
    from core.v2 import repository as repo
    from core.v2.eval import runner_lib as rl

    old = v2db.get_v2_db_path()
    v2db.set_v2_db_path(str(db_path))
    try:
        run = repo.get_run(run_id)
        bundle = rl.load_eval_artifacts(run_id, run.requirement_version_id)
        return associate_gold(gold, bundle.artifacts.to_index()), bundle
    finally:
        v2db.set_v2_db_path(old)


@pytest.mark.parametrize("label", ["deepseek", "qwen"])
class TestRealSmokeRegression:
    """G1 夹具（gold-v0.2 口径）。这些数字仅作重构回归标尺，不是质量目标、不是模型能力评分。"""

    def test_requirement_coverage_and_identity(self, label, suite):
        db, rid = _SMOKES[label]
        if not db.exists():
            pytest.skip(f"smoke 库不存在：{db.name}")
        r, _ = _load_rail(db, rid, suite.golds["bc_01_login"])
        assert len(r.associated_gold_ids) == 8  # 8/10 = 0.80
        assert r.via_counts["identity"] == 0 and r.via_counts["bridge"] == 5 and r.via_counts["anchor"] == 3
        assert r.diagnostics.identity_match_rate == 0.0
        assert r.diagnostics.statement_verbatim == 0
        assert sorted(
            set(g.gold_id for g in suite.golds["bc_01_login"].gold_requirements) - set(r.associated_gold_ids)
        ) == [
            "gr-lock-rule",
            "gr-register-entry",
        ]

    def test_scenario_strategy_obligation(self, label, suite):
        db, rid = _SMOKES[label]
        if not db.exists():
            pytest.skip(f"smoke 库不存在：{db.name}")
        gold = suite.golds["bc_01_login"]
        r, bundle = _load_rail(db, rid, gold)
        covered = [o for o in r.scenario_outcomes if o.state.value == "covered"]
        assert len(covered) == 3 and len(r.scenario_outcomes) == 6
        assert sorted(o.scenario_id for o in covered) == [
            "cs-code-expired",
            "cs-unregistered-login",
            "cs-wrong-password",
        ]
        assert all(o.met is True for o in r.strategy_outcomes)  # 由全 None 变为 6/6 可判
        # obligation 轨口径不得因改造退化（S6 装配 + S3 判定）
        obs = RunObservation(
            case_id="bc_01_login",
            gold=gold,
            status=BenchmarkRunStatus.COMPLETED,
            artifacts=bundle.artifacts,
        )
        rep = evaluate_hard_metrics(obs)
        assert rep.obligation_coverage.value == 1.0 and rep.structural_validity.value == 1.0

    def test_identity_diagnostics_per_model(self, label, suite):
        db, rid = _SMOKES[label]
        if not db.exists():
            pytest.skip(f"smoke 库不存在：{db.name}")
        r, _ = _load_rail(db, rid, suite.golds["bc_01_login"])
        expected = {"deepseek": (3, 9), "qwen": (0, 8)}[label]
        assert (r.diagnostics.module_consistency, r.diagnostics.type_consistency) == expected
        assert "用户注册" in r.diagnostics.runtime_modules

    def test_s5_sendable_nonzero(self, label, suite):
        db, rid = _SMOKES[label]
        if not db.exists():
            pytest.skip(f"smoke 库不存在：{db.name}")
        r, bundle = _load_rail(db, rid, suite.golds["bc_01_login"])
        hit_items = {m.item_id for m in r.matches.matches if m.state == ItemMatchState.AUTO_HIT and m.item_id}
        tp_by = {t.id: t for t in bundle.artifacts.test_points}
        sendable = sum(
            1
            for tc in bundle.artifacts.test_cases
            if any(set(tp_by[p].item_ids) & hit_items for p in tc.test_point_ids if p in tp_by)
        )
        assert sendable >= 30, f"S5 可送评 TC 过少：{sendable}"


# ============================================================
# 边界纪律
# ============================================================


class TestBoundaryDiscipline:
    def test_matching_identity_logic_untouched(self):
        """裁决 D12：matching.py 只允许机械抽取 helper，不得出现三轨新逻辑"""
        src = (_ROOT / "core/v2/eval/matching.py").read_text(encoding="utf-8")
        assert "_anchor_satisfied" not in src  # 已抽至 textops
        for banned in ("VIA_BRIDGE", "obligation_bridge", "associate_gold", "rails"):
            assert banned not in src, f"matching.py 不应含三轨逻辑：{banned}"

    def test_rails_does_not_import_runtime_or_soft(self):
        import ast

        tree = ast.parse((_ROOT / "core/v2/eval/rails.py").read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        assert not any(m.startswith("core.v2.runtime") for m in mods)
        assert "core.v2.eval.metrics_soft" not in mods  # 避免循环依赖
        assert "core.v2.eval.metrics_hard" not in mods

    def test_gold_only_bc01_changed(self, suite):
        """裁决 D4/D13：本轮只改 bc_01 锚点，其余正式 Gold 与 demo fixture 均未被触碰"""
        assert suite.golds["bc_01_login"].gold_version == "gold-v0.2"
        for cid, case in suite.cases.items():
            if cid == "bc_01_login" or case.benchmark_version != "bench-v0.1":
                continue
            assert suite.golds[cid].gold_version == "gold-v0.1", cid
        assert suite.golds["bc_demo_login"].gold_version == "gold-v0"  # demo 隔离，未被顺带升级
        req = normalize_text(
            (_BC01 / "cases" / suite.cases["bc_01_login"].requirement_file).read_text(encoding="utf-8")
        )
        for sc in suite.golds["bc_01_login"].critical_scenarios:
            for a in [*sc.expected_actions, *sc.expected_outcomes]:
                assert normalize_text(a) in req, f"锚点非需求原文逐字子串：{a}"


# ============================================================
# Step B 报告面守卫（裁决 D1：三项必须同时展示 + 每个 AUTO_HIT 带 via）
# ============================================================


class TestStepBReportSurface:
    """Step B 新增面的机器化守卫（此前只在 rails 层断言，报告层/payload 层无守卫）。

    裁决 2 的两条硬要求：
      ① 报告必须同时展示 requirement_coverage / identity_match_rate / via_counts；
      ② 禁止把 identity_match_rate=0 解读为 requirement coverage=0。
    """

    def _bridge_case(self):
        """identity 必然落空、bridge 必然命中的夹具（Gold 与 runtime 措辞不同 → 指纹不同）"""
        item = _item("手机号必填且为 11 位")
        ob = _ob(item.id)
        tp = _tp([item.id], obligation_id=ob.id, technique=Technique.BOUNDARY_VALUE)
        tc = _tc([tp.id])
        arts = EvalArtifacts(items=[item], test_points=[tp], test_cases=[tc], obligations=[ob])
        arts.obligation_covered_points = {ob.id: [tp.id]}
        gold = _gold(
            grs=[_gr("gr-phone", "手机号必填，须为 11 位中国大陆号码")],
            obligations=[
                {
                    "target": "phone.length",
                    "technique": "boundary_value",
                    "description": "d",
                    "requirement_gold_id": "gr-phone",
                    "min_covered_points": 1,
                }
            ],
        )
        return gold, arts

    def _report(self, gold, arts, status=BenchmarkRunStatus.COMPLETED):
        obs = RunObservation(case_id="bc_rails", gold=gold, status=status, run_id=_RUN, artifacts=arts)
        return evaluate_hard_metrics(obs)

    def test_report_shows_three_required_outputs(self):
        """裁决 2 ①：三项同时出现在 HardMetricsReport 上，且都可序列化进 payload"""
        from core.v2.eval import runner_lib as rl

        gold, arts = self._bridge_case()
        rep = self._report(gold, arts)
        assert rep.requirement_coverage.value is not None
        assert rep.identity_match_rate.value is not None
        assert isinstance(rep.via_counts, dict) and rep.via_counts
        assert "identity_match_rate" in rl._HARD_CELLS
        payload = rl.hard_to_payload(rep)
        assert "requirement_coverage" in payload["metrics"]
        assert "identity_match_rate" in payload["metrics"]
        assert payload["via_counts"] == rep.via_counts
        assert payload["identity_diagnostics"] == rep.identity_diagnostics
        assert payload["anchor_scope"] == "all_non_archived_testcases"  # 裁决 D9 口径落盘

    def test_identity_zero_does_not_imply_coverage_zero(self):
        """裁决 2 ②：identity_match_rate=0 时 requirement_coverage 仍必须 >0（机器化禁止误读）"""
        gold, arts = self._bridge_case()
        rep = self._report(gold, arts)
        assert rep.identity_match_rate.value == 0.0
        assert rep.identity_match_rate.numerator == 0
        assert rep.requirement_coverage.value == 1.0, "identity=0 被误读为 coverage=0（违反裁决 D1）"
        assert rep.via_counts["identity"] == 0 and rep.via_counts["bridge"] == 1
        # 诊断必须自带禁止误读的说明，避免下游只读数字
        assert "不得解读为需求覆盖为 0" in rep.identity_diagnostics["note"]
        assert "不代表需求覆盖为 0" in rep.identity_match_rate.detail

    def test_every_auto_hit_carries_via(self):
        """裁决 2：每个 AUTO_HIT 必须带 via ∈ {identity, bridge, anchor}；非命中项 via 为空"""
        from core.v2.eval.rails import VIA_VALUES

        gold, arts = self._bridge_case()
        rep = self._report(gold, arts)
        for m in rep.requirement_matches:
            if m.state == ItemMatchState.AUTO_HIT:
                assert m.via in VIA_VALUES, f"{m.gold_id} 的 AUTO_HIT 缺 via 标注"
            else:
                assert m.via == ""
        assert sum(rep.via_counts[v] for v in VIA_VALUES) == sum(
            1 for m in rep.requirement_matches if m.state == ItemMatchState.AUTO_HIT
        )

    def test_payload_roundtrip_preserves_new_fields(self):
        from core.v2.eval import runner_lib as rl

        gold, arts = self._bridge_case()
        rep = self._report(gold, arts)
        back = rl.hard_from_payload(rl.hard_to_payload(rep))
        assert back.identity_match_rate.value == rep.identity_match_rate.value
        assert back.via_counts == rep.via_counts
        assert back.identity_diagnostics == rep.identity_diagnostics
        assert back.anchor_scope == rep.anchor_scope
        assert [m.via for m in back.requirement_matches] == [m.via for m in rep.requirement_matches]

    def test_old_payload_without_new_keys_still_loads(self):
        """S6 形态 runset（无 via / via_counts / identity_diagnostics / 第 8 个 cell）必须仍可加载"""
        from core.v2.eval import runner_lib as rl

        gold, arts = self._bridge_case()
        payload = rl.hard_to_payload(self._report(gold, arts))
        for key in ("via_counts", "identity_diagnostics", "anchor_scope"):
            payload.pop(key)
        payload["metrics"].pop("identity_match_rate")
        for m in payload["requirement_matches"]:
            m.pop("via")
        back = rl.hard_from_payload(payload)  # 不得抛异常
        assert back.requirement_coverage.value == 1.0
        assert all(m.via == "" for m in back.requirement_matches)
        assert back.via_counts == {} and back.identity_diagnostics == {} and back.anchor_scope == ""
        assert back.identity_match_rate.value is None  # 缺项 → None，不伪造 0

    def test_non_completed_nulls_identity_match_rate(self):
        """非 COMPLETED 时新诊断单元同样不进质量分母（与其余 7 个 cell 一致）"""
        gold, arts = self._bridge_case()
        rep = self._report(gold, arts, status=BenchmarkRunStatus.LLM_FAILURE)
        assert rep.quality_evaluated is False
        assert rep.identity_match_rate.value is None
        assert rep.requirement_coverage.value is None
        assert rep.via_counts == {} and rep.identity_diagnostics == {}

    @pytest.mark.parametrize("label", ["deepseek", "qwen"])
    def test_real_smoke_report_level(self, label, suite):
        """两个真实 smoke 库在**报告层**的回归对照基线（非质量目标、非模型能力评分）"""
        db, rid = _SMOKES[label]
        if not db.exists():
            pytest.skip(f"smoke 库不存在：{db.name}")
        gold = suite.golds["bc_01_login"]
        _, bundle = _load_rail(db, rid, gold)
        rep = self._report(gold, bundle.artifacts)
        assert rep.requirement_coverage.value == 0.8
        assert rep.identity_match_rate.value == 0.0  # 两模型 statement 逐字率均为 0
        assert rep.via_counts["identity"] == 0 and rep.via_counts["bridge"] == 5 and rep.via_counts["anchor"] == 3
        assert rep.anchor_scope == "all_non_archived_testcases"
        assert rep.requirement_coverage.value > 0, "identity=0 不得被读成 coverage=0（裁决 D1）"
