"""V2 测试点生成器 - LLM 调用 + 鲁棒 JSON 提取（Step 3）。

分工：
  - 本模块只负责 **调 LLM + 提取 JSON**，产出"原始候选 dict 列表"（未经 Pydantic 校验）。
  - 规范化/枚举兜底/item_ids 归一/去重/fingerprint 计算 → 交给 `tp_validator.py`。
  - Run/GenerationConfig/持久化编排 → 交给 `tp_orchestrator.py`。

两阶段生成（对应 tp_prompts.py）：
  - Phase A `generate_for_item`：逐 RequirementItem 独立调用 LLM。
  - Phase B `complete_cross_item`：全量 items + Phase A 摘要 → 单次调用 LLM 补跨项；
    items 数量超阈值时按 module 分批。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from core.schemas import RequirementItem, TestPoint
from core.v2.parser import extract_json  # 复用 Step 2 的鲁棒 JSON 提取
from core.v2.tp_prompts import (
    COMPLETER_USER_TEMPLATE,
    GENERATOR_USER_TEMPLATE,
    TEST_POINT_COMPLETER_PROMPT,
    TEST_POINT_GENERATOR_PROMPT,
)

logger = logging.getLogger("v2.tp_generator")

# Phase B 分批阈值：items 数量超过此值时按 module 分组分批调用（每批独立补漏）
PHASE_B_BATCH_THRESHOLD = 30

# Phase B 摘要里每个 item 展示的字段（控制 token；不展示 fields/rules/permissions 详情）
_ITEM_SUMMARY_FIELDS = ("id", "type", "module", "statement", "priority_hint", "confidence")


@dataclass
class GeneratorResult:
    """单阶段 LLM 调用的原始产出（未过 Pydantic）。"""

    raw_points: list[dict] = field(default_factory=list)  # LLM 输出的候选 dict 列表
    issues: list[str] = field(default_factory=list)  # LLM 调用/JSON 提取失败记录
    raw_response: str = ""  # 原始 LLM 响应（调试/审计用）
    calls: int = 0  # 实际 LLM 调用次数（Phase B 分批时 > 1）


# ============================================================
# 序列化工具
# ============================================================


def _item_to_json(item: RequirementItem) -> str:
    """RequirementItem → JSON 字符串（用于 Phase A user prompt）。

    只保留 LLM 生成测试点需要的字段，剔除 version_id/seq/provenance/status/confidence_level
    等内部审计字段，降低 token 消耗与干扰。
    """
    data = item.model_dump(mode="json", exclude_none=True)
    # 剔除内部审计字段
    for k in ("version_id", "seq", "provenance", "status", "confidence_level", "schema_version", "id"):
        data.pop(k, None)
    # 保留 id 用于 Phase B 引用；Phase A 单独注入
    return json.dumps(data, ensure_ascii=False, indent=2)


def _item_summary_line(item: RequirementItem) -> str:
    """RequirementItem → 单行摘要（用于 Phase B items 列表）。"""
    parts = [f"id={item.id}"]
    for f in _ITEM_SUMMARY_FIELDS:
        if f == "id":
            continue
        v = getattr(item, f, None)
        if v is None:
            continue
        if hasattr(v, "value"):  # 枚举
            v = v.value
        parts.append(f"{f}={v}")
    # 附加 fields/rules/permissions 数量提示，帮助 LLM 判断跨项联动潜力
    if item.fields:
        parts.append(f"fields=[{','.join(f.name for f in item.fields)}]")
    if item.rules:
        parts.append(f"rules=[{','.join(r.name for r in item.rules if r.name)}]")
    if item.permissions:
        parts.append(f"permissions={len(item.permissions)}条")
    return " | ".join(parts)


def _point_summary_line(tp: TestPoint | dict) -> str:
    """TestPoint → 单行摘要（用于 Phase B 已产出摘要）。"""
    if isinstance(tp, TestPoint):
        item_ids = list(tp.item_ids)
        module, subcategory, title = tp.module, tp.subcategory, tp.title
        dim = tp.dimension.value if hasattr(tp.dimension, "value") else str(tp.dimension)
    else:
        item_ids = list(tp.get("item_ids") or [])
        module = tp.get("module", "")
        subcategory = tp.get("subcategory", "")
        title = tp.get("title", "")
        dim = tp.get("dimension", "")
    ids_repr = ",".join(item_ids) if item_ids else "-"
    return f"[{dim}] {module} / {subcategory} / {title} (items={ids_repr})"


# ============================================================
# Phase A：逐 item 独立生成
# ============================================================


def generate_for_item(client, item: RequirementItem) -> GeneratorResult:
    """针对单个 RequirementItem 调 LLM 生成基础 TestPoint 候选。

    client 需实现 .chat(system_prompt, user_prompt, images=None) -> str（V1 LLMClient 接口）。
    返回原始 dict 列表 + issues；规范化与 Pydantic 校验交给 tp_validator。
    """
    user_prompt = GENERATOR_USER_TEMPLATE.format(item_json=_item_to_json(item))
    # Phase A 显式告诉 LLM 当前 item 的 id，方便代码侧兜底覆写 item_ids
    user_prompt = (
        f"（当前需求项 id = {item.id}；生成的测试点将自动关联到此 id，你不需要输出 item_ids 字段。）\n\n{user_prompt}"
    )

    try:
        raw = client.chat(TEST_POINT_GENERATOR_PROMPT, user_prompt)
    except Exception as e:
        logger.exception("Phase A LLM 调用失败: item=%s", item.id)
        return GeneratorResult(issues=[f"Phase A LLM 调用失败 item={item.id}: {e}"], calls=1)

    payload = extract_json(raw)
    if payload is None:
        return GeneratorResult(issues=[f"Phase A 无法解析 JSON: item={item.id}"], raw_response=raw, calls=1)

    raw_points = payload.get("test_points") if isinstance(payload, dict) else payload
    if not isinstance(raw_points, list):
        return GeneratorResult(issues=[f"Phase A 响应缺少 test_points 数组: item={item.id}"], raw_response=raw, calls=1)

    # 只保留 dict 元素
    cleaned = [p for p in raw_points if isinstance(p, dict)]
    dropped = len(raw_points) - len(cleaned)
    issues = []
    if dropped:
        issues.append(f"Phase A 丢弃 {dropped} 个非对象元素: item={item.id}")
    return GeneratorResult(raw_points=cleaned, issues=issues, raw_response=raw, calls=1)


# ============================================================
# Phase B：跨项补漏（含按 module 分批）
# ============================================================


def _group_items_by_module(items: list[RequirementItem]) -> dict[str, list[RequirementItem]]:
    """按 module 分组（保持首次出现顺序）。"""
    groups: dict[str, list[RequirementItem]] = {}
    for it in items:
        groups.setdefault(it.module or "未分类", []).append(it)
    return groups


def _build_phase_b_user_prompt(
    items: list[RequirementItem],
    existing_points: list[TestPoint] | list[dict],
) -> str:
    """组装 Phase B 的 user prompt：items 摘要 + 已产出摘要。"""
    items_summary = "\n".join(f"- {_item_summary_line(it)}" for it in items) or "- （无）"
    phase_a_summary = "\n".join(f"- {_point_summary_line(p)}" for p in existing_points) or "- （Phase A 未产出）"
    return COMPLETER_USER_TEMPLATE.format(
        item_count=len(items),
        items_summary=items_summary,
        phase_a_count=len(existing_points),
        phase_a_summary=phase_a_summary,
    )


def _call_phase_b_once(
    client,
    items: list[RequirementItem],
    existing_points: list[TestPoint] | list[dict],
) -> tuple[list[dict], list[str], str]:
    """单次 Phase B LLM 调用；返回 (raw_points, issues, raw_response)。"""
    user_prompt = _build_phase_b_user_prompt(items, existing_points)
    try:
        raw = client.chat(TEST_POINT_COMPLETER_PROMPT, user_prompt)
    except Exception as e:
        logger.exception("Phase B LLM 调用失败")
        return [], [f"Phase B LLM 调用失败: {e}"], ""

    payload = extract_json(raw)
    if payload is None:
        return [], ["Phase B 无法解析 JSON"], raw

    raw_points = payload.get("test_points") if isinstance(payload, dict) else payload
    if not isinstance(raw_points, list):
        return [], ["Phase B 响应缺少 test_points 数组"], raw

    cleaned = [p for p in raw_points if isinstance(p, dict)]
    issues = []
    dropped = len(raw_points) - len(cleaned)
    if dropped:
        issues.append(f"Phase B 丢弃 {dropped} 个非对象元素")
    return cleaned, issues, raw


def complete_cross_item(
    client,
    items: list[RequirementItem],
    existing_points: list[TestPoint] | list[dict],
    *,
    batch_threshold: int = PHASE_B_BATCH_THRESHOLD,
) -> GeneratorResult:
    """Phase B：全量 items + Phase A 摘要 → LLM 补跨项 TestPoint 候选。

    - items 数量 ≤ batch_threshold → 单次调用。
    - items 数量 > batch_threshold → 按 module 分组分批，每批独立调用，代码侧合并结果。
      分批时 existing_points 全量传入（每批都能看到 Phase A 全貌，避免重复补漏）。
    """
    if not items:
        return GeneratorResult(issues=["Phase B 无 items 可补漏"], calls=0)

    if len(items) <= batch_threshold:
        raw_points, issues, raw = _call_phase_b_once(client, items, existing_points)
        return GeneratorResult(raw_points=raw_points, issues=issues, raw_response=raw, calls=1)

    # 分批模式
    groups = _group_items_by_module(items)
    all_points: list[dict] = []
    all_issues: list[str] = []
    raws: list[str] = []
    calls = 0
    for module, group_items in groups.items():
        raw_points, issues, raw = _call_phase_b_once(client, group_items, existing_points)
        all_points.extend(raw_points)
        all_issues.extend(f"[{module}] {msg}" for msg in issues)
        if raw:
            raws.append(raw)
        calls += 1
    logger.info("Phase B 分批完成: modules=%d calls=%d points=%d", len(groups), calls, len(all_points))
    return GeneratorResult(
        raw_points=all_points,
        issues=all_issues,
        raw_response="\n---BATCH---\n".join(raws),
        calls=calls,
    )
