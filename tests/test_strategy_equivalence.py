"""V2 Step 4 等价类策略测试 - FieldSpec 6 种属性 → 抽象类标识（不含真实数据）。

★ Step 4 修订：Strategy Engine 不生成真实测试数据，只产出抽象类标识；
  具体数据由 Step 5 TestCase 合成阶段的 TestDataGenerator 生成。
"""

import pytest

from core.schemas import (
    CoverageObligation,
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
from core.v2.strategy.equivalence import (
    derive_equivalence_obligations,
    derive_equivalence_testpoints,
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
        statement="字段约束",
        fields=fields,
        priority_hint=priority_hint,
        confidence=0.9,
    )


def _find_ob(obs: list[CoverageObligation], kind: str) -> CoverageObligation | None:
    for ob in obs:
        if ob.params.get("kind") == kind:
            return ob
    return None


# ============================================================
# derive_equivalence_obligations 义务派生
# ============================================================


class TestDeriveEquivalenceObligations:
    def test_enum_values_derives_obligation(self):
        item = _make_item(
            [
                FieldSpec(
                    name="status",
                    label="状态",
                    data_type=DataType.ENUM,
                    enum_values=["active", "disabled"],
                    nullable=True,
                )
            ]
        )
        obs = derive_equivalence_obligations(item, RUN_ID)
        assert len(obs) == 1
        ob = obs[0]
        assert ob.technique == Technique.EQUIVALENCE_CLASS
        assert ob.target == "status.enum"
        assert ob.params["kind"] == "enum"
        assert ob.params["enum_values"] == ["active", "disabled"]
        # ★ 修订后 params 不含 invalid_class（TestPoint 层面用 strategy_params.class 表达）
        assert "invalid_class" not in ob.params

    def test_pattern_derives_obligation_no_examples(self):
        """★ 修订后 pattern obligation 不含 valid_example/invalid_example（真实数据由 Step 5 生成）"""
        item = _make_item([FieldSpec(name="phone", label="手机号", data_type=DataType.STRING, pattern=r"^\d{11}$")])
        obs = derive_equivalence_obligations(item, RUN_ID)
        ob = _find_ob(obs, "pattern")
        assert ob is not None
        assert ob.target == "phone.pattern"
        assert ob.params["pattern"] == r"^\d{11}$"
        assert "valid_example" not in ob.params
        assert "invalid_example" not in ob.params

    def test_required_derives_obligation(self):
        item = _make_item([FieldSpec(name="age", label="年龄", data_type=DataType.INT, required=True, nullable=True)])
        obs = derive_equivalence_obligations(item, RUN_ID)
        ob = _find_ob(obs, "required")
        assert ob is not None
        assert ob.target == "age.required"
        assert "invalid_class" not in ob.params

    def test_nullable_false_derives_obligation(self):
        item = _make_item([FieldSpec(name="age", label="年龄", data_type=DataType.INT, required=False, nullable=False)])
        obs = derive_equivalence_obligations(item, RUN_ID)
        ob = _find_ob(obs, "nullable")
        assert ob is not None
        assert ob.target == "age.nullable"

    def test_nullable_true_no_obligation(self):
        item = _make_item(
            [FieldSpec(name="remark", label="备注", data_type=DataType.STRING, required=False, nullable=True)]
        )
        obs = derive_equivalence_obligations(item, RUN_ID)
        assert _find_ob(obs, "nullable") is None

    def test_unique_derives_obligation(self):
        item = _make_item([FieldSpec(name="username", label="用户名", data_type=DataType.STRING, unique=True)])
        obs = derive_equivalence_obligations(item, RUN_ID)
        ob = _find_ob(obs, "unique")
        assert ob is not None
        assert ob.target == "username.unique"

    @pytest.mark.parametrize("data_type", [DataType.EMAIL, DataType.PHONE, DataType.URL, DataType.ID_CARD])
    def test_data_type_format_derives_obligation_no_examples(self, data_type):
        """★ 修订后 data_type_format obligation 不含 valid_example/invalid_example"""
        item = _make_item([FieldSpec(name="x", label="X", data_type=data_type)])
        obs = derive_equivalence_obligations(item, RUN_ID)
        ob = _find_ob(obs, "data_type_format")
        assert ob is not None
        assert ob.target == "x.format"
        assert ob.params["data_type"] == data_type.value
        assert "valid_example" not in ob.params
        assert "invalid_example" not in ob.params

    def test_non_format_data_type_no_format_obligation(self):
        item = _make_item([FieldSpec(name="age", label="年龄", data_type=DataType.INT)])
        obs = derive_equivalence_obligations(item, RUN_ID)
        assert _find_ob(obs, "data_type_format") is None

    def test_one_field_multiple_rules_derives_multiple_obligations(self):
        item = _make_item(
            [
                FieldSpec(
                    name="email",
                    label="邮箱",
                    data_type=DataType.EMAIL,
                    required=True,
                    nullable=False,
                    unique=True,
                    pattern=r"^[^@]+@[^@]+$",
                )
            ]
        )
        obs = derive_equivalence_obligations(item, RUN_ID)
        kinds = {ob.params["kind"] for ob in obs}
        assert kinds == {"pattern", "required", "nullable", "unique", "data_type_format"}
        assert len(obs) == 5

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
        assert derive_equivalence_obligations(item, RUN_ID) == []

    def test_bare_field_no_rules_returns_empty(self):
        item = _make_item(
            [FieldSpec(name="nickname", label="昵称", data_type=DataType.STRING, required=False, nullable=True)]
        )
        obs = derive_equivalence_obligations(item, RUN_ID)
        assert obs == []

    def test_obligation_order_is_stable(self):
        item = _make_item(
            [
                FieldSpec(
                    name="email",
                    label="邮箱",
                    data_type=DataType.EMAIL,
                    required=True,
                    nullable=False,
                    unique=True,
                    pattern=r"^[^@]+@[^@]+$",
                    enum_values=["a@b.com", "c@d.com"],
                )
            ]
        )
        obs = derive_equivalence_obligations(item, RUN_ID)
        kinds = [ob.params["kind"] for ob in obs]
        assert kinds == ["enum", "pattern", "required", "nullable", "unique", "data_type_format"]

    def test_label_fallback_to_name(self):
        item = _make_item([FieldSpec(name="status", label="", data_type=DataType.ENUM, enum_values=["a", "b"])])
        obs = derive_equivalence_obligations(item, RUN_ID)
        assert obs[0].params["label"] == "status"

    def test_enum_empty_list_no_obligation(self):
        item = _make_item(
            [FieldSpec(name="status", label="状态", data_type=DataType.ENUM, enum_values=[], nullable=True)]
        )
        obs = derive_equivalence_obligations(item, RUN_ID)
        assert _find_ob(obs, "enum") is None


# ============================================================
# derive_equivalence_testpoints TestPoint 派生（1:N）+ strategy_params
# ============================================================


class TestDeriveEquivalenceTestpoints:
    def _ob_and_item(self, field: FieldSpec, kind: str) -> tuple[CoverageObligation, RequirementItem]:
        item = _make_item([field])
        obs = derive_equivalence_obligations(item, RUN_ID)
        ob = _find_ob(obs, kind)
        assert ob is not None, f"未派生 kind={kind} 的 obligation"
        return ob, item

    def test_enum_derives_n_plus_1_points_with_strategy_params(self):
        ob, item = self._ob_and_item(
            FieldSpec(
                name="status",
                label="状态",
                data_type=DataType.ENUM,
                enum_values=["active", "disabled", "pending"],
                nullable=True,
            ),
            "enum",
        )
        tps = derive_equivalence_testpoints(ob, item)
        assert len(tps) == 4  # 3 合法 + 1 非法
        # 前 3 个是合法枚举值
        for tp, v in zip(tps[:3], ["active", "disabled", "pending"], strict=True):
            assert tp.strategy_params == {"class": "valid_enum_value", "value": v}
            assert tp.title == f"状态 枚举值 {v} 合法"
        # 最后 1 个是非法类
        assert tps[3].strategy_params == {"class": "invalid_enum_value"}
        assert tps[3].title == "状态 枚举外值非法"

    def test_pattern_derives_2_points_abstract_class(self):
        """★ 修订后 description 不含具体数据样例，含 Step 5 提示"""
        ob, item = self._ob_and_item(
            FieldSpec(name="phone", label="手机号", data_type=DataType.STRING, pattern=r"^\d{11}$"),
            "pattern",
        )
        tps = derive_equivalence_testpoints(ob, item)
        assert len(tps) == 2
        assert tps[0].strategy_params == {"class": "valid_pattern"}
        assert tps[1].strategy_params == {"class": "invalid_pattern"}
        assert tps[0].title == "手机号 匹配正则合法"
        assert tps[1].title == "手机号 不匹配正则非法"
        # description 不含具体数据（11111111111 / aaaaaaaaaaa），含 Step 5 提示
        assert "11111111111" not in tps[0].description
        assert "Step 5" in tps[0].description or "TestDataGenerator" in tps[0].description

    def test_required_derives_2_points(self):
        ob, item = self._ob_and_item(
            FieldSpec(name="age", label="年龄", data_type=DataType.INT, required=True, nullable=True),
            "required",
        )
        tps = derive_equivalence_testpoints(ob, item)
        assert len(tps) == 2
        assert tps[0].strategy_params == {"class": "required_provided"}
        assert tps[1].strategy_params == {"class": "required_missing"}

    def test_nullable_derives_2_points(self):
        ob, item = self._ob_and_item(
            FieldSpec(name="age", label="年龄", data_type=DataType.INT, required=False, nullable=False),
            "nullable",
        )
        tps = derive_equivalence_testpoints(ob, item)
        assert len(tps) == 2
        assert tps[0].strategy_params == {"class": "non_null_value"}
        assert tps[1].strategy_params == {"class": "null_value"}

    def test_unique_derives_2_points(self):
        ob, item = self._ob_and_item(
            FieldSpec(name="username", label="用户名", data_type=DataType.STRING, unique=True),
            "unique",
        )
        tps = derive_equivalence_testpoints(ob, item)
        assert len(tps) == 2
        assert tps[0].strategy_params == {"class": "unique_new_value"}
        assert tps[1].strategy_params == {"class": "unique_duplicate_value"}

    def test_data_type_format_derives_2_points_abstract(self):
        """★ 修订后 description 不含具体数据样例（user@example.com / not-an-email）"""
        ob, item = self._ob_and_item(
            FieldSpec(name="email", label="邮箱", data_type=DataType.EMAIL),
            "data_type_format",
        )
        tps = derive_equivalence_testpoints(ob, item)
        assert len(tps) == 2
        assert tps[0].strategy_params == {"class": "valid_format", "data_type": "email"}
        assert tps[1].strategy_params == {"class": "invalid_format", "data_type": "email"}
        assert tps[0].title == "邮箱 email 格式合法"
        assert tps[1].title == "邮箱 email 格式非法"
        assert "user@example.com" not in tps[0].description
        assert "not-an-email" not in tps[1].description

    def test_testpoint_hard_constraints(self):
        """Step 4 硬性约束：provenance=STRATEGY / technique=EQUIVALENCE_CLASS / obligation_id 回链 / scope=STRATEGY"""
        ob, item = self._ob_and_item(
            FieldSpec(name="status", label="状态", data_type=DataType.ENUM, enum_values=["a", "b"], nullable=True),
            "enum",
        )
        tps = derive_equivalence_testpoints(ob, item)
        for tp in tps:
            assert tp.provenance == Provenance.STRATEGY
            assert tp.technique == Technique.EQUIVALENCE_CLASS
            assert tp.obligation_id == ob.id
            assert tp.generation_scope == GenerationScope.STRATEGY
            assert tp.dimension == TestDimension.EQUIVALENCE
            assert tp.subcategory == "等价类"
            assert tp.module == item.module
            assert tp.item_ids == [item.id]
            assert tp.version_id == VERSION_ID
            assert tp.run_id == RUN_ID
            assert tp.strategy_params is not None

    def test_priority_from_item_hint(self):
        item = _make_item(
            [FieldSpec(name="status", label="状态", data_type=DataType.ENUM, enum_values=["a"], nullable=True)],
            priority_hint=Priority.P0,
        )
        ob = _find_ob(derive_equivalence_obligations(item, RUN_ID), "enum")
        tps = derive_equivalence_testpoints(ob, item)
        assert all(tp.priority == Priority.P0 for tp in tps)

    def test_priority_defaults_to_p1(self):
        ob, item = self._ob_and_item(
            FieldSpec(name="status", label="状态", data_type=DataType.ENUM, enum_values=["a"], nullable=True),
            "enum",
        )
        tps = derive_equivalence_testpoints(ob, item)
        assert all(tp.priority == Priority.P1 for tp in tps)

    def test_strategy_params_unique_per_point(self):
        """同一 obligation 下多个点的 strategy_params 互不相同（fingerprint 区分依据）"""
        ob, item = self._ob_and_item(
            FieldSpec(name="status", label="状态", data_type=DataType.ENUM, enum_values=["a", "b", "c"], nullable=True),
            "enum",
        )
        tps = derive_equivalence_testpoints(ob, item)
        from core.v2.fingerprint import canonical_strategy_params

        canonical = [canonical_strategy_params(tp.strategy_params) for tp in tps]
        assert len(set(canonical)) == len(tps), "每个点的 strategy_params 必须互不相同"

    def test_wrong_technique_returns_empty(self):
        ob = CoverageObligation(
            run_id=RUN_ID,
            item_id=ITEM_ID,
            technique=Technique.BOUNDARY_VALUE,
            target="x",
            description="d",
            params={"kind": "enum"},
        )
        item = _make_item([])
        assert derive_equivalence_testpoints(ob, item) == []

    def test_unknown_kind_returns_empty(self):
        ob = CoverageObligation(
            run_id=RUN_ID,
            item_id=ITEM_ID,
            technique=Technique.EQUIVALENCE_CLASS,
            target="x",
            description="d",
            params={"kind": "unknown_kind", "label": "X"},
        )
        item = _make_item([])
        assert derive_equivalence_testpoints(ob, item) == []
