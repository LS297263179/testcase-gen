"""V2 Step 5 TestDataPlanner 测试 - 数据来源优先级、各 strategy 数据生成、LLM 兜底 + re.fullmatch 验证。

全代码 + mock client，不依赖真实 API。
"""

import re

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
    TestPoint,
)
from core.v2.test_data_planner import (
    DataPlanResult,
    _gen_invalid_enum,
    _heuristic_pattern_sample,
    _make_valid_id_card,
    _resolve_valid_sample,
    _verify_pattern,
    plan_data_for_testpoint,
)

VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
ITEM_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
OB_ID = "01ARZ3NDEKTSV4RRFFQ69G5FC1"
RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD2"


class PatternClient:
    """复杂正则兜底的 mock client：按序返回预置响应（每次 chat 弹出一个）。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = 0
        self.prompts: list[str] = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls += 1
        self.prompts.append(system_prompt)
        if self.responses:
            return self.responses.pop(0)
        return '{"sample": ""}'


def _item(fields=None, permissions=None) -> RequirementItem:
    return RequirementItem(
        id=ITEM_ID,
        version_id=VERSION_ID,
        seq=1,
        type=RequirementItemType.DATA_FIELD,
        module="用户注册",
        statement="字段约束",
        fields=fields or [],
        permissions=permissions or [],
    )


def _ob(technique: Technique, target: str, params: dict) -> CoverageObligation:
    return CoverageObligation(
        id=OB_ID,
        run_id=RUN_ID,
        item_id=ITEM_ID,
        technique=technique,
        target=target,
        description="义务描述",
        params=params,
    )


def _strategy_tp(technique: Technique, dimension: TestDimension, strategy_params: dict) -> TestPoint:
    return TestPoint(
        id="01ARZ3NDEKTSV4RRFFQ69G5FE3",
        run_id=RUN_ID,
        version_id=VERSION_ID,
        item_ids=[ITEM_ID],
        module="用户注册",
        subcategory="等价类",
        title="测试点",
        description="描述",
        dimension=dimension,
        technique=technique,
        obligation_id=OB_ID,
        priority=Priority.P1,
        provenance=Provenance.STRATEGY,
        generation_scope=GenerationScope.STRATEGY,
        strategy_params=strategy_params,
    )


# ============================================================
# helper 单元测试
# ============================================================


class TestHelpers:
    def test_make_valid_id_card_checksum(self):
        """生成的身份证校验位正确（GB 11643 加权模 11-2）"""
        idc = _make_valid_id_card()
        assert len(idc) == 18
        weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
        check_map = "10X98765432"
        s = sum(int(idc[i]) * weights[i] for i in range(17))
        assert idc[17] == check_map[s % 11]

    def test_verify_pattern_valid(self):
        assert _verify_pattern(r"^\d{6}$", "123456", want_valid=True) is True
        assert _verify_pattern(r"^\d{6}$", "12345", want_valid=True) is False

    def test_verify_pattern_invalid_want(self):
        assert _verify_pattern(r"^\d{6}$", "abc", want_valid=False) is True
        assert _verify_pattern(r"^\d{6}$", "123456", want_valid=False) is False

    def test_verify_pattern_bad_regex_returns_false(self):
        """非法正则无法验证 → 返回 False（不接受）"""
        assert _verify_pattern(r"[unclosed", "x", want_valid=True) is False

    def test_verify_pattern_none_sample(self):
        assert _verify_pattern(r"^\d+$", None, want_valid=True) is False

    def test_heuristic_digits(self):
        assert _heuristic_pattern_sample(r"^\d{6}$", True) == "111111"

    def test_heuristic_invalid_returns_nonmatching(self):
        sample = _heuristic_pattern_sample(r"^\d{6}$", False)
        assert sample is not None
        assert re.fullmatch(r"^\d{6}$", sample) is None

    def test_gen_invalid_enum_not_in_set(self):
        assert _gen_invalid_enum(["A", "B"]) == "INVALID_VALUE"
        assert _gen_invalid_enum(["INVALID_VALUE"]) == "INVALID_VALUE_1"

    def test_resolve_valid_sample_priority_example_first(self):
        """数据来源优先级 1：example 最优先"""
        f = FieldSpec(name="x", label="X", data_type=DataType.STRING, example="ex", default="df", enum_values=["e1"])
        value, source = _resolve_valid_sample(f)
        assert value == "ex" and source == "example"

    def test_resolve_valid_sample_priority_default_second(self):
        """数据来源优先级 2：无 example 时用 default"""
        f = FieldSpec(name="x", label="X", data_type=DataType.STRING, default="df", enum_values=["e1"])
        value, source = _resolve_valid_sample(f)
        assert value == "df" and source == "default"

    def test_resolve_valid_sample_priority_enum_third(self):
        """数据来源优先级 3：无 example/default 时用 enum_values[0]"""
        f = FieldSpec(name="x", label="X", data_type=DataType.ENUM, enum_values=["e1", "e2"])
        value, source = _resolve_valid_sample(f)
        assert value == "e1" and source == "enum"

    def test_resolve_valid_sample_priority_builtin_format(self):
        """数据来源优先级 4：格式类 data_type 用内置样例"""
        f = FieldSpec(name="email", label="邮箱", data_type=DataType.EMAIL)
        value, source = _resolve_valid_sample(f)
        assert value == "user@example.com" and source == "builtin"

    def test_resolve_valid_sample_priority_builtin_generic(self):
        """数据来源优先级 4：非格式类 data_type 用通用样例"""
        f = FieldSpec(name="age", label="年龄", data_type=DataType.INT)
        value, source = _resolve_valid_sample(f)
        assert value == 1 and source == "builtin"


# ============================================================
# 边界值数据规划
# ============================================================


class TestBoundaryPlan:
    def _plan(self, boundary_type, value, kind="value", warn=None):
        sp = {"boundary_type": boundary_type, "value": value, "kind": kind}
        if warn:
            sp["warn"] = warn
        tp = _strategy_tp(Technique.BOUNDARY_VALUE, TestDimension.BOUNDARY, sp)
        ob = _ob(Technique.BOUNDARY_VALUE, "age", {"field": "age", "label": "年龄", "kind": kind, "min": 1, "max": 100})
        return plan_data_for_testpoint(tp, obligation=ob, item=_item())

    def test_boundary_value_concrete(self):
        """边界值 strategy_params.value 已是具体值，直接采用"""
        r = self._plan("min", 1)
        assert isinstance(r, DataPlanResult)
        assert r.data_plan[0].value == 1
        assert r.data_plan[0].source == "strategy_params"
        assert r.data_plan[0].generator == "code"
        assert r.used_llm is False

    def test_boundary_expected_valid_flags(self):
        """min/max/内点 → 合法；min-1/max+1 → 非法"""
        assert self._plan("min", 1).expected_valid is True
        assert self._plan("max", 100).expected_valid is True
        assert self._plan("min_plus_1", 2).expected_valid is True
        assert self._plan("min_minus_1", 0).expected_valid is False
        assert self._plan("max_plus_1", 101).expected_valid is False

    def test_boundary_length_kind_builds_string(self):
        """length 类边界：value 是长度 → 构造对应长度字符串"""
        r = self._plan("min", 6, kind="length")
        assert r.data_plan[0].value == "aaaaaa"

    def test_boundary_length_negative_clamped(self):
        """length 为负（min_length=0 的 min-1）→ 空字符串，不报错"""
        r = self._plan("min_minus_1", -1, kind="length")
        assert r.data_plan[0].value == ""

    def test_boundary_warn_in_precondition(self):
        """warn 标记 → precondition_hint 提示人工确认"""
        r = self._plan("min_minus_1", 0, warn="negative_value_may_be_invalid")
        assert "negative_value_may_be_invalid" in r.precondition_hint


# ============================================================
# 等价类数据规划
# ============================================================


class TestEquivalencePlan:
    def _plan(self, cls, params, sp_extra=None, field=None, client=None):
        sp = {"class": cls}
        if sp_extra:
            sp.update(sp_extra)
        tp = _strategy_tp(Technique.EQUIVALENCE_CLASS, TestDimension.EQUIVALENCE, sp)
        ob = _ob(Technique.EQUIVALENCE_CLASS, params.get("field", "f"), params)
        item = _item(fields=[field] if field else [])
        return plan_data_for_testpoint(tp, obligation=ob, item=item, client=client)

    def test_valid_enum_value_concrete(self):
        r = self._plan(
            "valid_enum_value", {"field": "status", "label": "状态", "kind": "enum"}, sp_extra={"value": "active"}
        )
        assert r.data_plan[0].value == "active"
        assert r.data_plan[0].expected_valid is True
        assert r.data_plan[0].source == "enum"

    def test_invalid_enum_value_not_in_set(self):
        r = self._plan("invalid_enum_value", {"field": "status", "kind": "enum", "enum_values": ["active", "disabled"]})
        assert r.data_plan[0].value not in ["active", "disabled"]
        assert r.data_plan[0].expected_valid is False

    def test_required_provided_uses_field_example(self):
        f = FieldSpec(name="phone", label="手机号", data_type=DataType.PHONE, required=True, example="13900139000")
        r = self._plan("required_provided", {"field": "phone", "label": "手机号", "kind": "required"}, field=f)
        assert r.data_plan[0].value == "13900139000"
        assert r.data_plan[0].source == "example"
        assert r.data_plan[0].expected_valid is True

    def test_required_missing_empty_string(self):
        r = self._plan("required_missing", {"field": "phone", "kind": "required"})
        assert r.data_plan[0].value == ""
        assert r.data_plan[0].expected_valid is False

    def test_null_value_is_none(self):
        r = self._plan("null_value", {"field": "remark", "kind": "nullable"})
        assert r.data_plan[0].value is None
        assert r.data_plan[0].expected_valid is False

    def test_non_null_value_from_builtin(self):
        f = FieldSpec(name="age", label="年龄", data_type=DataType.INT, nullable=False)
        r = self._plan("non_null_value", {"field": "age", "kind": "nullable"}, field=f)
        assert r.data_plan[0].value is not None
        assert r.data_plan[0].expected_valid is True

    def test_unique_new_value_precondition(self):
        f = FieldSpec(name="username", label="用户名", data_type=DataType.STRING, unique=True, example="alice")
        r = self._plan("unique_new_value", {"field": "username", "kind": "unique"}, field=f)
        assert r.data_plan[0].value == "alice"
        assert r.data_plan[0].expected_valid is True
        assert "不存在" in r.precondition_hint

    def test_unique_duplicate_value_precondition(self):
        f = FieldSpec(name="username", label="用户名", data_type=DataType.STRING, unique=True, example="alice")
        r = self._plan("unique_duplicate_value", {"field": "username", "kind": "unique"}, field=f)
        assert r.data_plan[0].expected_valid is False
        assert "已存在" in r.precondition_hint

    def test_valid_format_email_builtin(self):
        f = FieldSpec(name="email", label="邮箱", data_type=DataType.EMAIL)
        r = self._plan(
            "valid_format",
            {"field": "email", "kind": "data_type_format", "data_type": "email"},
            sp_extra={"data_type": "email"},
            field=f,
        )
        assert r.data_plan[0].value == "user@example.com"
        assert r.data_plan[0].expected_valid is True

    def test_invalid_format_email_builtin(self):
        f = FieldSpec(name="email", label="邮箱", data_type=DataType.EMAIL)
        r = self._plan(
            "invalid_format",
            {"field": "email", "kind": "data_type_format", "data_type": "email"},
            sp_extra={"data_type": "email"},
            field=f,
        )
        assert r.data_plan[0].value == "abc"
        assert r.data_plan[0].expected_valid is False

    def test_valid_pattern_simple_by_code(self):
        r"""简单正则（^\d{6}$）由代码启发式生成，不调 LLM"""
        f = FieldSpec(name="code", label="验证码", data_type=DataType.STRING, pattern=r"^\d{6}$")
        r = self._plan("valid_pattern", {"field": "code", "kind": "pattern", "pattern": r"^\d{6}$"}, field=f)
        assert re.fullmatch(r"^\d{6}$", str(r.data_plan[0].value)) is not None
        assert r.used_llm is False
        assert r.data_plan[0].generator == "code"

    def test_invalid_pattern_simple_by_code(self):
        f = FieldSpec(name="code", label="验证码", data_type=DataType.STRING, pattern=r"^\d{6}$")
        r = self._plan("invalid_pattern", {"field": "code", "kind": "pattern", "pattern": r"^\d{6}$"}, field=f)
        assert re.fullmatch(r"^\d{6}$", str(r.data_plan[0].value)) is None
        assert r.data_plan[0].expected_valid is False

    def test_valid_pattern_uses_example_if_matches(self):
        """example 匹配正则时优先用 example（数据来源优先级 1）"""
        f = FieldSpec(name="code", label="验证码", data_type=DataType.STRING, pattern=r"^\d{6}$", example="999888")
        r = self._plan("valid_pattern", {"field": "code", "kind": "pattern", "pattern": r"^\d{6}$"}, field=f)
        assert r.data_plan[0].value == "999888"
        assert r.data_plan[0].source == "example"

    def test_unknown_class_records_issue(self):
        r = self._plan("unknown_class_xyz", {"field": "x", "kind": "enum"})
        assert r.data_plan == []
        assert any("unknown_class_xyz" in i for i in r.issues)


# ============================================================
# 复杂正则 LLM 兜底 + re.fullmatch 验证（门槛 14 核心）
# ============================================================


class TestPatternLLMFallback:
    COMPLEX = r"^1[3-9]\d{9}$"  # 手机号：代码启发式产 "111111111" 不匹配 → 落 LLM

    def _plan(self, cls, client):
        tp = _strategy_tp(Technique.EQUIVALENCE_CLASS, TestDimension.EQUIVALENCE, {"class": cls})
        ob = _ob(
            Technique.EQUIVALENCE_CLASS,
            "phone",
            {"field": "phone", "label": "手机号", "kind": "pattern", "pattern": self.COMPLEX},
        )
        f = FieldSpec(name="phone", label="手机号", data_type=DataType.PHONE, pattern=self.COMPLEX)
        return plan_data_for_testpoint(tp, obligation=ob, item=_item(fields=[f]), client=client)

    def test_complex_pattern_falls_to_llm_and_verified(self):
        """复杂正则 → LLM 兜底，返回值经 re.fullmatch 验证通过"""
        client = PatternClient(['{"sample": "13800138000"}'])
        r = self._plan("valid_pattern", client)
        assert r.data_plan[0].value == "13800138000"
        assert r.used_llm is True
        assert r.data_plan[0].generator == "llm"
        assert r.data_plan[0].source == "llm"
        assert re.fullmatch(self.COMPLEX, str(r.data_plan[0].value)) is not None
        assert client.calls == 1

    def test_llm_invalid_sample_retried_then_success(self):
        """LLM 首次给不匹配样例 → 代码验证拒绝 → 重试 → 第二次通过"""
        client = PatternClient(['{"sample": "123"}', '{"sample": "13800138000"}'])
        r = self._plan("valid_pattern", client)
        assert r.data_plan[0].value == "13800138000"
        assert r.used_llm is True
        assert client.calls == 2  # 第一次被 re.fullmatch 拒绝，重试

    def test_llm_all_retries_fail_records_issue(self):
        """LLM 3 次均给不匹配样例 → 全部被代码拒绝 → value=None + issue"""
        client = PatternClient(['{"sample": "bad"}', '{"sample": "bad"}', '{"sample": "bad"}'])
        r = self._plan("valid_pattern", client)
        assert r.data_plan[0].value is None
        assert r.used_llm is True
        assert client.calls == 3
        assert any("无法为 pattern" in i for i in r.issues)

    def test_invalid_pattern_complex_handled_by_code(self):
        """非法样例：代码启发式即可产出不匹配样例（无需 LLM），且经 re.fullmatch 验证不匹配。

        LLM 兜底主要用于「合法复杂正则」（构造匹配串难）；非法样例任意不匹配串即可，代码优先。
        """
        client = PatternClient(['{"sample": "13800138000"}'])  # 不应被调用
        r = self._plan("invalid_pattern", client)
        assert re.fullmatch(self.COMPLEX, str(r.data_plan[0].value)) is None
        assert r.data_plan[0].expected_valid is False
        assert r.used_llm is False  # 代码启发式已处理，未落 LLM
        assert client.calls == 0

    def test_no_client_skips_llm(self):
        """client=None 时不调 LLM，代码启发式失败则 value=None + issue"""
        r = self._plan("valid_pattern", None)
        assert r.used_llm is False
        assert r.data_plan[0].value is None
        assert any("无法为 pattern" in i for i in r.issues)


# ============================================================
# 权限矩阵数据规划
# ============================================================


class TestPermissionPlan:
    def _plan(self, allowed, condition=None):
        sp = {"role": "admin", "resource": "order", "action": "refund", "allowed": allowed}
        tp = _strategy_tp(Technique.PERMISSION_MATRIX, TestDimension.PERMISSION, sp)
        params = {"role": "admin", "resource": "order", "action": "refund", "allowed": allowed, "condition": condition}
        ob = _ob(Technique.PERMISSION_MATRIX, "admin:order:refund", params)
        return plan_data_for_testpoint(tp, obligation=ob, item=_item())

    def test_permission_allowed(self):
        r = self._plan(True)
        assert r.expected_valid is True
        assert r.data_plan[0].strategy == "permission"
        assert "admin" in str(r.data_plan[0].value)

    def test_permission_denied(self):
        r = self._plan(False)
        assert r.expected_valid is False

    def test_permission_precondition_role(self):
        r = self._plan(True)
        assert "admin" in r.precondition_hint

    def test_permission_condition_in_precondition(self):
        """condition（自然语言）进入 precondition 提示，不参与数据值"""
        r = self._plan(True, condition="仅本人订单")
        assert "仅本人订单" in r.precondition_hint


# ============================================================
# LLM TestPoint + 边界情况
# ============================================================


class TestLLMTestPointAndEdge:
    def test_llm_testpoint_empty_dataplan(self):
        """LLM TestPoint → DataPlan 留空（LLM 合成器直接消费 item context）"""
        tp = TestPoint(
            id="01ARZ3NDEKTSV4RRFFQ69G5FF4",
            run_id=RUN_ID,
            version_id=VERSION_ID,
            item_ids=[ITEM_ID],
            module="登录",
            subcategory="功能",
            title="退出登录",
            description="验证退出登录",
            dimension=TestDimension.FUNCTIONAL,
            provenance=Provenance.LLM,
            generation_scope=GenerationScope.ITEM,
        )
        r = plan_data_for_testpoint(tp, obligation=None, item=_item())
        assert r.data_plan == []
        assert r.used_llm is False

    def test_strategy_without_obligation_records_issue(self):
        """strategy TestPoint 缺 obligation → 记 issue，不崩溃"""
        tp = _strategy_tp(Technique.BOUNDARY_VALUE, TestDimension.BOUNDARY, {"boundary_type": "min", "value": 1})
        r = plan_data_for_testpoint(tp, obligation=None, item=_item())
        assert r.data_plan == []
        assert any("缺少 obligation" in i for i in r.issues)

    def test_unsupported_technique_records_issue(self):
        """首批不支持的 technique（如 DECISION_TABLE）→ 记 issue"""
        tp = _strategy_tp(Technique.DECISION_TABLE, TestDimension.FUNCTIONAL, {})
        ob = _ob(Technique.DECISION_TABLE, "x", {})
        r = plan_data_for_testpoint(tp, obligation=ob, item=_item())
        assert r.data_plan == []
        assert any("不支持" in i for i in r.issues)
