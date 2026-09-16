"""V2 Step 7 Hard Review Engine 测试 - coverage 双指标 / duplication 两级 / consistency / executability 结构层。

review_hard 是纯代码（无 DB 依赖），故用直接构造的对象做单元测试。含门槛 12 的确定性稳定性验证。
"""

from core.schemas import (
    DataType,
    DuplicateLevel,
    FieldSpec,
    GenerationScope,
    Priority,
    Provenance,
    RequirementItem,
    RequirementItemType,
    ReviewDimension,
    TargetType,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestDimension,
    TestPoint,
    TestStep,
)
from core.v2.review_hard import (
    compute_consistency,
    compute_coverage,
    compute_duplication,
    compute_executability_structural,
    run_hard_review,
)

RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
ITEM1 = "01ARZ3NDEKTSV4RRFFQ69G5FC1"
ITEM2 = "01ARZ3NDEKTSV4RRFFQ69G5FD2"


def _tc(
    display_id,
    title,
    actions,
    *,
    expected="预期结果",
    module="登录",
    content_hash=None,
    status=TestCaseStatus.VALIDATED,
) -> TestCase:
    steps = [TestStep(seq=i + 1, action=a, expected="ok") for i, a in enumerate(actions)]
    return TestCase(
        run_id=RUN_ID,
        display_id=display_id,
        module=module,
        title=title,
        precondition="",
        steps=steps,
        expected=expected,
        priority=Priority.P1,
        type=TestCaseType.FUNCTIONAL,
        content_hash=content_hash,
        status=status,
    )


def _tp(item_ids, title="TP", dimension=TestDimension.FUNCTIONAL) -> TestPoint:
    return TestPoint(
        run_id=RUN_ID,
        version_id=VERSION_ID,
        item_ids=item_ids,
        module="登录",
        subcategory="校验",
        title=title,
        description="d",
        dimension=dimension,
        priority=Priority.P1,
        provenance=Provenance.LLM,
        generation_scope=GenerationScope.ITEM,
    )


def _item(item_id, statement="需求项", fields=None) -> RequirementItem:
    return RequirementItem(
        id=item_id,
        version_id=VERSION_ID,
        seq=1,
        type=RequirementItemType.DATA_FIELD,
        module="登录",
        statement=statement,
        fields=fields or [],
    )


# ============================================================
# coverage 双指标（门槛 2）
# ============================================================


class TestCoverage:
    def test_full_coverage(self):
        items = [_item(ITEM1), _item(ITEM2)]
        tps = [_tp([ITEM1]), _tp([ITEM2])]
        score, detail, findings, reason = compute_coverage(tps, items, strategy_coverage=1.0)
        assert score == 100.0
        assert detail.requirement_item_coverage == 1.0
        assert detail.strategy_obligation_coverage == 1.0
        assert detail.uncovered_item_ids == []
        assert findings == []

    def test_partial_coverage_dual_metrics(self):
        """门槛 2：双指标保留 + uncovered_item_ids 定位缺口"""
        items = [_item(ITEM1), _item(ITEM2)]
        tps = [_tp([ITEM1])]  # 只覆盖 ITEM1
        score, detail, findings, reason = compute_coverage(tps, items, strategy_coverage=1.0)
        assert detail.requirement_item_coverage == 0.5
        assert detail.strategy_obligation_coverage == 1.0  # 双指标分离，strategy 不被 item 稀释
        assert detail.uncovered_item_ids == [ITEM2]
        assert score == 50.0  # = item_coverage × 100
        # 未覆盖 item 产 COVERAGE finding，target=REQUIREMENT_ITEM
        assert len(findings) == 1
        assert findings[0].dimension == ReviewDimension.COVERAGE
        assert findings[0].target_type == TargetType.REQUIREMENT_ITEM
        assert findings[0].target_id == ITEM2
        assert findings[0].provenance == Provenance.VALIDATOR

    def test_strategy_and_item_coverage_independent(self):
        """strategy=1.0 但 item=0.5，两者独立报告（不合成模糊分）"""
        items = [_item(ITEM1), _item(ITEM2)]
        tps = [_tp([ITEM1])]
        _, detail, _, _ = compute_coverage(tps, items, strategy_coverage=1.0)
        assert detail.strategy_obligation_coverage == 1.0
        assert detail.requirement_item_coverage == 0.5


# ============================================================
# duplication 两级（门槛 4）
# ============================================================


class TestDuplication:
    def test_no_duplicate(self):
        cases = [
            _tc("TC_001", "登录成功", ["输入账号", "点击登录"], content_hash="h1"),
            _tc("TC_002", "登出", ["点击登出"], content_hash="h2"),
        ]
        score, findings, reason = compute_duplication(cases)
        assert score == 100.0
        assert findings == []

    def test_exact_duplicate_by_content_hash(self):
        """门槛 4：content_hash 相同 → EXACT_DUPLICATE + auto_fixable"""
        cases = [
            _tc("TC_001", "登录成功", ["输入账号", "点击登录"], content_hash="SAME"),
            _tc("TC_002", "登录成功", ["输入账号", "点击登录"], content_hash="SAME"),
        ]
        score, findings, reason = compute_duplication(cases)
        assert len(findings) == 1
        f = findings[0]
        assert f.dimension == ReviewDimension.DUPLICATION
        assert f.detail["duplicate_level"] == DuplicateLevel.EXACT.value
        assert f.detail["similarity"] == 1.0
        assert f.detail["counterpart_id"] == cases[0].id
        assert f.auto_fixable is True  # 标记可修，但 Step 7 不删
        assert f.provenance == Provenance.VALIDATOR
        assert score < 100.0

    def test_semantic_duplicate_by_similarity(self):
        """门槛 4：content_hash 不同但语义高度相似 → SEMANTIC_DUPLICATE + similarity"""
        cases = [
            _tc("TC_001", "验证手机号为空时无法登录", ["打开登录页", "手机号留空", "点击登录"], content_hash="h1"),
            _tc("TC_002", "验证手机号为空时不能登录", ["打开登录页", "手机号留空", "点击登录按钮"], content_hash="h2"),
        ]
        score, findings, reason = compute_duplication(cases, threshold=0.8)
        assert len(findings) == 1
        f = findings[0]
        assert f.detail["duplicate_level"] == DuplicateLevel.SEMANTIC.value
        assert 0.8 <= f.detail["similarity"] < 1.0
        assert f.auto_fixable is True

    def test_dissimilar_not_flagged(self):
        cases = [
            _tc("TC_001", "登录成功", ["输入账号", "点击登录"], content_hash="h1"),
            _tc("TC_002", "修改密码复杂度校验", ["进入设置", "输入新密码"], content_hash="h2"),
        ]
        _, findings, _ = compute_duplication(cases)
        assert findings == []


# ============================================================
# consistency（门槛：格式/枚举/命名）
# ============================================================


class TestConsistency:
    def test_all_consistent(self):
        cases = [_tc("TC_001", "登录", ["a"]), _tc("TC_002", "登出", ["b"])]
        score, findings, reason = compute_consistency(cases)
        assert score == 100.0
        assert findings == []

    def test_bad_display_id_format(self):
        cases = [_tc("BAD_ID", "登录", ["a"])]
        score, findings, _ = compute_consistency(cases)
        assert len(findings) == 1
        assert findings[0].dimension == ReviewDimension.CONSISTENCY
        assert "display_id" in findings[0].detail["violation"][0]
        assert score < 100.0

    def test_duplicate_display_id(self):
        cases = [_tc("TC_001", "登录", ["a"]), _tc("TC_001", "登出", ["b"])]
        _, findings, _ = compute_consistency(cases)
        assert any("重复" in f.issue for f in findings)

    def test_empty_module(self):
        cases = [_tc("TC_001", "登录", ["a"], module="")]
        _, findings, _ = compute_consistency(cases)
        assert any("module 为空" in f.issue for f in findings)


# ============================================================
# executability 结构层
# ============================================================


class TestExecutabilityStructural:
    def test_all_wellformed(self):
        cases = [_tc("TC_001", "登录", ["输入账号", "点击登录"], expected="登录成功")]
        score, findings, reason = compute_executability_structural(cases)
        assert score == 100.0
        assert findings == []

    def test_empty_expected(self):
        cases = [_tc("TC_001", "登录", ["输入账号"], expected="")]
        score, findings, _ = compute_executability_structural(cases)
        assert len(findings) == 1
        assert findings[0].dimension == ReviewDimension.EXECUTABILITY
        assert findings[0].detail["layer"] == "structural"
        assert score < 100.0

    def test_empty_steps(self):
        cases = [_tc("TC_001", "登录", [], expected="ok")]
        _, findings, _ = compute_executability_structural(cases)
        assert any("steps 为空" in f.detail["violation"] for f in findings)


# ============================================================
# 汇总 + 确定性稳定（门槛 5/12）
# ============================================================


class TestRunHardReview:
    def test_aggregates_all_dimensions(self):
        items = [_item(ITEM1), _item(ITEM2)]
        tps = [_tp([ITEM1])]  # ITEM2 未覆盖
        cases = [_tc("TC_001", "登录", ["a"], content_hash="h1")]
        r = run_hard_review(cases, tps, items, strategy_obligation_coverage=1.0)
        assert r.coverage_detail is not None
        assert r.coverage_detail.uncovered_item_ids == [ITEM2]
        # 硬维度 reason 齐全
        for dim in ("coverage", "duplication", "consistency", "executability_structural"):
            assert dim in r.dimension_reasons
        # 所有 finding provenance=validator（门槛 5）
        assert all(f.provenance == Provenance.VALIDATOR for f in r.findings)

    def test_deterministic_stability(self):
        """门槛 12：同一输入多次运行硬指标完全一致"""
        items = [_item(ITEM1), _item(ITEM2)]
        tps = [_tp([ITEM1])]
        cases = [_tc("TC_001", "登录", ["a"], content_hash="h1"), _tc("TC_002", "登录", ["a"], content_hash="h1")]
        r1 = run_hard_review(cases, tps, items, strategy_obligation_coverage=1.0)
        r2 = run_hard_review(cases, tps, items, strategy_obligation_coverage=1.0)
        r3 = run_hard_review(cases, tps, items, strategy_obligation_coverage=1.0)
        assert (
            (r1.coverage_score, r1.duplication_score, r1.consistency_score, r1.executability_structural_score)
            == (r2.coverage_score, r2.duplication_score, r2.consistency_score, r2.executability_structural_score)
            == (r3.coverage_score, r3.duplication_score, r3.consistency_score, r3.executability_structural_score)
        )
        assert len(r1.findings) == len(r2.findings) == len(r3.findings)

    def test_coverage_fieldspec_item(self):
        """带 FieldSpec 的 item 也能正常算覆盖"""
        items = [
            _item(
                ITEM1, fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)]
            )
        ]
        tps = [_tp([ITEM1], dimension=TestDimension.BOUNDARY)]
        cases = [_tc("TC_001", "年龄边界", ["输入年龄"])]
        r = run_hard_review(cases, tps, items, strategy_obligation_coverage=1.0)
        assert r.coverage_score == 100.0
