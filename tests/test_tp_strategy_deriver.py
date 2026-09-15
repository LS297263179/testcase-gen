"""V2 Step 4 TestPoint 派生器测试 - obligation → TestPoint + fingerprint + 去重 + 分派。"""

from core.schemas import (
    CoverageObligation,
    DataType,
    FieldSpec,
    GenerationScope,
    PermissionRule,
    Provenance,
    RequirementItem,
    RequirementItemType,
    Technique,
    TestDimension,
)
from core.v2.strategy.boundary import derive_boundary_obligations
from core.v2.strategy.deriver import DeriveResult, derive_testpoints_from_obligations
from core.v2.strategy.engine import derive_obligations
from core.v2.strategy.equivalence import derive_equivalence_obligations
from core.v2.strategy.permission import derive_permission_obligations

VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
ITEM_ID = "01ARZ3NDEKTSV4RRFFQ69G5FC0"


def _make_item(
    item_id: str = ITEM_ID,
    fields: list[FieldSpec] | None = None,
    permissions: list[PermissionRule] | None = None,
    module: str = "用户注册",
) -> RequirementItem:
    return RequirementItem(
        id=item_id,
        version_id=VERSION_ID,
        seq=1,
        type=RequirementItemType.DATA_FIELD,
        module=module,
        statement="字段约束",
        fields=fields or [],
        permissions=permissions or [],
        confidence=0.9,
    )


# ============================================================
# engine.derive_obligations 三策略汇总
# ============================================================


class TestEngineDeriveObligations:
    def test_aggregates_three_strategies(self):
        """一个 item 同时含 fields + permissions → 汇总三策略 obligation"""
        item = _make_item(
            fields=[
                FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100),
                FieldSpec(name="status", label="状态", data_type=DataType.ENUM, enum_values=["a", "b"], nullable=True),
            ],
            permissions=[PermissionRule(role="admin", resource="user", action="delete", allowed=True)],
        )
        obs = derive_obligations([item], RUN_ID)
        techniques = {ob.technique for ob in obs}
        assert Technique.BOUNDARY_VALUE in techniques
        assert Technique.EQUIVALENCE_CLASS in techniques
        assert Technique.PERMISSION_MATRIX in techniques
        # boundary: age 数值 1 个；equivalence: status enum 1 个；permission: 1 个 → 至少 3 个
        assert len(obs) >= 3

    def test_multiple_items_aggregated(self):
        item1 = _make_item(
            item_id="01ARZ3NDEKTSV4RRFFQ69G5FC1",
            fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)],
        )
        item2 = _make_item(
            item_id="01ARZ3NDEKTSV4RRFFQ69G5FC2",
            permissions=[PermissionRule(role="admin", resource="order", action="view", allowed=True)],
        )
        obs = derive_obligations([item1, item2], RUN_ID)
        item_ids = {ob.item_id for ob in obs}
        assert item_ids == {item1.id, item2.id}

    def test_empty_items_returns_empty(self):
        assert derive_obligations([], RUN_ID) == []

    def test_item_no_fields_no_permissions_returns_empty(self):
        item = _make_item()
        assert derive_obligations([item], RUN_ID) == []

    def test_obligation_order_stable(self):
        """同一 item 内顺序：boundary → equivalence → permission"""
        item = _make_item(
            fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)],
            permissions=[PermissionRule(role="admin", resource="user", action="delete", allowed=True)],
        )
        obs = derive_obligations([item], RUN_ID)
        techniques = [ob.technique for ob in obs]
        # boundary 在前，permission 在后
        assert techniques.index(Technique.BOUNDARY_VALUE) < techniques.index(Technique.PERMISSION_MATRIX)


# ============================================================
# deriver.derive_testpoints_from_obligations 派生 + fingerprint + 去重
# ============================================================


class TestDeriverDispatch:
    def test_boundary_obligation_dispatched(self):
        item = _make_item(
            fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)]
        )
        obs = derive_boundary_obligations(item, RUN_ID)
        result = derive_testpoints_from_obligations(obs, {item.id: item})
        assert isinstance(result, DeriveResult)
        assert len(result.points) == 6  # 六点边界
        assert all(tp.technique == Technique.BOUNDARY_VALUE for tp in result.points)
        assert all(tp.dimension == TestDimension.BOUNDARY for tp in result.points)

    def test_equivalence_obligation_dispatched(self):
        item = _make_item(
            fields=[
                FieldSpec(name="status", label="状态", data_type=DataType.ENUM, enum_values=["a", "b"], nullable=True)
            ]
        )
        obs = derive_equivalence_obligations(item, RUN_ID)
        result = derive_testpoints_from_obligations(obs, {item.id: item})
        assert len(result.points) == 3  # 2 合法 + 1 非法
        assert all(tp.technique == Technique.EQUIVALENCE_CLASS for tp in result.points)

    def test_permission_obligation_dispatched(self):
        item = _make_item(permissions=[PermissionRule(role="admin", resource="order", action="refund", allowed=True)])
        obs = derive_permission_obligations(item, RUN_ID)
        result = derive_testpoints_from_obligations(obs, {item.id: item})
        assert len(result.points) == 1  # 1:1
        assert result.points[0].technique == Technique.PERMISSION_MATRIX

    def test_unknown_technique_skipped_with_issue(self):
        """首批不支持的 technique（DECISION_TABLE）→ 记入 issues 跳过"""
        item = _make_item()
        ob = CoverageObligation(
            run_id=RUN_ID,
            item_id=item.id,
            technique=Technique.DECISION_TABLE,
            target="x",
            description="d",
            params={},
        )
        result = derive_testpoints_from_obligations([ob], {item.id: item})
        assert result.points == []
        assert any("首批不支持" in i for i in result.issues)

    def test_missing_item_skipped_with_issue(self):
        """obligation.item_id 不在 items_index → 记入 issues 跳过"""
        # 使用合法 ULID 格式但不在 items_index 里的 id
        missing_id = "01ARZ3NDEKTSV4RRFFQ69G5FZZ"
        ob = CoverageObligation(
            run_id=RUN_ID,
            item_id=missing_id,
            technique=Technique.BOUNDARY_VALUE,
            target="x",
            description="d",
            params={"kind": "value", "points": [1]},
        )
        result = derive_testpoints_from_obligations([ob], {})
        assert result.points == []
        assert any("不在 items_index" in i for i in result.issues)


class TestDeriverFingerprint:
    def test_all_points_have_fingerprint(self):
        item = _make_item(
            fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)]
        )
        obs = derive_boundary_obligations(item, RUN_ID)
        result = derive_testpoints_from_obligations(obs, {item.id: item})
        for tp in result.points:
            assert tp.fingerprint is not None
            assert tp.fingerprint.startswith("tp_")
            assert len(tp.fingerprint) == 35

    def test_fingerprint_unique_per_strategy_params(self):
        """同一 obligation 下 6 个点的 strategy_params 不同 → fingerprint 互不相同"""
        item = _make_item(
            fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)]
        )
        obs = derive_boundary_obligations(item, RUN_ID)
        result = derive_testpoints_from_obligations(obs, {item.id: item})
        fps = {tp.fingerprint for tp in result.points}
        assert len(fps) == 6

    def test_fingerprint_stable_across_calls(self):
        """相同输入两次派生 → fingerprint 完全一致（幂等基础）"""
        item = _make_item(
            fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)]
        )
        obs1 = derive_boundary_obligations(item, RUN_ID)
        # obligation.id 每次新生成，需固定才能比较 fingerprint；此处用同一 obs 对象
        r1 = derive_testpoints_from_obligations(obs1, {item.id: item})
        r2 = derive_testpoints_from_obligations(obs1, {item.id: item})
        assert {tp.fingerprint for tp in r1.points} == {tp.fingerprint for tp in r2.points}

    def test_version_id_injected(self):
        """显式 version_id 覆盖 item.version_id"""
        item = _make_item(
            fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)]
        )
        obs = derive_boundary_obligations(item, RUN_ID)
        other_vid = "01ARZ3NDEKTSV4RRFFQ69G5FZZ"
        result = derive_testpoints_from_obligations(obs, {item.id: item}, version_id=other_vid)
        assert all(tp.version_id == other_vid for tp in result.points)

    def test_version_id_defaults_to_item(self):
        item = _make_item(
            fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)]
        )
        obs = derive_boundary_obligations(item, RUN_ID)
        result = derive_testpoints_from_obligations(obs, {item.id: item})
        assert all(tp.version_id == VERSION_ID for tp in result.points)


class TestDeriverDedupe:
    def test_duplicate_fingerprint_kept_first(self):
        """构造两个 strategy_params 完全相同的 obligation → 第二个被去重"""
        item = _make_item(
            fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)]
        )
        obs = derive_boundary_obligations(item, RUN_ID)
        # 复制同一 obligation 两次（模拟重复输入）
        result = derive_testpoints_from_obligations(obs + obs, {item.id: item})
        # 6 个点保留，6 个重复被丢弃
        assert len(result.points) == 6
        assert len([i for i in result.issues if "重复 fingerprint" in i]) == 6


class TestDeriverHardConstraints:
    def test_all_step4_constraints(self):
        """Step 4 硬性约束：provenance=STRATEGY / scope=STRATEGY / technique!=None / obligation_id!=None"""
        item = _make_item(
            fields=[FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100)],
            permissions=[PermissionRule(role="admin", resource="order", action="refund", allowed=True)],
        )
        obs = derive_obligations([item], RUN_ID)
        result = derive_testpoints_from_obligations(obs, {item.id: item})
        assert len(result.points) > 0
        for tp in result.points:
            assert tp.provenance == Provenance.STRATEGY
            assert tp.generation_scope == GenerationScope.STRATEGY
            assert tp.technique is not None
            assert tp.obligation_id is not None
            assert tp.strategy_params is not None
            assert tp.item_ids == [item.id]
            assert tp.module == item.module


class TestDeriverMixedStrategies:
    def test_mixed_obligations_all_derived(self):
        """三策略 obligation 混合输入 → 全部正确派生"""
        item = _make_item(
            fields=[
                # age 设 nullable=True 避免额外派生 nullable obligation，让计数清晰
                FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True),
                FieldSpec(name="status", label="状态", data_type=DataType.ENUM, enum_values=["a", "b"], nullable=True),
            ],
            permissions=[PermissionRule(role="admin", resource="order", action="refund", allowed=True)],
        )
        obs = derive_obligations([item], RUN_ID)
        result = derive_testpoints_from_obligations(obs, {item.id: item})
        # boundary 6 (age) + equivalence 3 (status enum: 2 合法+1 非法) + permission 1 = 10
        assert len(result.points) == 10
        techniques = {tp.technique for tp in result.points}
        assert techniques == {Technique.BOUNDARY_VALUE, Technique.EQUIVALENCE_CLASS, Technique.PERMISSION_MATRIX}
        # 无 issues
        assert result.issues == []
