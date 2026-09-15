"""V2 Step 4 等价类策略（EQUIVALENCE_CLASS）- FieldSpec 属性 → 合法类 + 非法类。

派生规则（首批覆盖 6 种字段属性，每种独立派生 obligation）：
  - enum_values 非空    → N 个合法类（每枚举值）+ 1 个非法类（值不在枚举中）
  - pattern 非空        → 1 合法（匹配正则）+ 1 非法（不匹配）
  - required=True       → 1 合法（提供值）+ 1 非法（缺失/空字符串）
  - nullable=False      → 1 合法（非 null）+ 1 非法（null 值）
  - unique=True         → 1 合法（新值）+ 1 非法（已存在值）  [plan Q4 推荐]
  - data_type ∈ {email,phone,url,id_card} → 1 合法格式 + 1 非法格式  [plan Q2 推荐]

一个 FieldSpec 可同时命中多条规则 → 派生多个独立 obligation（语义不同，分开覆盖）。

★ Step 4 修订（用户补充）：Strategy Engine 不生成真实测试数据。
  等价类只产出抽象类标识（例如 class="invalid_pattern"），具体测试数据
  （例如非法邮箱 "abc"）由 Step 5 TestCase 合成阶段的 TestDataGenerator 生成。
  这样分层更干净：Strategy 负责"测什么类"，TestCase 负责"用什么数据测"。

CoverageObligation.params 结构（按 kind 分，不含具体数据样例）：
  - enum:             {"field","label","kind":"enum","enum_values":[...]}
  - pattern:          {"field","label","kind":"pattern","pattern":"..."}
  - required:         {"field","label","kind":"required"}
  - nullable:         {"field","label","kind":"nullable"}
  - unique:           {"field","label","kind":"unique"}
  - data_type_format: {"field","label","kind":"data_type_format","data_type":"email"}

TestPoint.strategy_params 结构（用于 fingerprint 区分同 obligation 下多个点）：
  - enum 合法:        {"class":"valid_enum_value","value":"active"}
  - enum 非法:        {"class":"invalid_enum_value"}
  - pattern 合法:     {"class":"valid_pattern"}
  - pattern 非法:     {"class":"invalid_pattern"}
  - required 合法:    {"class":"required_provided"}
  - required 非法:    {"class":"required_missing"}
  - nullable 合法:    {"class":"non_null_value"}
  - nullable 非法:    {"class":"null_value"}
  - unique 合法:      {"class":"unique_new_value"}
  - unique 非法:      {"class":"unique_duplicate_value"}
  - format 合法:      {"class":"valid_format","data_type":"email"}
  - format 非法:      {"class":"invalid_format","data_type":"email"}
"""

from __future__ import annotations

import logging

from core.schemas import (
    CoverageObligation,
    DataType,
    GenerationScope,
    Priority,
    Provenance,
    RequirementItem,
    Technique,
    TestDimension,
    TestPoint,
)

logger = logging.getLogger("v2.strategy.equivalence")

# 需要派生格式等价类的 data_type 集合（plan Q2 推荐）
_FORMAT_DATA_TYPES = {
    DataType.EMAIL.value,
    DataType.PHONE.value,
    DataType.URL.value,
    DataType.ID_CARD.value,
}


# ============================================================
# Obligation 派生
# ============================================================


def derive_equivalence_obligations(item: RequirementItem, run_id: str) -> list[CoverageObligation]:
    """遍历 item.fields，按 6 种属性规则派生 EQUIVALENCE_CLASS obligation。

    一个 field 可命中多条规则 → 派生多个独立 obligation。
    obligation 顺序按 kind 固定：enum → pattern → required → nullable → unique → data_type_format。
    """
    obligations: list[CoverageObligation] = []
    for field in item.fields:
        label = field.label or field.name

        # 1. enum_values
        if field.enum_values:
            obligations.append(
                CoverageObligation(
                    run_id=run_id,
                    item_id=item.id,
                    technique=Technique.EQUIVALENCE_CLASS,
                    target=f"{field.name}.enum",
                    description=f"字段 {label} 的枚举值等价类覆盖（合法值：{field.enum_values}）",
                    params={
                        "field": field.name,
                        "label": label,
                        "kind": "enum",
                        "enum_values": list(field.enum_values),
                    },
                )
            )

        # 2. pattern
        if field.pattern:
            obligations.append(
                CoverageObligation(
                    run_id=run_id,
                    item_id=item.id,
                    technique=Technique.EQUIVALENCE_CLASS,
                    target=f"{field.name}.pattern",
                    description=f"字段 {label} 的正则模式等价类覆盖（pattern={field.pattern}）",
                    params={
                        "field": field.name,
                        "label": label,
                        "kind": "pattern",
                        "pattern": field.pattern,
                    },
                )
            )

        # 3. required=True
        if field.required:
            obligations.append(
                CoverageObligation(
                    run_id=run_id,
                    item_id=item.id,
                    technique=Technique.EQUIVALENCE_CLASS,
                    target=f"{field.name}.required",
                    description=f"字段 {label} 的必填校验等价类覆盖",
                    params={
                        "field": field.name,
                        "label": label,
                        "kind": "required",
                    },
                )
            )

        # 4. nullable=False（独立于 required，语义不同：required=字段必须出现，nullable=值不可为 null）
        if not field.nullable:
            obligations.append(
                CoverageObligation(
                    run_id=run_id,
                    item_id=item.id,
                    technique=Technique.EQUIVALENCE_CLASS,
                    target=f"{field.name}.nullable",
                    description=f"字段 {label} 的不可为 null 等价类覆盖",
                    params={
                        "field": field.name,
                        "label": label,
                        "kind": "nullable",
                    },
                )
            )

        # 5. unique=True（plan Q4 推荐）
        if field.unique:
            obligations.append(
                CoverageObligation(
                    run_id=run_id,
                    item_id=item.id,
                    technique=Technique.EQUIVALENCE_CLASS,
                    target=f"{field.name}.unique",
                    description=f"字段 {label} 的唯一性等价类覆盖",
                    params={
                        "field": field.name,
                        "label": label,
                        "kind": "unique",
                    },
                )
            )

        # 6. data_type 格式类（plan Q2 推荐）
        dt_value = field.data_type.value if hasattr(field.data_type, "value") else str(field.data_type)
        if dt_value in _FORMAT_DATA_TYPES:
            obligations.append(
                CoverageObligation(
                    run_id=run_id,
                    item_id=item.id,
                    technique=Technique.EQUIVALENCE_CLASS,
                    target=f"{field.name}.format",
                    description=f"字段 {label} 的 {dt_value} 格式等价类覆盖",
                    params={
                        "field": field.name,
                        "label": label,
                        "kind": "data_type_format",
                        "data_type": dt_value,
                    },
                )
            )
    return obligations


# ============================================================
# TestPoint 派生（1:N）
# ============================================================


def _make_tp(
    ob: CoverageObligation,
    item: RequirementItem,
    *,
    title: str,
    description: str,
    strategy_params: dict,
) -> TestPoint:
    """按 plan D5 规则构造 strategy TestPoint（fingerprint 由 deriver 层统一计算）。"""
    return TestPoint(
        run_id=ob.run_id,
        version_id=item.version_id,
        item_ids=[item.id],
        module=item.module,
        subcategory="等价类",
        title=title,
        description=description,
        dimension=TestDimension.EQUIVALENCE,
        technique=Technique.EQUIVALENCE_CLASS,
        obligation_id=ob.id,
        priority=item.priority_hint or Priority.P1,
        provenance=Provenance.STRATEGY,
        generation_scope=GenerationScope.STRATEGY,
        strategy_params=strategy_params,
    )


# 具体测试数据由 Step 5 TestDataGenerator 生成，此处只描述抽象类
_DATA_HINT = "具体测试数据由 Step 5 TestCase 合成阶段的 TestDataGenerator 生成。"


def derive_equivalence_testpoints(ob: CoverageObligation, item: RequirementItem) -> list[TestPoint]:
    """从 EQUIVALENCE_CLASS obligation 派生 TestPoint 列表（1:N，按 params.kind 分派）。

    ★ 不生成真实测试数据，只产出抽象类标识（见模块 docstring）。
    """
    if ob.technique != Technique.EQUIVALENCE_CLASS:
        return []
    params = ob.params or {}
    kind = params.get("kind")
    label = params.get("label") or params.get("field", "")

    if kind == "enum":
        enum_values = params.get("enum_values") or []
        tps: list[TestPoint] = []
        for v in enum_values:
            tps.append(
                _make_tp(
                    ob,
                    item,
                    title=f"{label} 枚举值 {v} 合法",
                    description=(
                        f"验证字段「{label}」取枚举值「{v}」时应被接受。合法枚举集合：{enum_values}。预期：接受。"
                    ),
                    strategy_params={"class": "valid_enum_value", "value": v},
                )
            )
        tps.append(
            _make_tp(
                ob,
                item,
                title=f"{label} 枚举外值非法",
                description=(
                    f"验证字段「{label}」取不在枚举集合中的值时应被拒绝。"
                    f"合法枚举集合：{enum_values}。预期：拒绝并提示。{_DATA_HINT}"
                ),
                strategy_params={"class": "invalid_enum_value"},
            )
        )
        return tps

    if kind == "pattern":
        pattern = params.get("pattern", "")
        return [
            _make_tp(
                ob,
                item,
                title=f"{label} 匹配正则合法",
                description=(f"验证字段「{label}」取匹配正则「{pattern}」的值时应被接受。预期：接受。{_DATA_HINT}"),
                strategy_params={"class": "valid_pattern"},
            ),
            _make_tp(
                ob,
                item,
                title=f"{label} 不匹配正则非法",
                description=(
                    f"验证字段「{label}」取不匹配正则「{pattern}」的值时应被拒绝。"
                    f"预期：拒绝并提示格式错误。{_DATA_HINT}"
                ),
                strategy_params={"class": "invalid_pattern"},
            ),
        ]

    if kind == "required":
        return [
            _make_tp(
                ob,
                item,
                title=f"{label} 必填提供值合法",
                description=(f"验证字段「{label}」提供有效值时应被接受。预期：接受。{_DATA_HINT}"),
                strategy_params={"class": "required_provided"},
            ),
            _make_tp(
                ob,
                item,
                title=f"{label} 必填缺失非法",
                description=(
                    f"验证字段「{label}」缺失或为空字符串时应被拒绝。"
                    f"必填字段不可省略。预期：拒绝并提示必填。{_DATA_HINT}"
                ),
                strategy_params={"class": "required_missing"},
            ),
        ]

    if kind == "nullable":
        return [
            _make_tp(
                ob,
                item,
                title=f"{label} 非 null 值合法",
                description=(f"验证字段「{label}」取非 null 值时应被接受。预期：接受。{_DATA_HINT}"),
                strategy_params={"class": "non_null_value"},
            ),
            _make_tp(
                ob,
                item,
                title=f"{label} null 值非法",
                description=(
                    f"验证字段「{label}」取 null 值时应被拒绝。该字段声明为不可为 null。预期：拒绝并提示。{_DATA_HINT}"
                ),
                strategy_params={"class": "null_value"},
            ),
        ]

    if kind == "unique":
        return [
            _make_tp(
                ob,
                item,
                title=f"{label} 唯一新值合法",
                description=(f"验证字段「{label}」取系统中未存在的新值时应被接受。预期：接受。{_DATA_HINT}"),
                strategy_params={"class": "unique_new_value"},
            ),
            _make_tp(
                ob,
                item,
                title=f"{label} 唯一重复值非法",
                description=(
                    f"验证字段「{label}」取系统中已存在的值时应被拒绝。"
                    f"该字段声明为唯一。预期：拒绝并提示重复。{_DATA_HINT}"
                ),
                strategy_params={"class": "unique_duplicate_value"},
            ),
        ]

    if kind == "data_type_format":
        dt = params.get("data_type", "")
        return [
            _make_tp(
                ob,
                item,
                title=f"{label} {dt} 格式合法",
                description=(f"验证字段「{label}」取符合 {dt} 格式的值时应被接受。预期：接受。{_DATA_HINT}"),
                strategy_params={"class": "valid_format", "data_type": dt},
            ),
            _make_tp(
                ob,
                item,
                title=f"{label} {dt} 格式非法",
                description=(
                    f"验证字段「{label}」取不符合 {dt} 格式的值时应被拒绝。预期：拒绝并提示格式错误。{_DATA_HINT}"
                ),
                strategy_params={"class": "invalid_format", "data_type": dt},
            ),
        ]

    logger.warning("未知 equivalence kind=%s，跳过派生", kind)
    return []
