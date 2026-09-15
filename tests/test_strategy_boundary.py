"""V2 Step 4 边界值策略测试 - FieldSpec.min/max_value + min/max_length → 六点边界。"""

from core.schemas import (
    DataType,
    FieldSpec,
    GenerationScope,
    Priority,
    Provenance,
    RequirementItem,
    RequirementItemType,
    Technique,
    TestDimension,
)
from core.v2.strategy.boundary import (
    _classify_point,
    _compute_boundary_points,
    derive_boundary_obligations,
    derive_boundary_testpoints,
)

VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
ITEM_ID = "01ARZ3NDEKTSV4RRFFQ69G5FC0"


def _make_item(fields: list[FieldSpec], priority_hint: Priority | None = None) -> RequirementItem:
    return RequirementItem(
        id=ITEM_ID,
        version_id=VERSION_ID,
        seq=1,
        type=RequirementItemType.DATA_FIELD,
        module="用户注册",
        statement="用户注册字段约束",
        fields=fields,
        priority_hint=priority_hint,
        confidence=0.9,
    )


# ============================================================
# _compute_boundary_points 边界点计算
# ============================================================


class TestComputeBoundaryPoints:
    def test_min_and_max_both_present(self):
        """min=1, max=100 → 6 点：0,1,2,99,100,101"""
        pts = _compute_boundary_points(1, 100)
        assert pts == [0, 1, 2, 99, 100, 101]

    def test_only_min(self):
        """min=1 → 3 点：0,1,2"""
        pts = _compute_boundary_points(1, None)
        assert pts == [0, 1, 2]

    def test_only_max(self):
        """max=100 → 3 点：99,100,101"""
        pts = _compute_boundary_points(None, 100)
        assert pts == [99, 100, 101]

    def test_min_equals_max(self):
        """min=max=5 → 3 点（去重后）：4,5,6"""
        pts = _compute_boundary_points(5, 5)
        assert pts == [4, 5, 6]

    def test_adjacent_boundaries_dedup(self):
        """min=1, max=2 → 4 点（重合去重）：0,1,2,3"""
        pts = _compute_boundary_points(1, 2)
        assert pts == [0, 1, 2, 3]

    def test_float_values(self):
        """float 也按步长 1 处理（首批保守策略，见 plan Q1）"""
        pts = _compute_boundary_points(0.5, 10.5)
        assert pts == [-0.5, 0.5, 1.5, 9.5, 10.5, 11.5]

    def test_no_boundaries(self):
        pts = _compute_boundary_points(None, None)
        assert pts == []


# ============================================================
# _classify_point 边界点分类
# ============================================================


class TestClassifyPoint:
    def test_below_min(self):
        assert _classify_point(0, 1, 100) == "below"

    def test_at_min(self):
        assert _classify_point(1, 1, 100) == "at_min"

    def test_inside(self):
        assert _classify_point(2, 1, 100) == "inside"
        assert _classify_point(99, 1, 100) == "inside"

    def test_at_max(self):
        assert _classify_point(100, 1, 100) == "at_max"

    def test_above_max(self):
        assert _classify_point(101, 1, 100) == "above"

    def test_only_min_no_max(self):
        assert _classify_point(0, 1, None) == "below"
        assert _classify_point(1, 1, None) == "at_min"
        assert _classify_point(5, 1, None) == "inside"

    def test_only_max_no_min(self):
        assert _classify_point(101, None, 100) == "above"
        assert _classify_point(100, None, 100) == "at_max"
        assert _classify_point(50, None, 100) == "inside"


# ============================================================
# derive_boundary_obligations 义务派生
# ============================================================


class TestDeriveBoundaryObligations:
    def test_min_max_value_derives_one_obligation(self):
        item = _make_item([FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)])
        obs = derive_boundary_obligations(item, RUN_ID)
        assert len(obs) == 1
        ob = obs[0]
        assert ob.technique == Technique.BOUNDARY_VALUE
        assert ob.target == "age"
        assert ob.item_id == ITEM_ID
        assert ob.run_id == RUN_ID
        assert ob.params["kind"] == "value"
        assert ob.params["min"] == 1
        assert ob.params["max"] == 100
        assert ob.params["points"] == [0, 1, 2, 99, 100, 101]
        assert ob.params["label"] == "年龄"

    def test_min_max_length_derives_one_obligation(self):
        item = _make_item(
            [FieldSpec(name="username", label="用户名", data_type=DataType.STRING, min_length=3, max_length=20)]
        )
        obs = derive_boundary_obligations(item, RUN_ID)
        assert len(obs) == 1
        ob = obs[0]
        assert ob.target == "username.length"
        assert ob.params["kind"] == "length"
        assert ob.params["points"] == [2, 3, 4, 19, 20, 21]

    def test_value_and_length_both_present_derives_two_obligations(self):
        """数值与长度语义不同，派生两个独立 obligation"""
        item = _make_item(
            [
                FieldSpec(
                    name="price",
                    label="价格",
                    data_type=DataType.FLOAT,
                    min_value=0.01,
                    max_value=99999.99,
                    min_length=1,
                    max_length=10,
                )
            ]
        )
        obs = derive_boundary_obligations(item, RUN_ID)
        assert len(obs) == 2
        kinds = {ob.params["kind"] for ob in obs}
        assert kinds == {"value", "length"}
        targets = {ob.target for ob in obs}
        assert targets == {"price", "price.length"}

    def test_only_min_value(self):
        item = _make_item([FieldSpec(name="count", label="数量", data_type=DataType.INT, min_value=1)])
        obs = derive_boundary_obligations(item, RUN_ID)
        assert len(obs) == 1
        assert obs[0].params["points"] == [0, 1, 2]
        assert obs[0].params["max"] is None

    def test_only_max_value(self):
        item = _make_item([FieldSpec(name="count", label="数量", data_type=DataType.INT, max_value=100)])
        obs = derive_boundary_obligations(item, RUN_ID)
        assert len(obs) == 1
        assert obs[0].params["points"] == [99, 100, 101]
        assert obs[0].params["min"] is None

    def test_no_boundaries_no_obligation(self):
        """FieldSpec 无任何 min/max → 不派生 obligation"""
        item = _make_item([FieldSpec(name="nickname", label="昵称", data_type=DataType.STRING)])
        obs = derive_boundary_obligations(item, RUN_ID)
        assert obs == []

    def test_multiple_fields(self):
        item = _make_item(
            [
                FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100),
                FieldSpec(name="username", label="用户名", data_type=DataType.STRING, min_length=3, max_length=20),
                FieldSpec(name="nickname", label="昵称", data_type=DataType.STRING),  # 无边界，跳过
            ]
        )
        obs = derive_boundary_obligations(item, RUN_ID)
        assert len(obs) == 2
        targets = {ob.target for ob in obs}
        assert targets == {"age", "username.length"}

    def test_no_fields_returns_empty(self):
        item = RequirementItem(
            id=ITEM_ID,
            version_id=VERSION_ID,
            seq=1,
            type=RequirementItemType.FUNCTION,
            module="m",
            statement="s",
            confidence=0.9,
        )
        assert derive_boundary_obligations(item, RUN_ID) == []

    def test_label_fallback_to_name(self):
        """FieldSpec.label 为空时，params.label 兜底为 name"""
        item = _make_item([FieldSpec(name="age", label="", data_type=DataType.INT, min_value=1, max_value=100)])
        obs = derive_boundary_obligations(item, RUN_ID)
        assert obs[0].params["label"] == "age"


# ============================================================
# derive_boundary_testpoints TestPoint 派生（1:N）
# ============================================================


class TestDeriveBoundaryTestpoints:
    def _make_ob_and_item(self, **field_kwargs):
        item = _make_item([FieldSpec(name="age", label="年龄", data_type=DataType.INT, **field_kwargs)])
        obs = derive_boundary_obligations(item, RUN_ID)
        return obs[0], item

    def test_six_points_derived(self):
        ob, item = self._make_ob_and_item(min_value=1, max_value=100)
        tps = derive_boundary_testpoints(ob, item)
        assert len(tps) == 6
        titles = [tp.title for tp in tps]
        assert titles == [
            "年龄 数值边界 0",
            "年龄 数值边界 1",
            "年龄 数值边界 2",
            "年龄 数值边界 99",
            "年龄 数值边界 100",
            "年龄 数值边界 101",
        ]

    def test_testpoint_hard_constraints(self):
        """Step 4 硬性约束：provenance=STRATEGY / technique=BOUNDARY_VALUE / obligation_id 回链 / scope=STRATEGY"""
        ob, item = self._make_ob_and_item(min_value=1, max_value=100)
        tps = derive_boundary_testpoints(ob, item)
        for tp in tps:
            assert tp.provenance == Provenance.STRATEGY
            assert tp.technique == Technique.BOUNDARY_VALUE
            assert tp.obligation_id == ob.id
            assert tp.generation_scope == GenerationScope.STRATEGY
            assert tp.dimension == TestDimension.BOUNDARY
            assert tp.subcategory == "边界值"
            assert tp.module == item.module
            assert tp.item_ids == [item.id]
            assert tp.version_id == VERSION_ID
            assert tp.run_id == RUN_ID

    def test_priority_from_item_hint(self):
        item = _make_item(
            [FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)],
            priority_hint=Priority.P0,
        )
        ob = derive_boundary_obligations(item, RUN_ID)[0]
        tps = derive_boundary_testpoints(ob, item)
        assert all(tp.priority == Priority.P0 for tp in tps)

    def test_priority_defaults_to_p1(self):
        ob, item = self._make_ob_and_item(min_value=1, max_value=100)
        # item.priority_hint 默认 None
        tps = derive_boundary_testpoints(ob, item)
        assert all(tp.priority == Priority.P1 for tp in tps)

    def test_description_contains_expectation(self):
        """越界点描述含"预期被拒绝"，合法边界含"预期被接受" """
        ob, item = self._make_ob_and_item(min_value=1, max_value=100)
        tps = derive_boundary_testpoints(ob, item)
        # 按 points 顺序 [0,1,2,99,100,101]
        assert "预期被拒绝" in tps[0].description  # 0 < min → below
        assert "预期被接受" in tps[1].description  # 1 == min → at_min
        assert "预期被接受" in tps[2].description  # 2 → inside
        assert "预期被接受" in tps[3].description  # 99 → inside
        assert "预期被接受" in tps[4].description  # 100 == max → at_max
        assert "预期被拒绝" in tps[5].description  # 101 > max → above

    def test_length_boundary_titles(self):
        item = _make_item(
            [FieldSpec(name="username", label="用户名", data_type=DataType.STRING, min_length=3, max_length=20)]
        )
        ob = derive_boundary_obligations(item, RUN_ID)[0]
        tps = derive_boundary_testpoints(ob, item)
        assert len(tps) == 6
        assert tps[0].title == "用户名 长度边界 2"
        assert tps[-1].title == "用户名 长度边界 21"

    def test_wrong_technique_returns_empty(self):
        """非 BOUNDARY_VALUE obligation 传入 → 返回空（防御性）"""
        from core.schemas import CoverageObligation

        ob = CoverageObligation(
            run_id=RUN_ID,
            item_id=ITEM_ID,
            technique=Technique.EQUIVALENCE_CLASS,
            target="x",
            description="d",
            params={},
        )
        item = _make_item([FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1)])
        assert derive_boundary_testpoints(ob, item) == []

    def test_wrong_kind_returns_empty(self):
        """params.kind 非 value/length → 返回空（防御性）"""
        from core.schemas import CoverageObligation

        ob = CoverageObligation(
            run_id=RUN_ID,
            item_id=ITEM_ID,
            technique=Technique.BOUNDARY_VALUE,
            target="x",
            description="d",
            params={"field": "x", "kind": "unknown", "points": [1, 2]},
        )
        item = _make_item([])
        assert derive_boundary_testpoints(ob, item) == []

    def test_three_points_when_only_min(self):
        ob, item = self._make_ob_and_item(min_value=1)
        tps = derive_boundary_testpoints(ob, item)
        assert len(tps) == 3
        assert [tp.title for tp in tps] == ["年龄 数值边界 0", "年龄 数值边界 1", "年龄 数值边界 2"]


# ============================================================
# strategy_params 结构化参数（Step 4 修订：fingerprint 区分同 obligation 下多个点）
# ============================================================


class TestStrategyParams:
    def _make_ob_and_item(self, **field_kwargs):
        item = _make_item([FieldSpec(name="age", label="年龄", data_type=DataType.INT, **field_kwargs)])
        obs = derive_boundary_obligations(item, RUN_ID)
        return obs[0], item

    def test_strategy_params_structure_six_points(self):
        """min=1, max=100 → 6 个点的 strategy_params 结构完整且互不相同"""
        ob, item = self._make_ob_and_item(min_value=1, max_value=100)
        tps = derive_boundary_testpoints(ob, item)
        assert len(tps) == 6
        expected_types = ["min_minus_1", "min", "min_plus_1", "max_minus_1", "max", "max_plus_1"]
        expected_values = [0, 1, 2, 99, 100, 101]
        for tp, btype, val in zip(tps, expected_types, expected_values, strict=True):
            assert tp.strategy_params is not None
            assert tp.strategy_params["boundary_type"] == btype
            assert tp.strategy_params["value"] == val
            assert tp.strategy_params["kind"] == "value"

    def test_strategy_params_unique_per_point(self):
        """同一 obligation 下 6 个点的 strategy_params 互不相同（fingerprint 区分依据）"""
        ob, item = self._make_ob_and_item(min_value=1, max_value=100)
        tps = derive_boundary_testpoints(ob, item)
        # 用 canonical 序列化后比较
        from core.v2.fingerprint import canonical_strategy_params

        canonical = [canonical_strategy_params(tp.strategy_params) for tp in tps]
        assert len(set(canonical)) == 6, "6 个点的 strategy_params 必须互不相同"

    def test_strategy_params_kind_length(self):
        """长度边界的 strategy_params.kind = 'length'"""
        item = _make_item(
            [FieldSpec(name="username", label="用户名", data_type=DataType.STRING, min_length=3, max_length=20)]
        )
        ob = derive_boundary_obligations(item, RUN_ID)[0]
        tps = derive_boundary_testpoints(ob, item)
        assert all(tp.strategy_params["kind"] == "length" for tp in tps)
        expected_types = ["min_minus_1", "min", "min_plus_1", "max_minus_1", "max", "max_plus_1"]
        assert [tp.strategy_params["boundary_type"] for tp in tps] == expected_types

    def test_strategy_params_three_points_only_min(self):
        """只有 min → 3 个点的 boundary_type 为 min_minus_1/min/min_plus_1"""
        ob, item = self._make_ob_and_item(min_value=1)
        tps = derive_boundary_testpoints(ob, item)
        assert [tp.strategy_params["boundary_type"] for tp in tps] == ["min_minus_1", "min", "min_plus_1"]

    def test_strategy_params_three_points_only_max(self):
        """只有 max → 3 个点的 boundary_type 为 max_minus_1/max/max_plus_1"""
        ob, item = self._make_ob_and_item(max_value=100)
        tps = derive_boundary_testpoints(ob, item)
        assert [tp.strategy_params["boundary_type"] for tp in tps] == ["max_minus_1", "max", "max_plus_1"]


# ============================================================
# min ≤ 0 异常输入合理性提示（Step 4 修订：第一版简单规则）
# ============================================================


class TestNegativeBoundaryWarning:
    def _make_ob_and_item(self, **field_kwargs):
        item = _make_item([FieldSpec(name="count", label="数量", data_type=DataType.INT, **field_kwargs)])
        obs = derive_boundary_obligations(item, RUN_ID)
        return obs[0], item

    def test_min_zero_warns_on_minus_one(self):
        """min=0 → min-1=-1 点带 warn 标记 + description 提示"""
        ob, item = self._make_ob_and_item(min_value=0, max_value=10)
        tps = derive_boundary_testpoints(ob, item)
        # 第一个点是 min_minus_1 = -1
        tp_minus_1 = tps[0]
        assert tp_minus_1.strategy_params["boundary_type"] == "min_minus_1"
        assert tp_minus_1.strategy_params["value"] == -1
        assert tp_minus_1.strategy_params.get("warn") == "negative_value_may_be_invalid"
        assert "可能为负数" in tp_minus_1.description or "人工确认" in tp_minus_1.description

    def test_min_negative_warns_on_minus_one(self):
        """min=-5 → min-1=-6 点带 warn 标记"""
        ob, item = self._make_ob_and_item(min_value=-5, max_value=5)
        tps = derive_boundary_testpoints(ob, item)
        tp_minus_1 = next(tp for tp in tps if tp.strategy_params["boundary_type"] == "min_minus_1")
        assert tp_minus_1.strategy_params["value"] == -6
        assert tp_minus_1.strategy_params.get("warn") == "negative_value_may_be_invalid"

    def test_min_positive_no_warn(self):
        """min=1 → min-1=0 点无 warn（0 通常业务合理）"""
        ob, item = self._make_ob_and_item(min_value=1, max_value=100)
        tps = derive_boundary_testpoints(ob, item)
        tp_minus_1 = next(tp for tp in tps if tp.strategy_params["boundary_type"] == "min_minus_1")
        assert tp_minus_1.strategy_params["value"] == 0
        assert "warn" not in tp_minus_1.strategy_params

    def test_warn_only_on_min_minus_1_not_others(self):
        """warn 只标记在 min_minus_1 点上，其他 5 点无 warn"""
        ob, item = self._make_ob_and_item(min_value=0, max_value=10)
        tps = derive_boundary_testpoints(ob, item)
        warned = [tp for tp in tps if tp.strategy_params.get("warn")]
        assert len(warned) == 1
        assert warned[0].strategy_params["boundary_type"] == "min_minus_1"

    def test_length_min_zero_warns(self):
        """长度边界 min_length=0 → min-1=-1 也带 warn（长度不可为负）"""
        item = _make_item(
            [FieldSpec(name="nickname", label="昵称", data_type=DataType.STRING, min_length=0, max_length=20)]
        )
        ob = derive_boundary_obligations(item, RUN_ID)[0]
        tps = derive_boundary_testpoints(ob, item)
        tp_minus_1 = next(tp for tp in tps if tp.strategy_params["boundary_type"] == "min_minus_1")
        assert tp_minus_1.strategy_params["value"] == -1
        assert tp_minus_1.strategy_params.get("warn") == "negative_value_may_be_invalid"
