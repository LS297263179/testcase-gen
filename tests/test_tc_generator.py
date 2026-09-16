"""V2 Step 5 用例生成器测试 - strategy 代码模板合成 + LLM 合成 + generation_mode 判定。

全代码 + mock client，不依赖真实 API。
"""

import json

from core.schemas import (
    DataPlanItem,
    GenerationMode,
    GenerationScope,
    Priority,
    Provenance,
    RequirementItem,
    RequirementItemType,
    Technique,
    TestCaseType,
    TestDimension,
    TestPoint,
)
from core.v2.tc_generator import (
    TestCaseCandidate,
    _render_value,
    synthesize_strategy_template,
    synthesize_with_llm,
)
from core.v2.test_data_planner import DataPlanResult

VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
ITEM_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
OB_ID = "01ARZ3NDEKTSV4RRFFQ69G5FC1"
RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD2"
TP_ID = "01ARZ3NDEKTSV4RRFFQ69G5FE3"


class SynthClient:
    """LLM 合成 mock：返回预置响应；可模拟异常。"""

    def __init__(self, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        if self.raise_exc:
            raise self.raise_exc
        return self.response if self.response is not None else "{}"


def _strategy_tp(technique, dimension, strategy_params, title="测试点", priority=Priority.P1) -> TestPoint:
    return TestPoint(
        id=TP_ID,
        run_id=RUN_ID,
        version_id=VERSION_ID,
        item_ids=[ITEM_ID],
        module="用户注册",
        subcategory="等价类",
        title=title,
        description="描述",
        dimension=dimension,
        technique=technique,
        obligation_id=OB_ID,
        priority=priority,
        provenance=Provenance.STRATEGY,
        generation_scope=GenerationScope.STRATEGY,
        strategy_params=strategy_params,
    )


def _llm_tp(title="退出登录", dimension=TestDimension.FUNCTIONAL) -> TestPoint:
    return TestPoint(
        id=TP_ID,
        run_id=RUN_ID,
        version_id=VERSION_ID,
        item_ids=[ITEM_ID],
        module="登录",
        subcategory="功能",
        title=title,
        description="验证退出登录功能",
        dimension=dimension,
        priority=Priority.P1,
        provenance=Provenance.LLM,
        generation_scope=GenerationScope.ITEM,
    )


def _item() -> RequirementItem:
    return RequirementItem(
        id=ITEM_ID, version_id=VERSION_ID, seq=1, type=RequirementItemType.FUNCTION, module="登录", statement="退出登录"
    )


def _plan(
    data_plan,
    *,
    used_llm=False,
    technique="boundary_value",
    strategy_class="min",
    field_label="年龄",
    expected_valid=True,
    precondition_hint="",
) -> DataPlanResult:
    return DataPlanResult(
        data_plan=data_plan,
        used_llm=used_llm,
        technique=technique,
        strategy_class=strategy_class,
        field_label=field_label,
        expected_valid=expected_valid,
        precondition_hint=precondition_hint,
    )


# ============================================================
# _render_value
# ============================================================


class TestRenderValue:
    def test_none(self):
        assert "null" in _render_value(None)

    def test_bool(self):
        assert _render_value(True) == "true"
        assert _render_value(False) == "false"

    def test_empty_string(self):
        assert "空字符串" in _render_value("")

    def test_normal(self):
        assert _render_value(18) == "18"
        assert _render_value("abc") == "abc"


# ============================================================
# strategy 模板合成
# ============================================================


class TestStrategyTemplate:
    def test_boundary_template_code_mode(self):
        tp = _strategy_tp(
            Technique.BOUNDARY_VALUE, TestDimension.BOUNDARY, {"boundary_type": "min", "value": 1, "kind": "value"}
        )
        plan = _plan(
            [
                DataPlanItem(
                    field="age",
                    strategy="boundary",
                    value=1,
                    source="strategy_params",
                    generator="code",
                    expected_valid=True,
                )
            ],
            technique="boundary_value",
            strategy_class="min",
            field_label="年龄",
            expected_valid=True,
        )
        cand = synthesize_strategy_template(tp, plan)
        assert isinstance(cand, TestCaseCandidate)
        assert cand.generation_mode == GenerationMode.CODE
        assert cand.calls == 0  # 模板合成不调 LLM
        assert cand.raw["type"] == TestCaseType.BOUNDARY.value
        assert cand.raw["module"] == "用户注册"
        assert len(cand.raw["steps"]) == 2
        assert "年龄" in cand.raw["expected"]

    def test_boundary_invalid_expected_reject(self):
        tp = _strategy_tp(
            Technique.BOUNDARY_VALUE,
            TestDimension.BOUNDARY,
            {"boundary_type": "min_minus_1", "value": 0, "kind": "value"},
        )
        plan = _plan(
            [
                DataPlanItem(
                    field="age",
                    strategy="boundary",
                    value=0,
                    source="strategy_params",
                    generator="code",
                    expected_valid=False,
                )
            ],
            strategy_class="min_minus_1",
            field_label="年龄",
            expected_valid=False,
        )
        cand = synthesize_strategy_template(tp, plan)
        assert "拒绝" in cand.raw["expected"]

    def test_equivalence_template_type(self):
        tp = _strategy_tp(Technique.EQUIVALENCE_CLASS, TestDimension.EQUIVALENCE, {"class": "invalid_enum_value"})
        plan = _plan(
            [
                DataPlanItem(
                    field="status",
                    strategy="equivalence",
                    value="INVALID_VALUE",
                    source="enum",
                    generator="code",
                    expected_valid=False,
                )
            ],
            technique="equivalence_class",
            strategy_class="invalid_enum_value",
            field_label="状态",
            expected_valid=False,
        )
        cand = synthesize_strategy_template(tp, plan)
        assert cand.raw["type"] == TestCaseType.EQUIVALENCE.value
        assert cand.generation_mode == GenerationMode.CODE

    def test_permission_template(self):
        tp = _strategy_tp(
            Technique.PERMISSION_MATRIX,
            TestDimension.PERMISSION,
            {"role": "admin", "resource": "order", "action": "refund", "allowed": True},
        )
        plan = _plan(
            [
                DataPlanItem(
                    field="admin:order:refund",
                    strategy="permission",
                    value="role=admin, action=refund",
                    source="strategy_params",
                    generator="code",
                    expected_valid=True,
                )
            ],
            technique="permission_matrix",
            strategy_class="permission",
            field_label="admin refund order",
            expected_valid=True,
            precondition_hint="前置：以「admin」角色登录",
        )
        cand = synthesize_strategy_template(tp, plan)
        assert cand.raw["type"] == TestCaseType.PERMISSION.value
        assert cand.raw["precondition"] == "前置：以「admin」角色登录"
        assert "admin" in cand.raw["steps"][0]["action"]
        assert "允许" in cand.raw["expected"] or "成功" in cand.raw["expected"]

    def test_hybrid_mode_when_llm_data_used(self):
        """DataPlanner 用了 LLM 兜底数据 → generation_mode=HYBRID"""
        tp = _strategy_tp(Technique.EQUIVALENCE_CLASS, TestDimension.EQUIVALENCE, {"class": "valid_pattern"})
        plan = _plan(
            [
                DataPlanItem(
                    field="phone",
                    strategy="equivalence",
                    value="13800138000",
                    source="llm",
                    generator="llm",
                    expected_valid=True,
                )
            ],
            used_llm=True,
            technique="equivalence_class",
            strategy_class="valid_pattern",
            field_label="手机号",
        )
        cand = synthesize_strategy_template(tp, plan)
        assert cand.generation_mode == GenerationMode.HYBRID
        assert "HYBRID" in cand.raw["remark"]

    def test_priority_from_testpoint(self):
        tp = _strategy_tp(
            Technique.BOUNDARY_VALUE,
            TestDimension.BOUNDARY,
            {"boundary_type": "min", "value": 1, "kind": "value"},
            priority=Priority.P0,
        )
        plan = _plan(
            [
                DataPlanItem(
                    field="age",
                    strategy="boundary",
                    value=1,
                    source="strategy_params",
                    generator="code",
                    expected_valid=True,
                )
            ]
        )
        cand = synthesize_strategy_template(tp, plan)
        assert cand.raw["priority"] == "P0"

    def test_data_plan_carried_to_candidate(self):
        tp = _strategy_tp(
            Technique.BOUNDARY_VALUE, TestDimension.BOUNDARY, {"boundary_type": "min", "value": 1, "kind": "value"}
        )
        dp = [
            DataPlanItem(
                field="age",
                strategy="boundary",
                value=1,
                source="strategy_params",
                generator="code",
                expected_valid=True,
            )
        ]
        cand = synthesize_strategy_template(tp, _plan(dp))
        assert cand.data_plan == dp
        assert cand.test_point_id == TP_ID


# ============================================================
# LLM 合成
# ============================================================


class TestLLMSynthesis:
    def _llm_response(self):
        return json.dumps(
            {
                "title": "退出登录后回到登录页",
                "precondition": "用户已登录",
                "steps": [
                    {"action": "点击退出按钮", "data": None, "expected": "系统清除会话"},
                    {"action": "观察页面跳转", "data": None, "expected": "回到登录页"},
                ],
                "expected": "用户被登出并跳转到登录页",
                "type": "functional",
                "priority": "P1",
                "remark": "",
            },
            ensure_ascii=False,
        )

    def test_llm_synthesis_success(self):
        client = SynthClient(response=self._llm_response())
        cand = synthesize_with_llm(client, _llm_tp(), _item(), _plan([]))
        assert cand.generation_mode == GenerationMode.LLM
        assert cand.calls == 1
        assert cand.raw["title"] == "退出登录后回到登录页"
        assert len(cand.raw["steps"]) == 2
        assert not cand.issues

    def test_llm_synthesis_overwrites_module(self):
        """module 由代码覆写为 TestPoint.module，不接受 LLM 给的值"""
        resp = json.dumps(
            {
                "title": "t",
                "module": "LLM乱写的模块",
                "steps": [{"action": "a"}],
                "expected": "e",
                "type": "functional",
                "priority": "P1",
            },
            ensure_ascii=False,
        )
        client = SynthClient(response=resp)
        cand = synthesize_with_llm(client, _llm_tp(), _item(), _plan([]))
        assert cand.raw["module"] == "登录"  # 来自 TestPoint

    def test_llm_synthesis_json_in_codeblock(self):
        """LLM 响应包裹在 ```json ``` 代码块里也能提取"""
        client = SynthClient(response=f"```json\n{self._llm_response()}\n```")
        cand = synthesize_with_llm(client, _llm_tp(), _item(), _plan([]))
        assert cand.raw["title"] == "退出登录后回到登录页"

    def test_llm_synthesis_call_exception(self):
        client = SynthClient(raise_exc=RuntimeError("API down"))
        cand = synthesize_with_llm(client, _llm_tp(), _item(), _plan([]))
        assert cand.raw == {}
        assert cand.calls == 1
        assert any("调用失败" in i for i in cand.issues)

    def test_llm_synthesis_bad_json(self):
        client = SynthClient(response="这不是 JSON")
        cand = synthesize_with_llm(client, _llm_tp(), _item(), _plan([]))
        assert cand.raw == {}
        assert any("无法解析 JSON" in i for i in cand.issues)

    def test_llm_synthesis_prompt_contains_context(self):
        """user prompt 应含 TestPoint / item / DataPlan 上下文"""
        client = SynthClient(response=self._llm_response())
        synthesize_with_llm(client, _llm_tp(), _item(), _plan([]))
        user = client.calls[0]["user"]
        assert "退出登录" in user  # TestPoint title
        assert "测试点" in user or "TestPoint" in user

    def test_llm_synthesis_injects_data_plan(self):
        """DataPlan 非空时注入 user prompt（供 LLM 使用具体数据）"""
        dp = [
            DataPlanItem(
                field="phone",
                strategy="equivalence",
                value="13800138000",
                source="builtin",
                generator="code",
                expected_valid=True,
            )
        ]
        client = SynthClient(response=self._llm_response())
        synthesize_with_llm(client, _llm_tp(), _item(), _plan(dp))
        assert "13800138000" in client.calls[0]["user"]
