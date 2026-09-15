"""V2 IR 校验器 - 代码级确定性校验/规范化（Validator 阶段）。

在 Parser(LLM 抽取) 之后、持久化之前对 IR 做代码把关，体现"结构化数据 → Validator → 结构化数据"：
  - 去重（module + 归一化 statement）
  - 结构一致性检查（data_field 应有 fields、permission 应有 permissions…）→ warnings
  - 置信度分级已由模型 before-validator 完成，此处汇总低置信项供人工确认（§7）
  - seq 重排（去重后 1..N 连续）
  - 产出结构化校验报告（代码生成，非 LLM）
注：Schema 级非法项已在 parser.coerce_item 丢弃，此处不重复。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.schemas import ConfidenceLevel, RequirementItem, RequirementItemType

_PUNCT = re.compile(r"[\s，。、；：,.:;！!？?\"'（）()\[\]【】]+")


@dataclass
class IRValidationResult:
    """IR 校验结果（结构化，代码生成）"""

    items: list[RequirementItem] = field(default_factory=list)  # 去重 + 重排后的干净 items
    duplicates_removed: int = 0
    low_confidence_ids: list[str] = field(default_factory=list)  # 需人工确认的需求项
    warnings: list[str] = field(default_factory=list)  # 结构一致性提醒（不丢弃）


def _normalize_statement(statement: str) -> str:
    """归一化用于去重比较：去空白/标点、转小写"""
    return _PUNCT.sub("", (statement or "").strip().lower())


def validate_ir(items: list[RequirementItem]) -> IRValidationResult:
    """对解析出的 IR 做代码级校验与规范化"""
    result = IRValidationResult()
    seen: set[str] = set()
    kept: list[RequirementItem] = []

    for it in items:
        key = f"{it.module}::{_normalize_statement(it.statement)}"
        if key in seen:
            result.duplicates_removed += 1
            continue
        seen.add(key)
        kept.append(it)

    # seq 重排 + 结构检查 + 低置信标记
    for i, it in enumerate(kept, 1):
        it.seq = i  # 去重后重排为连续序号
        if it.type == RequirementItemType.DATA_FIELD and not it.fields:
            result.warnings.append(f"item#{i} 标记 data_field 但无 fields：{it.statement[:24]}")
        elif it.type == RequirementItemType.PERMISSION and not it.permissions:
            result.warnings.append(f"item#{i} 标记 permission 但无 permissions：{it.statement[:24]}")
        elif it.type == RequirementItemType.BUSINESS_RULE and not it.rules:
            result.warnings.append(f"item#{i} 标记 business_rule 但无 rules：{it.statement[:24]}")
        if it.confidence_level == ConfidenceLevel.LOW:
            result.low_confidence_ids.append(it.id)

    result.items = kept
    return result
