"""V2 Step 4 权限矩阵策略测试 - PermissionRule → 1:1 派生 CoverageObligation + TestPoint。"""

from core.schemas import (
    CoverageObligation,
    GenerationScope,
    PermissionRule,
    Priority,
    Provenance,
    RequirementItem,
    RequirementItemType,
    Technique,
    TestDimension,
)
from core.v2.strategy.permission import (
    derive_permission_obligations,
    derive_permission_testpoints,
)

VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
ITEM_ID = "01ARZ3NDEKTSV4RRFFQ69G5FC0"


def _make_item(
    permissions: list[PermissionRule], priority_hint: Priority | None = None, module: str = "订单管理"
) -> RequirementItem:
    return RequirementItem(
        id=ITEM_ID,
        version_id=VERSION_ID,
        seq=1,
        type=RequirementItemType.PERMISSION,
        module=module,
        statement="权限规则",
        permissions=permissions,
        priority_hint=priority_hint,
        confidence=0.9,
    )


# ============================================================
# derive_permission_obligations 义务派生
# ============================================================


class TestDerivePermissionObligations:
    def test_single_rule_derives_one_obligation(self):
        item = _make_item([PermissionRule(role="admin", resource="order", action="refund", allowed=True)])
        obs = derive_permission_obligations(item, RUN_ID)
        assert len(obs) == 1
        ob = obs[0]
        assert ob.technique == Technique.PERMISSION_MATRIX
        assert ob.target == "admin:order:refund"
        assert ob.item_id == ITEM_ID
        assert ob.run_id == RUN_ID
        assert ob.params == {
            "role": "admin",
            "resource": "order",
            "action": "refund",
            "allowed": True,
            "condition": None,
        }

    def test_deny_rule(self):
        item = _make_item([PermissionRule(role="user", resource="order", action="refund", allowed=False)])
        obs = derive_permission_obligations(item, RUN_ID)
        assert len(obs) == 1
        assert obs[0].params["allowed"] is False
        assert "拒绝" in obs[0].description

    def test_multiple_rules_derive_multiple_obligations(self):
        item = _make_item(
            [
                PermissionRule(role="admin", resource="order", action="refund", allowed=True),
                PermissionRule(role="user", resource="order", action="refund", allowed=False),
                PermissionRule(role="admin", resource="order", action="view", allowed=True),
            ]
        )
        obs = derive_permission_obligations(item, RUN_ID)
        assert len(obs) == 3
        targets = {ob.target for ob in obs}
        assert targets == {"admin:order:refund", "user:order:refund", "admin:order:view"}

    def test_condition_preserved_in_params(self):
        item = _make_item(
            [PermissionRule(role="user", resource="order", action="cancel", allowed=True, condition="仅本人订单")]
        )
        obs = derive_permission_obligations(item, RUN_ID)
        assert obs[0].params["condition"] == "仅本人订单"

    def test_no_permissions_returns_empty(self):
        item = RequirementItem(
            id=ITEM_ID,
            version_id=VERSION_ID,
            seq=1,
            type=RequirementItemType.FUNCTION,
            module="m",
            statement="s",
            confidence=0.9,
        )
        assert derive_permission_obligations(item, RUN_ID) == []


# ============================================================
# derive_permission_testpoints TestPoint 派生（1:1）
# ============================================================


class TestDerivePermissionTestpoints:
    def _ob_and_item(self, rule: PermissionRule, **item_kwargs) -> tuple[CoverageObligation, RequirementItem]:
        item = _make_item([rule], **item_kwargs)
        ob = derive_permission_obligations(item, RUN_ID)[0]
        return ob, item

    def test_allow_rule_derives_one_testpoint(self):
        ob, item = self._ob_and_item(PermissionRule(role="admin", resource="order", action="refund", allowed=True))
        tps = derive_permission_testpoints(ob, item)
        assert len(tps) == 1
        tp = tps[0]
        assert tp.title == "admin refund order 允许"
        assert "预期：操作成功" in tp.description

    def test_deny_rule_derives_one_testpoint(self):
        ob, item = self._ob_and_item(PermissionRule(role="user", resource="order", action="refund", allowed=False))
        tps = derive_permission_testpoints(ob, item)
        assert len(tps) == 1
        tp = tps[0]
        assert tp.title == "user refund order 拒绝"
        assert "预期：操作被拒绝" in tp.description
        assert "权限不足" in tp.description

    def test_condition_in_description_not_in_strategy_params(self):
        """condition 出现在 description 里，但不进入 strategy_params（避免自然语言参与 fingerprint）"""
        ob, item = self._ob_and_item(
            PermissionRule(role="user", resource="order", action="cancel", allowed=True, condition="仅本人订单")
        )
        tps = derive_permission_testpoints(ob, item)
        tp = tps[0]
        assert "仅本人订单" in tp.description
        assert "附加条件" in tp.description
        # strategy_params 不含 condition
        assert "condition" not in tp.strategy_params
        assert tp.strategy_params == {
            "role": "user",
            "resource": "order",
            "action": "cancel",
            "allowed": True,
        }

    def test_no_condition_no_note(self):
        ob, item = self._ob_and_item(PermissionRule(role="admin", resource="order", action="view", allowed=True))
        tps = derive_permission_testpoints(ob, item)
        assert "附加条件" not in tps[0].description

    def test_testpoint_hard_constraints(self):
        """Step 4 硬性约束：provenance=STRATEGY / technique=PERMISSION_MATRIX / obligation_id 回链 / scope=STRATEGY"""
        ob, item = self._ob_and_item(PermissionRule(role="admin", resource="order", action="refund", allowed=True))
        tps = derive_permission_testpoints(ob, item)
        tp = tps[0]
        assert tp.provenance == Provenance.STRATEGY
        assert tp.technique == Technique.PERMISSION_MATRIX
        assert tp.obligation_id == ob.id
        assert tp.generation_scope == GenerationScope.STRATEGY
        assert tp.dimension == TestDimension.PERMISSION
        assert tp.subcategory == "权限矩阵"
        assert tp.module == item.module
        assert tp.item_ids == [item.id]
        assert tp.version_id == VERSION_ID
        assert tp.run_id == RUN_ID
        assert tp.strategy_params is not None

    def test_priority_from_item_hint(self):
        ob, item = self._ob_and_item(
            PermissionRule(role="admin", resource="order", action="refund", allowed=True),
            priority_hint=Priority.P0,
        )
        tps = derive_permission_testpoints(ob, item)
        assert tps[0].priority == Priority.P0

    def test_priority_defaults_to_p1(self):
        ob, item = self._ob_and_item(PermissionRule(role="admin", resource="order", action="refund", allowed=True))
        tps = derive_permission_testpoints(ob, item)
        assert tps[0].priority == Priority.P1

    def test_module_from_item(self):
        ob, item = self._ob_and_item(
            PermissionRule(role="admin", resource="order", action="refund", allowed=True),
            module="支付网关",
        )
        tps = derive_permission_testpoints(ob, item)
        assert tps[0].module == "支付网关"

    def test_wrong_technique_returns_empty(self):
        """非 PERMISSION_MATRIX obligation 传入 → 返回空（防御性）"""
        ob = CoverageObligation(
            run_id=RUN_ID,
            item_id=ITEM_ID,
            technique=Technique.BOUNDARY_VALUE,
            target="x",
            description="d",
            params={"role": "admin"},
        )
        item = _make_item([])
        assert derive_permission_testpoints(ob, item) == []

    def test_strategy_params_unique_per_rule(self):
        """同一 item 下多个 PermissionRule 派生的 TestPoint，strategy_params 互不相同"""
        item = _make_item(
            [
                PermissionRule(role="admin", resource="order", action="refund", allowed=True),
                PermissionRule(role="user", resource="order", action="refund", allowed=False),
            ]
        )
        obs = derive_permission_obligations(item, RUN_ID)
        all_tps = []
        for ob in obs:
            all_tps.extend(derive_permission_testpoints(ob, item))
        assert len(all_tps) == 2
        from core.v2.fingerprint import canonical_strategy_params

        canonical = [canonical_strategy_params(tp.strategy_params) for tp in all_tps]
        assert len(set(canonical)) == 2
