"""V2 Step 5 测试数据规划器（TestDataPlanner）- TestPoint → DataPlan[]。

职责拆分（用户核心设计）：TestPoint → DataPlan → TestCase，而非 TestDataGenerator 直接改 TestCase。
DataPlan 结构化（field/strategy/value/source/generator/expected_valid），持久化到 TestCase.data_plan，
便于审计数据来源、未来 Playwright/API/参数化执行复用、Step 9 人工编辑区分代码/LLM 数据。

★ 分工边界：
  - **STRATEGY TestPoint**：Step 4 只产出抽象类标识（如 class="invalid_pattern"），
    本模块据 obligation.params + strategy_params + FieldSpec 生成**具体测试数据**。
    Code-first（数据来源优先级）+ LLM fallback（复杂正则），LLM 产物必过 re.fullmatch 验证。
  - **LLM TestPoint**：无确定性字段数据需求，DataPlan 留空；
    LLM 合成器直接消费 TestPoint + RequirementItem context（已含字段 example）。

数据来源优先级（用户拍板，Code Generator 内部）：
  1. FieldSpec.example   2. FieldSpec.default   3. strategy_params.value
  4. enum_values         5. data_type 内置样例库  6. 必要时 LLM（复杂正则）

★ 核心原则：LLM 可以生成测试数据，但不能自证正确——复杂正则样例必须过代码 re.fullmatch 验证。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from core.schemas import (
    CoverageObligation,
    DataPlanItem,
    FieldSpec,
    Provenance,
    RequirementItem,
    Technique,
    TestPoint,
)
from core.v2.parser import extract_json
from core.v2.tc_prompts import PATTERN_DATA_USER_TEMPLATE, TEST_DATA_PATTERN_PROMPT

logger = logging.getLogger("v2.test_data_planner")

# LLM 兜底生成复杂正则样例的最大重试次数（每次都用 re.fullmatch 验证）
_PATTERN_LLM_MAX_RETRIES = 3


# ============================================================
# 内置样例库（data_type → 合法/非法样例）
# ============================================================


def _make_valid_id_card(body17: str = "11010119900307457") -> str:
    """生成校验位正确的 18 位身份证号（GB 11643 加权模 11-2 算法）。"""
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_map = "10X98765432"
    s = sum(int(body17[i]) * weights[i] for i in range(17))
    return body17 + check_map[s % 11]


_VALID_FORMAT_SAMPLES = {
    "email": "user@example.com",
    "phone": "13800138000",
    "url": "https://example.com/path",
    "id_card": _make_valid_id_card(),
}
_INVALID_FORMAT_SAMPLES = {
    "email": "abc",
    "phone": "12345",
    "url": "not-a-url",
    "id_card": "123",
}
# 非格式类 data_type 的通用合法样例
_GENERIC_VALID_SAMPLES = {
    "string": "示例文本",
    "int": 1,
    "float": 1.0,
    "bool": True,
    "date": "2024-01-01",
    "datetime": "2024-01-01 12:00:00",
    "enum": "value1",
}
# 非法正则样例的通用候选（逐个用 re.fullmatch 验证「不匹配」后采用）
_INVALID_PATTERN_CANDIDATES = ["", "!!!", "abc", " ", "@@@", "123", "null", "非法值"]


# ============================================================
# DataPlan 产物
# ============================================================


@dataclass
class DataPlanResult:
    """TestDataPlanner 产物 + 模板步骤生成所需的元数据。"""

    data_plan: list[DataPlanItem] = field(default_factory=list)
    used_llm: bool = False  # 是否用了 LLM 兜底（影响 generation_mode：True→HYBRID）
    llm_calls: int = 0  # LLM 兜底实际调用次数（复杂正则含重试）
    issues: list[str] = field(default_factory=list)
    # 以下元数据供 strategy TestPoint 的模板步骤生成消费
    technique: str = ""  # boundary_value / equivalence_class / permission_matrix
    strategy_class: str = ""  # strategy_params 的 class 或 boundary_type
    field_label: str = ""  # 人类可读字段名
    expected_valid: bool = True  # 整体预期（合法/非法），用于 expected 文案
    precondition_hint: str = ""  # 前置条件提示（如"系统中已存在该值"）


# ============================================================
# 通用 helper
# ============================================================


def _find_field(item: RequirementItem | None, field_name: str) -> FieldSpec | None:
    """按字段名从 RequirementItem.fields 查找 FieldSpec。"""
    if item is None or not field_name:
        return None
    for f in item.fields:
        if f.name == field_name:
            return f
    return None


def _data_type_str(field: FieldSpec | None) -> str:
    """取 FieldSpec.data_type 的字符串值（兜底 string）。"""
    if field is None:
        return "string"
    dt = field.data_type
    return dt.value if hasattr(dt, "value") else str(dt)


def _resolve_valid_sample(field: FieldSpec | None, data_type: str | None = None) -> tuple[object, str]:
    """按数据来源优先级解析一个**合法**样例，返回 (value, source)。

    优先级：example → default → enum_values[0] → data_type 内置样例 → 通用样例。
    """
    if field is not None:
        if field.example:
            return field.example, "example"
        if field.default:
            return field.default, "default"
        if field.enum_values:
            return field.enum_values[0], "enum"
    dt = data_type or _data_type_str(field)
    if dt in _VALID_FORMAT_SAMPLES:
        return _VALID_FORMAT_SAMPLES[dt], "builtin"
    return _GENERIC_VALID_SAMPLES.get(dt, "test_value"), "builtin"


def _gen_invalid_enum(enum_values: list[str]) -> str:
    """生成一个保证不在 enum_values 集合中的非法枚举值。"""
    candidate = "INVALID_VALUE"
    i = 1
    while candidate in (enum_values or []):
        candidate = f"INVALID_VALUE_{i}"
        i += 1
    return candidate


# ============================================================
# 复杂正则样例生成（Code 启发式 + LLM 兜底 + re.fullmatch 验证）
# ============================================================


def _verify_pattern(pattern: str, sample: str, want_valid: bool) -> bool:
    """用 re.fullmatch 验证样例是否符合预期（want_valid=True 须完全匹配，False 须不匹配）。

    正则本身非法（re.error）时返回 False（无法验证 → 不接受）。
    """
    if sample is None:
        return False
    try:
        matched = re.fullmatch(pattern, str(sample)) is not None
    except re.error:
        logger.warning("正则非法，无法验证: pattern=%s", pattern)
        return False
    return matched == want_valid


def _heuristic_pattern_sample(pattern: str, want_valid: bool) -> str | None:
    """简单正则的代码启发式样例生成（覆盖常见模式；复杂正则返回 None 交给 LLM）。"""
    if not want_valid:
        # 非法样例：逐个候选验证「不匹配」
        for c in _INVALID_PATTERN_CANDIDATES:
            if _verify_pattern(pattern, c, want_valid=False):
                return c
        return None
    # 合法样例：针对常见 token 启发式构造
    p = pattern.strip()
    if p.startswith("^"):
        p = p[1:]
    if p.endswith("$"):
        p = p[:-1]
    # \d{n} / \d{m,n} / \d+
    m = re.search(r"\\d\{(\d+)\}", p)
    if m:
        return "1" * int(m.group(1))
    m = re.search(r"\\d\{(\d+),(\d+)\}", p)
    if m:
        return "1" * int(m.group(1))
    if re.search(r"\\d", p):
        return "12345"
    # [A-Za-z0-9_] 类
    if re.search(r"\[A-Za-z", p) or re.search(r"\[a-z", p):
        return "abc"
    if re.search(r"\[0-9\]", p):
        return "123"
    return None


def _llm_pattern_sample(
    client, pattern: str, field_name: str, field_label: str, want_valid: bool
) -> tuple[str | None, int]:
    """调 LLM 生成正则样例，并用 re.fullmatch 验证；最多重试 _PATTERN_LLM_MAX_RETRIES 次。

    ★ LLM 不能自证正确：每次返回都用代码验证，不符合预期则重试；全部失败返回 None。
    返回 (sample, calls)：calls 为实际 LLM 调用次数。
    """
    if client is None:
        return None, 0
    want = "匹配" if want_valid else "不匹配"
    match_desc = "re.fullmatch 应返回完全匹配" if want_valid else "re.fullmatch 应返回 None"
    user_prompt = PATTERN_DATA_USER_TEMPLATE.format(
        pattern=pattern,
        field_name=field_name or "(未知)",
        field_label=field_label or field_name or "(未知)",
        want=want,
        match_desc=match_desc,
    )
    calls = 0
    for attempt in range(1, _PATTERN_LLM_MAX_RETRIES + 1):
        calls += 1
        try:
            raw = client.chat(TEST_DATA_PATTERN_PROMPT, user_prompt)
        except Exception as e:  # noqa: BLE001 - LLM 调用失败需记录并重试
            logger.warning("正则样例 LLM 调用失败(第%d次): %s", attempt, e)
            continue
        payload = extract_json(raw)
        sample = None
        if isinstance(payload, dict):
            sample = payload.get("sample")
        elif isinstance(payload, str):
            sample = payload
        if sample is not None and _verify_pattern(pattern, str(sample), want_valid):
            return str(sample), calls
        logger.info(
            "正则样例 LLM 第%d次未通过 re.fullmatch 验证: pattern=%s want_valid=%s", attempt, pattern, want_valid
        )
    return None, calls


def _gen_pattern_sample(
    pattern: str, field: FieldSpec | None, want_valid: bool, client, field_name: str, field_label: str
) -> tuple[str | None, str, str, bool, int, list[str]]:
    """生成正则样例，返回 (value, source, generator, used_llm, llm_calls, issues)。

    顺序：FieldSpec.example（仅 want_valid 且匹配）→ 代码启发式 → LLM 兜底（均经 re.fullmatch 验证）。
    """
    # 1. example 优先（仅合法样例且确实匹配时采用）
    if want_valid and field is not None and field.example and _verify_pattern(pattern, field.example, True):
        return field.example, "example", "code", False, 0, []
    # 2. 代码启发式
    heuristic = _heuristic_pattern_sample(pattern, want_valid)
    if heuristic is not None and _verify_pattern(pattern, heuristic, want_valid):
        return heuristic, "builtin", "code", False, 0, []
    # 3. LLM 兜底（含 re.fullmatch 验证）
    llm_sample, calls = _llm_pattern_sample(client, pattern, field_name, field_label, want_valid)
    if llm_sample is not None:
        return llm_sample, "llm", "llm", True, calls, []
    want_label = "合法" if want_valid else "非法"
    return (
        None,
        "llm",
        "llm",
        bool(client),
        calls,
        [f"无法为 pattern={pattern} 生成{want_label}样例（代码启发式与 LLM 兜底均失败）"],
    )


# ============================================================
# STRATEGY TestPoint 的分技术规划
# ============================================================


def _plan_boundary(tp: TestPoint, ob: CoverageObligation, item: RequirementItem | None) -> DataPlanResult:
    """边界值：strategy_params.value 已是具体边界点，直接采用（length 类构造对应长度字符串）。"""
    sp = tp.strategy_params or {}
    params = ob.params or {}
    field_name = params.get("field", "")
    label = params.get("label") or field_name
    kind = sp.get("kind") or params.get("kind", "value")
    boundary_type = sp.get("boundary_type", "")
    raw_value = sp.get("value")
    # 预期合法性：min/max/内点 → 合法；min-1/max+1 → 非法
    expected_valid = boundary_type not in ("min_minus_1", "max_plus_1")

    if kind == "length":
        n = int(raw_value) if isinstance(raw_value, (int, float)) and raw_value >= 0 else 0
        value: object = "a" * n
    else:
        value = raw_value

    dp = DataPlanItem(
        field=field_name,
        strategy="boundary",
        value=value,
        source="strategy_params",
        generator="code",
        expected_valid=expected_valid,
    )
    warn = sp.get("warn")
    precondition = ""
    if warn:
        precondition = f"注意：{warn}（边界值可能超出业务合理范围，需人工确认）"
    return DataPlanResult(
        data_plan=[dp],
        used_llm=False,
        technique=Technique.BOUNDARY_VALUE.value,
        strategy_class=boundary_type,
        field_label=label,
        expected_valid=expected_valid,
        precondition_hint=precondition,
    )


def _plan_equivalence(tp: TestPoint, ob: CoverageObligation, item: RequirementItem | None, client) -> DataPlanResult:
    """等价类：按 strategy_params.class 分派，生成具体合法/非法数据。"""
    sp = tp.strategy_params or {}
    params = ob.params or {}
    cls = sp.get("class", "")
    field_name = params.get("field", "")
    label = params.get("label") or field_name
    fs = _find_field(item, field_name)
    issues: list[str] = []
    used_llm = False
    llm_calls = 0
    precondition = ""

    def _result(
        value: object, source: str, generator: str, expected_valid: bool, strategy: str = "equivalence"
    ) -> DataPlanResult:
        dp = DataPlanItem(
            field=field_name,
            strategy=strategy,
            value=value,
            source=source,
            generator=generator,
            expected_valid=expected_valid,
        )
        return DataPlanResult(
            data_plan=[dp],
            used_llm=used_llm,
            llm_calls=llm_calls,
            issues=issues,
            technique=Technique.EQUIVALENCE_CLASS.value,
            strategy_class=cls,
            field_label=label,
            expected_valid=expected_valid,
            precondition_hint=precondition,
        )

    if cls == "valid_enum_value":
        return _result(sp.get("value"), "enum", "code", True)
    if cls == "invalid_enum_value":
        return _result(_gen_invalid_enum(params.get("enum_values") or []), "enum", "code", False)

    if cls in ("valid_pattern", "invalid_pattern"):
        want_valid = cls == "valid_pattern"
        value, source, generator, used_llm_flag, calls, gen_issues = _gen_pattern_sample(
            params.get("pattern", ""), fs, want_valid, client, field_name, label
        )
        used_llm = used_llm_flag
        llm_calls = calls
        issues.extend(gen_issues)
        return _result(value, source, generator, want_valid)

    if cls == "required_provided":
        value, source = _resolve_valid_sample(fs)
        return _result(value, source, "code", True)
    if cls == "required_missing":
        return _result("", "builtin", "code", False)

    if cls == "non_null_value":
        value, source = _resolve_valid_sample(fs)
        return _result(value, source, "code", True)
    if cls == "null_value":
        return _result(None, "builtin", "code", False)

    if cls == "unique_new_value":
        value, source = _resolve_valid_sample(fs)
        precondition = f"前置：系统中不存在该 {label} 值"
        return _result(value, source, "code", True)
    if cls == "unique_duplicate_value":
        value, source = _resolve_valid_sample(fs)
        precondition = f"前置：系统中已存在一条 {label}={value} 的记录"
        return _result(value, source, "code", False)

    if cls in ("valid_format", "invalid_format"):
        dt = sp.get("data_type") or params.get("data_type") or _data_type_str(fs)
        want_valid = cls == "valid_format"
        if want_valid:
            value = (fs.example if fs and fs.example else None) or _VALID_FORMAT_SAMPLES.get(dt, "valid_sample")
            source = "example" if (fs and fs.example) else "builtin"
        else:
            value = _INVALID_FORMAT_SAMPLES.get(dt, "invalid_sample")
            source = "builtin"
        return _result(value, source, "code", want_valid)

    issues.append(f"未知 equivalence class={cls}，无法生成具体数据")
    return DataPlanResult(
        data_plan=[],
        used_llm=False,
        issues=issues,
        technique=Technique.EQUIVALENCE_CLASS.value,
        strategy_class=cls,
        field_label=label,
        expected_valid=True,
        precondition_hint="",
    )


def _plan_permission(tp: TestPoint, ob: CoverageObligation, item: RequirementItem | None) -> DataPlanResult:
    """权限矩阵：strategy_params 已含 role/resource/action/allowed，组装为数据计划。"""
    sp = tp.strategy_params or {}
    params = ob.params or {}
    role = sp.get("role", "")
    resource = sp.get("resource", "")
    action = sp.get("action", "")
    allowed = bool(sp.get("allowed", False))
    condition = params.get("condition")

    dp = DataPlanItem(
        field=f"{role}:{resource}:{action}",
        strategy="permission",
        value=f"role={role}, action={action}",
        source="strategy_params",
        generator="code",
        expected_valid=allowed,
    )
    precondition = f"前置：以「{role}」角色登录"
    if condition:
        precondition += f"；附加条件：{condition}"
    return DataPlanResult(
        data_plan=[dp],
        used_llm=False,
        technique=Technique.PERMISSION_MATRIX.value,
        strategy_class="permission",
        field_label=f"{role} {action} {resource}",
        expected_valid=allowed,
        precondition_hint=precondition,
    )


def _plan_for_strategy(
    tp: TestPoint, ob: CoverageObligation | None, item: RequirementItem | None, client
) -> DataPlanResult:
    """STRATEGY TestPoint 分技术派生具体测试数据。"""
    if ob is None:
        return DataPlanResult(
            data_plan=[],
            issues=[
                f"strategy TestPoint {tp.id} 缺少 obligation，无法生成具体数据（obligation_id={tp.obligation_id}）"
            ],
        )
    technique = tp.technique
    if technique == Technique.BOUNDARY_VALUE:
        return _plan_boundary(tp, ob, item)
    if technique == Technique.EQUIVALENCE_CLASS:
        return _plan_equivalence(tp, ob, item, client)
    if technique == Technique.PERMISSION_MATRIX:
        return _plan_permission(tp, ob, item)
    return DataPlanResult(
        data_plan=[],
        issues=[f"strategy TestPoint {tp.id} 的 technique={technique} 首批不支持数据规划"],
    )


# ============================================================
# 顶层入口
# ============================================================


def plan_data_for_testpoint(
    tp: TestPoint,
    *,
    obligation: CoverageObligation | None = None,
    item: RequirementItem | None = None,
    client=None,
) -> DataPlanResult:
    """为一个 TestPoint 规划测试数据 → DataPlanResult。

    - STRATEGY TestPoint：据 obligation.params + strategy_params + FieldSpec 生成具体数据
      （Code-first + LLM fallback，LLM 产物必过 re.fullmatch 验证）。
    - LLM TestPoint：DataPlan 留空（LLM 合成器直接消费 TestPoint + RequirementItem context，
      其中已含字段 example/default）。generation_mode 由编排层据 provenance 判定为 LLM。

    参数：
      - obligation：strategy TestPoint 回链的 CoverageObligation（LLM TestPoint 传 None）
      - item：关联的主 RequirementItem（用于取 FieldSpec 的 example/default；cross_item 传首个）
      - client：LLM 客户端（复杂正则兜底用；为 None 时跳过 LLM，仅代码生成）
    """
    if tp.provenance == Provenance.STRATEGY:
        return _plan_for_strategy(tp, obligation, item, client)
    # LLM TestPoint：无确定性字段数据需求，DataPlan 留空
    return DataPlanResult(data_plan=[], used_llm=False, issues=[], technique="", strategy_class="")
