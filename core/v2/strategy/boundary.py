"""V2 Step 4 边界值策略（BOUNDARY_VALUE）- FieldSpec.min/max_value + min/max_length → 六点边界。

派生规则（首批只做整数步长=1，float 精度处理留待迭代，见 plan Q1）：
  - min + max 都有 → 6 点：min-1, min, min+1, max-1, max, max+1
  - 只有 min → 3 点：min-1, min, min+1
  - 只有 max → 3 点：max-1, max, max+1
  - 边界重合（例如 min=1, max=2）→ 去重后点数减少
  - min == max → 3 点：min-1, min, min+1（max-1/max/max+1 与 min 侧重合）

CoverageObligation.params 结构：
  {"field": "age", "kind": "value"|"length", "min": ..., "max": ..., "points": [...]}

TestPoint 派生（1:N）：每个边界点一个 TestPoint。
  - 越界点（min-1 / max+1）：预期拒绝
  - 合法边界（min / max）：预期接受
  - 内点（min+1 / max-1）：预期接受
"""

from __future__ import annotations

import logging

from core.schemas import (
    CoverageObligation,
    GenerationScope,
    Priority,
    Provenance,
    RequirementItem,
    Technique,
    TestDimension,
    TestPoint,
)

logger = logging.getLogger("v2.strategy.boundary")

# 首批步长固定为 1（int 语义）；float precision 处理留待迭代
_STEP = 1


def _compute_boundary_points(
    min_val: float | int | None, max_val: float | int | None, step: int = _STEP
) -> list[float | int]:
    """计算边界点集合（去重 + 升序）。

    - min 存在：加 min-step, min, min+step
    - max 存在：加 max-step, max, max+step
    - 重合自动去重（例如 min=1,max=2 → [0,1,2,3]）
    """
    points: set[float | int] = set()
    if min_val is not None:
        points.update([min_val - step, min_val, min_val + step])
    if max_val is not None:
        points.update([max_val - step, max_val, max_val + step])
    return sorted(points)


def _classify_point(p: float | int, min_val: float | int | None, max_val: float | int | None) -> str:
    """判定边界点的语义类别：'below' / 'at_min' / 'above_min' / 'below_max' / 'at_max' / 'above'。

    用于 TestPoint.description 里说明"预期接受 vs 预期拒绝"。
    """
    if min_val is not None and p < min_val:
        return "below"  # 越下界 → 拒绝
    if min_val is not None and p == min_val:
        return "at_min"  # 下界 → 接受
    if max_val is not None and p > max_val:
        return "above"  # 越上界 → 拒绝
    if max_val is not None and p == max_val:
        return "at_max"  # 上界 → 接受
    return "inside"  # 内点 → 接受


_EXPECTATION = {
    "below": "预期被拒绝（低于下界）",
    "at_min": "预期被接受（合法下界）",
    "inside": "预期被接受（区间内点）",
    "at_max": "预期被接受（合法上界）",
    "above": "预期被拒绝（高于上界）",
}

# 边界点类型标识（写入 strategy_params.boundary_type，用于 fingerprint 区分同 obligation 下多个点）
_BOUNDARY_TYPE = {
    "below": "min_minus_1",
    "at_min": "min",
    "inside_near_min": "min_plus_1",
    "inside_near_max": "max_minus_1",
    "at_max": "max",
    "above": "max_plus_1",
}


def _boundary_type_of(p: float | int, min_val: float | int | None, max_val: float | int | None) -> str:
    """判定边界点的具体类型标识（写入 strategy_params.boundary_type）。"""
    if min_val is not None:
        if p == min_val - _STEP:
            return "min_minus_1"
        if p == min_val:
            return "min"
        if p == min_val + _STEP:
            return "min_plus_1"
    if max_val is not None:
        if p == max_val - _STEP:
            return "max_minus_1"
        if p == max_val:
            return "max"
        if p == max_val + _STEP:
            return "max_plus_1"
    return "inside"  # 理论上不会走到（_compute_boundary_points 只生成 6 类点）


def _format_num(v: float | int | None) -> str:
    """数值格式化：整数值的 float（1.0）显示为 int（1），避免 title 出现冗余的 .0。

    FieldSpec.min_value/max_value 声明为 float | None，Pydantic 会把 int 输入转为 float；
    为让 TestPoint.title/description 对人类友好，此处统一格式化。
    """
    if v is None:
        return "None"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# ============================================================
# Obligation 派生
# ============================================================


def derive_boundary_obligations(item: RequirementItem, run_id: str) -> list[CoverageObligation]:
    """遍历 item.fields，对有 min/max_value 或 min/max_length 的字段派生 BOUNDARY_VALUE obligation。

    - 数值边界（min_value/max_value）：target = field.name
    - 长度边界（min_length/max_length）：target = field.name + ".length"
    - 两者都有 → 派生两个独立 obligation（数值与长度语义不同，分开覆盖）
    """
    obligations: list[CoverageObligation] = []
    for field in item.fields:
        label = field.label or field.name

        # 数值边界
        if field.min_value is not None or field.max_value is not None:
            points = _compute_boundary_points(field.min_value, field.max_value)
            obligations.append(
                CoverageObligation(
                    run_id=run_id,
                    item_id=item.id,
                    technique=Technique.BOUNDARY_VALUE,
                    target=field.name,
                    description=f"字段 {label} 的数值边界覆盖（min={field.min_value}, max={field.max_value}）",
                    params={
                        "field": field.name,
                        "label": label,
                        "kind": "value",
                        "min": field.min_value,
                        "max": field.max_value,
                        "points": points,
                    },
                )
            )

        # 长度边界
        if field.min_length is not None or field.max_length is not None:
            points = _compute_boundary_points(field.min_length, field.max_length)
            obligations.append(
                CoverageObligation(
                    run_id=run_id,
                    item_id=item.id,
                    technique=Technique.BOUNDARY_VALUE,
                    target=f"{field.name}.length",
                    description=f"字段 {label} 的长度边界覆盖（min_length={field.min_length}, max_length={field.max_length}）",
                    params={
                        "field": field.name,
                        "label": label,
                        "kind": "length",
                        "min": field.min_length,
                        "max": field.max_length,
                        "points": points,
                    },
                )
            )
    return obligations


# ============================================================
# TestPoint 派生（1:N）
# ============================================================


def derive_boundary_testpoints(ob: CoverageObligation, item: RequirementItem) -> list[TestPoint]:
    """从 BOUNDARY_VALUE obligation 派生 TestPoint 列表（每个边界点一个）。

    TestPoint 字段规则（对应 plan D5）：
      - provenance=STRATEGY, generation_scope=STRATEGY, technique=BOUNDARY_VALUE, obligation_id=ob.id
      - module=item.module, subcategory="边界值", dimension=BOUNDARY
      - priority=item.priority_hint 或 P1
      - item_ids=[item.id]（strategy TestPoint 天然单 item 追溯）
      - fingerprint 由 deriver 层统一计算（此处不设，避免与 Step 3 公式混淆）
    """
    if ob.technique != Technique.BOUNDARY_VALUE:
        return []
    params = ob.params or {}
    if params.get("kind") not in ("value", "length"):
        return []

    field_name = params.get("field", "")
    label = params.get("label") or field_name
    kind = params["kind"]
    min_val = params.get("min")
    max_val = params.get("max")
    points = params.get("points") or []

    kind_label = "数值" if kind == "value" else "长度"
    priority = item.priority_hint or Priority.P1

    testpoints: list[TestPoint] = []
    for p in points:
        cls = _classify_point(p, min_val, max_val)
        expectation = _EXPECTATION[cls]
        p_str = _format_num(p)
        btype = _boundary_type_of(p, min_val, max_val)

        # 合理性提示（第一版简单规则）：当 min ≤ 0 且 boundary_type=min_minus_1 时，
        # min-1 可能为负数，对年龄/数量等语义字段可能不合理，加 warn 标记 + description 提示。
        # 复杂业务语义判断（data_type/unit/是否允许负数）留待迭代，见 plan Q1。
        warn = None
        if btype == "min_minus_1" and min_val is not None and min_val <= 0:
            warn = "negative_value_may_be_invalid"

        title = f"{label} {kind_label}边界 {p_str}"
        warn_note = (
            "注意：该值可能为负数或零以下，若业务语义不允许（例如年龄/数量/长度），需人工确认此测试点的合理性。"
            if warn
            else ""
        )
        description = (
            f"验证字段「{label}」在{kind_label}边界点 {p_str} 处的行为。"
            f"约束：min={_format_num(min_val)}, max={_format_num(max_val)}。{expectation}。"
            f"边界值分析是测试设计基础技术，缺陷常在边界处集中。"
            f"{warn_note}"
        )

        # strategy_params：用于 fingerprint 区分同 obligation 下多个点（避免仅靠 title 区分不够可靠）
        strategy_params: dict = {
            "boundary_type": btype,
            "value": p,
            "kind": kind,  # "value" | "length"
        }
        if warn:
            strategy_params["warn"] = warn

        tp = TestPoint(
            run_id=ob.run_id,
            version_id=item.version_id,
            item_ids=[item.id],
            module=item.module,
            subcategory="边界值",
            title=title,
            description=description,
            dimension=TestDimension.BOUNDARY,
            technique=Technique.BOUNDARY_VALUE,
            obligation_id=ob.id,
            priority=priority,
            provenance=Provenance.STRATEGY,
            generation_scope=GenerationScope.STRATEGY,
            strategy_params=strategy_params,
        )
        testpoints.append(tp)
    return testpoints
