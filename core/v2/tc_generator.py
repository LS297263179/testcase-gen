"""V2 Step 5 测试用例生成器 - TestPoint + DataPlan → TestCase 候选。

两条合成路径（对应 generation_mode 判定，见 plan D6）：
  - **STRATEGY TestPoint** → `synthesize_strategy_template`：纯代码模板合成（不调 LLM）。
    Step 4 已把义务展开成原子级 TestPoint，其步骤是确定的（"在字段 X 输入值 Y，预期接受/拒绝"），
    模板合成比 LLM 更可复现、无幻觉、无 API 成本，契合 V2「确定性关注点代码化」原则。
    → generation_mode = CODE（若 DataPlanner 用了 LLM 兜底数据则 HYBRID）。
  - **LLM TestPoint** → `synthesize_with_llm`：调 LLM 合成自然语言步骤。
    → generation_mode = LLM。

本模块只产出"原始候选 dict"（未经 Pydantic 校验）；规范化/修复/fingerprint/状态判定交给 tc_validator.py；
Run 复用/持久化编排交给 tc_orchestrator.py。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from core.schemas import (
    DataPlanItem,
    GenerationMode,
    RequirementItem,
    Technique,
    TestCaseType,
    TestPoint,
)
from core.v2.parser import extract_json  # 复用 Step 2 的鲁棒 JSON 提取
from core.v2.tc_prompts import SYNTHESIZER_USER_TEMPLATE, TEST_CASE_SYNTHESIZER_PROMPT
from core.v2.test_data_planner import DataPlanResult

logger = logging.getLogger("v2.tc_generator")


# technique → TestCaseType 映射（strategy 模板合成用）
_TECHNIQUE_TO_TYPE = {
    Technique.BOUNDARY_VALUE: TestCaseType.BOUNDARY,
    Technique.EQUIVALENCE_CLASS: TestCaseType.EQUIVALENCE,
    Technique.PERMISSION_MATRIX: TestCaseType.PERMISSION,
}

# 边界点类型的人类可读描述
_BOUNDARY_DESC = {
    "min_minus_1": "下界-1（越下界）",
    "min": "下界（合法最小值）",
    "min_plus_1": "下界+1（区间内点）",
    "max_minus_1": "上界-1（区间内点）",
    "max": "上界（合法最大值）",
    "max_plus_1": "上界+1（越上界）",
}

# 等价类 class 的人类可读描述
_CLASS_DESC = {
    "valid_enum_value": "一个合法枚举值",
    "invalid_enum_value": "一个不在枚举集合中的非法值",
    "valid_pattern": "一个匹配格式要求的值",
    "invalid_pattern": "一个不匹配格式要求的非法值",
    "required_provided": "有效值（必填项已提供）",
    "required_missing": "空值（必填项缺失）",
    "non_null_value": "非 null 值",
    "null_value": "null 值",
    "unique_new_value": "系统中未存在的新值",
    "unique_duplicate_value": "系统中已存在的重复值",
    "valid_format": "符合格式要求的值",
    "invalid_format": "不符合格式要求的非法值",
}


@dataclass
class TestCaseCandidate:
    """单条 TestCase 候选（未过 Pydantic 校验，交给 tc_validator）。"""

    __test__ = False  # 名称以 Test 开头但非 pytest 测试类，禁止被收集

    test_point_id: str
    raw: dict = field(default_factory=dict)  # 用例字段：title/precondition/steps/expected/type/priority/remark/module
    generation_mode: GenerationMode = GenerationMode.LLM
    data_plan: list[DataPlanItem] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    calls: int = 0  # LLM 调用次数（模板合成为 0）


# ============================================================
# 序列化工具
# ============================================================


def _render_value(v: object) -> str:
    """把测试数据值渲染为人类可读文本（用于 steps.data / expected 文案）。"""
    if v is None:
        return "null（空值）"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str) and v == "":
        return "（空字符串）"
    return str(v)


def _testpoint_to_json(tp: TestPoint) -> str:
    """TestPoint → JSON（喂 LLM 合成器，剔除内部审计字段）。"""
    data = tp.model_dump(mode="json", exclude_none=True)
    for k in (
        "id",
        "run_id",
        "version_id",
        "provenance",
        "status",
        "generation_scope",
        "fingerprint",
        "strategy_params",
        "obligation_id",
        "schema_version",
        "created_at",
        "updated_at",
    ):
        data.pop(k, None)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _item_to_json(item: RequirementItem | None) -> str:
    """RequirementItem → JSON（喂 LLM 合成器，剔除内部审计字段）。"""
    if item is None:
        return "{}"
    data = item.model_dump(mode="json", exclude_none=True)
    for k in (
        "version_id",
        "seq",
        "provenance",
        "status",
        "confidence_level",
        "schema_version",
        "id",
        "created_at",
        "updated_at",
    ):
        data.pop(k, None)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _data_plan_to_json(data_plan: list[DataPlanItem]) -> str:
    """DataPlan → JSON（喂 LLM 合成器）。"""
    if not data_plan:
        return "[]"
    return json.dumps([d.model_dump(mode="json") for d in data_plan], ensure_ascii=False, indent=2)


# ============================================================
# STRATEGY TestPoint：代码模板合成（不调 LLM）
# ============================================================


def _template_boundary(plan: DataPlanResult, value_repr: str) -> tuple[list[dict], str, str]:
    """边界值模板步骤 + expected + title 后缀。"""
    label = plan.field_label or "该字段"
    bdesc = _BOUNDARY_DESC.get(plan.strategy_class, plan.strategy_class)
    valid = plan.expected_valid
    steps = [
        {
            "action": f"在「{label}」字段输入边界值 {value_repr}",
            "data": value_repr,
            "expected": "系统接受该输入" if valid else "系统拒绝该输入并提示超出取值范围",
        },
        {
            "action": "提交表单 / 触发校验",
            "data": None,
            "expected": "提交成功，无校验错误" if valid else "提交被阻止，显示边界校验错误提示",
        },
    ]
    expected = f"「{label}」取值 {value_repr}（{bdesc}），系统应{'接受该值' if valid else '拒绝该值并提示超出边界约束'}"
    return steps, expected, bdesc


def _template_equivalence(plan: DataPlanResult, value_repr: str) -> tuple[list[dict], str, str]:
    """等价类模板步骤 + expected + class 描述。"""
    label = plan.field_label or "该字段"
    cdesc = _CLASS_DESC.get(plan.strategy_class, plan.strategy_class)
    valid = plan.expected_valid
    steps = [
        {
            "action": f"在「{label}」字段输入{cdesc}：{value_repr}",
            "data": value_repr,
            "expected": "系统接受该输入" if valid else "系统拒绝该输入并提示校验错误",
        },
        {
            "action": "提交表单 / 触发校验",
            "data": None,
            "expected": "提交成功" if valid else "提交被阻止，显示等价类校验错误提示",
        },
    ]
    expected = f"「{label}」取{cdesc}（{value_repr}），系统应{'接受' if valid else '拒绝并提示'}"
    return steps, expected, cdesc


def _template_permission(plan: DataPlanResult) -> tuple[list[dict], str]:
    """权限矩阵模板步骤 + expected。"""
    dp = plan.data_plan[0] if plan.data_plan else None
    field_key = dp.field if dp else ""  # role:resource:action
    parts = field_key.split(":") if field_key else ["", "", ""]
    role = parts[0] if len(parts) > 0 else ""
    resource = parts[1] if len(parts) > 1 else ""
    action = parts[2] if len(parts) > 2 else ""
    allowed = plan.expected_valid
    steps = [
        {
            "action": f"以「{role}」身份对资源「{resource}」执行「{action}」操作",
            "data": None,
            "expected": "操作成功，返回预期结果" if allowed else "操作被拒绝，提示权限不足",
        },
    ]
    expected = f"权限矩阵：角色「{role}」对「{resource}」执行「{action}」应{'被允许，操作成功' if allowed else '被拒绝，提示权限不足'}"
    return steps, expected


def synthesize_strategy_template(tp: TestPoint, plan: DataPlanResult) -> TestCaseCandidate:
    """STRATEGY TestPoint → TestCase 候选（纯代码模板，不调 LLM）。

    generation_mode：DataPlanner 用了 LLM 兜底数据（复杂正则）→ HYBRID；否则 CODE。
    """
    technique = tp.technique
    tc_type = _TECHNIQUE_TO_TYPE.get(technique, TestCaseType.FUNCTIONAL)
    type_val = tc_type.value if hasattr(tc_type, "value") else str(tc_type)
    priority_val = tp.priority.value if hasattr(tp.priority, "value") else str(tp.priority)

    # 主数据值（取 DataPlan 首项）
    primary_value = plan.data_plan[0].value if plan.data_plan else None
    value_repr = _render_value(primary_value)

    issues = list(plan.issues)
    if technique == Technique.BOUNDARY_VALUE:
        steps, expected, _ = _template_boundary(plan, value_repr)
    elif technique == Technique.EQUIVALENCE_CLASS:
        steps, expected, _ = _template_equivalence(plan, value_repr)
    elif technique == Technique.PERMISSION_MATRIX:
        steps, expected = _template_permission(plan)
    else:
        steps, expected = [], tp.description
        issues.append(f"technique={technique} 无模板，回退用 description 作 expected")

    raw = {
        "module": tp.module,
        "title": tp.title,
        "precondition": plan.precondition_hint or "",
        "steps": steps,
        "expected": expected,
        "type": type_val,
        "priority": priority_val,
        "remark": "策略引擎代码模板合成（Step 5 CODE 路径）"
        if not plan.used_llm
        else "策略引擎模板合成 + LLM 兜底数据（Step 5 HYBRID 路径）",
    }
    mode = GenerationMode.HYBRID if plan.used_llm else GenerationMode.CODE
    return TestCaseCandidate(
        test_point_id=tp.id,
        raw=raw,
        generation_mode=mode,
        data_plan=list(plan.data_plan),
        issues=issues,
        calls=0,
    )


# ============================================================
# LLM TestPoint：LLM 合成
# ============================================================


def synthesize_with_llm(
    client,
    tp: TestPoint,
    item: RequirementItem | None,
    plan: DataPlanResult,
) -> TestCaseCandidate:
    """LLM TestPoint → TestCase 候选（调 LLM 合成自然语言步骤）。

    generation_mode = LLM（LLM TestPoint + LLM steps；DataPlan 对 LLM TestPoint 通常为空）。
    LLM 调用/解析失败时返回带 issues 的空候选（由 orchestrator 决定是否跳过）。
    """
    user_prompt = SYNTHESIZER_USER_TEMPLATE.format(
        testpoint_json=_testpoint_to_json(tp),
        item_json=_item_to_json(item),
        data_plan_json=_data_plan_to_json(plan.data_plan),
    )
    try:
        raw_response = client.chat(TEST_CASE_SYNTHESIZER_PROMPT, user_prompt)
    except Exception as e:  # noqa: BLE001 - LLM 调用失败需记录并返回空候选
        logger.exception("LLM 用例合成调用失败: tp=%s", tp.id)
        return TestCaseCandidate(
            test_point_id=tp.id,
            generation_mode=GenerationMode.LLM,
            data_plan=list(plan.data_plan),
            issues=[f"LLM 用例合成调用失败 tp={tp.id}: {e}"],
            calls=1,
        )

    payload = extract_json(raw_response)
    if not isinstance(payload, dict):
        return TestCaseCandidate(
            test_point_id=tp.id,
            generation_mode=GenerationMode.LLM,
            data_plan=list(plan.data_plan),
            issues=[f"LLM 用例合成无法解析 JSON: tp={tp.id}"],
            calls=1,
        )

    # 代码覆写 module（来自 TestPoint，不接受 LLM 改写）
    payload["module"] = tp.module
    return TestCaseCandidate(
        test_point_id=tp.id,
        raw=payload,
        generation_mode=GenerationMode.LLM,
        data_plan=list(plan.data_plan),
        issues=[],
        calls=1,
    )
