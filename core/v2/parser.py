"""V2 需求解析器 - LLM 抽取 → 结构化 IR。

核心原则落地：LLM 只负责"理解与抽取"，代码负责
  - 鲁棒 JSON 提取（```json 块 / 裸 JSON / json5 / 括号配对 / 控制字符清理）
  - 白名单过滤（丢弃 LLM 多给的噪声键，模型保持 extra=forbid 严格）
  - 类型/优先级/置信度的代码侧修复与规范化
  - 超长需求自动分段（按段落切分、逐段抽取、seq 全局连续）
  - ID 生成、seq 编号、provenance 标注
见 docs/v2/step1-data-model.md §0/§5。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from core.schemas import (
    BusinessRule,
    FieldSpec,
    PermissionRule,
    Priority,
    Provenance,
    RequirementItem,
    RequirementItemType,
    SourceRef,
)
from core.v2.prompts import IR_EXTRACTION_PROMPT, SEGMENT_HINT

logger = logging.getLogger("v2.parser")

_VALID_TYPES = {e.value for e in RequirementItemType}
_VALID_PRIORITIES = {p.value for p in Priority}

# 超长自动分段阈值（字符数）；超过则按段落切分逐段抽取再合并
SEGMENT_MAX_CHARS = 6000


@dataclass
class ParseResult:
    """LLM 抽取 + 代码校验后的结果"""

    items: list[RequirementItem] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)  # 被丢弃/修复项的原因（代码记录）
    raw_response: str = ""
    segments: int = 1  # 实际分段数（1 = 未分段）


# ============================================================
# 鲁棒 JSON 提取
# ============================================================


def _try_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _try_json5(text: str) -> object | None:
    try:
        import json5

        return json5.loads(text)
    except Exception:
        return None


def _strip_control_chars(text: str) -> str:
    """去除 JSON 字符串中未转义的控制字符（LLM 常见输出问题）"""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def extract_json(raw: str) -> object | None:
    """从 LLM 响应中鲁棒提取 JSON 对象/数组，失败返回 None"""
    if not raw or not raw.strip():
        return None
    text = _strip_control_chars(raw.strip())

    # 1. ```json ... ``` 或 ``` ... ``` 代码块：**遍历全部**围栏块逐个尝试解析。
    #    模型经常回显需求正文里的代码块（状态机流转、示例等），若只取第一个围栏块
    #    并整体替换 text，真正的 JSON 负载会被丢弃，且后续兜底也在错误文本上进行。
    for m in re.finditer(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL):
        for loader in (_try_json, _try_json5):
            obj = loader(m.group(1).strip())
            if obj is not None:
                return obj

    # 2. 直接解析（无围栏，或所有围栏块都解析失败时）
    for loader in (_try_json, _try_json5):
        obj = loader(text)
        if obj is not None:
            return obj

    # 3. 大括号/中括号配对截取（前后夹杂解释文字时）
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            snippet = text[start : end + 1]
            for loader in (_try_json, _try_json5):
                obj = loader(snippet)
                if obj is not None:
                    return obj
    return None


# ============================================================
# 超长自动分段
# ============================================================


def _hard_split(text: str, max_chars: int) -> list[str]:
    """按行硬切超长段落；单行仍超长则按字符切"""
    segments: list[str] = []
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 <= max_chars:
            buf = f"{buf}\n{line}" if buf else line
        else:
            if buf:
                segments.append(buf)
            buf = line
            while len(buf) > max_chars:
                segments.append(buf[:max_chars])
                buf = buf[max_chars:]
    if buf:
        segments.append(buf)
    return segments


def split_segments(text: str, max_chars: int = SEGMENT_MAX_CHARS) -> list[str]:
    """把长需求按段落(空行)切分为不超过 max_chars 的段；不超阈值则原样单段返回。"""
    if not text or len(text) <= max_chars:
        return [text]
    segments: list[str] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        if len(para) > max_chars:  # 单段就超长 → 硬切
            if buf:
                segments.append(buf)
                buf = ""
            segments.extend(_hard_split(para, max_chars))
            continue
        if len(buf) + len(para) + 2 <= max_chars:
            buf = f"{buf}\n\n{para}" if buf else para
        else:
            if buf:
                segments.append(buf)
            buf = para
    if buf:
        segments.append(buf)
    return segments


# ============================================================
# 代码侧规范化 + Pydantic 校验
# ============================================================


def _filter_known(model_cls: type[BaseModel], data: object) -> dict:
    """只保留模型已定义字段，丢弃 LLM 多给的噪声键（模型本身仍 extra=forbid）"""
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in model_cls.model_fields}


def _infer_type(d: dict) -> str:
    """type 缺失/非法时按携带内容推断（代码修复，不靠 LLM 重来）"""
    if d.get("permissions"):
        return RequirementItemType.PERMISSION.value
    if d.get("fields"):
        return RequirementItemType.DATA_FIELD.value
    if d.get("rules"):
        return RequirementItemType.BUSINESS_RULE.value
    return RequirementItemType.FUNCTION.value


def coerce_item(raw_item: object, version_id: str, seq: int) -> tuple[RequirementItem | None, str | None]:
    """把单个 LLM item 规范化并校验为 RequirementItem；失败返回 (None, 原因)"""
    if not isinstance(raw_item, dict):
        return None, "非对象"

    d = _filter_known(RequirementItem, raw_item)

    statement = str(d.get("statement") or "").strip()
    if not statement:
        return None, "缺少 statement"
    d["statement"] = statement

    if d.get("type") not in _VALID_TYPES:
        d["type"] = _infer_type(d)
    if not str(d.get("module") or "").strip():
        d["module"] = "未分类"
    if d.get("priority_hint") not in _VALID_PRIORITIES:
        d.pop("priority_hint", None)
    if "confidence" in d:
        try:
            d["confidence"] = min(1.0, max(0.0, float(d["confidence"])))
        except (TypeError, ValueError):
            d["confidence"] = 0.5

    d["fields"] = [_filter_known(FieldSpec, f) for f in d.get("fields", []) if isinstance(f, dict)]
    d["rules"] = [_filter_known(BusinessRule, r) for r in d.get("rules", []) if isinstance(r, dict)]
    d["permissions"] = [_filter_known(PermissionRule, p) for p in d.get("permissions", []) if isinstance(p, dict)]

    # source_ref 归一化：LLM 可能给字符串(原文摘录)或对象 → SourceRef（无法定位则 None）
    sr = d.get("source_ref")
    if isinstance(sr, str):
        d["source_ref"] = {"locator": "quote", "value": sr.strip()} if sr.strip() else None
    elif isinstance(sr, dict):
        filtered = _filter_known(SourceRef, sr)
        d["source_ref"] = filtered if filtered.get("value") else None
        if d["source_ref"] and not d["source_ref"].get("locator"):
            d["source_ref"]["locator"] = "quote"
    else:
        d.pop("source_ref", None)

    d["version_id"] = version_id
    d["seq"] = seq
    d["provenance"] = Provenance.LLM.value

    try:
        return RequirementItem.model_validate(d), None
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        return None, f"Schema 校验失败: {first.get('loc', '')} {first.get('msg', '')}".strip()


# ============================================================
# 解析入口
# ============================================================


def _parse_segment(
    client, text: str, version_id: str, images: list[dict] | None, start_seq: int, hint: str = ""
) -> tuple[list[RequirementItem], list[str], str]:
    """抽取单段：LLM 调用 → JSON 提取 → 逐项校验。返回 (items, issues, raw)"""
    user_prompt = f"## 用户需求\n{text}\n\n请按要求输出结构化 IR JSON。"
    if hint:
        user_prompt = f"{hint}\n\n{user_prompt}"
    try:
        raw = client.chat(IR_EXTRACTION_PROMPT, user_prompt, images=images)
    except Exception as e:
        logger.exception("需求解析 LLM 调用失败")
        return [], [f"LLM 调用失败: {e}"], ""

    payload = extract_json(raw)
    if payload is None:
        return [], ["无法从 LLM 响应解析出 JSON"], raw
    raw_items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return [], ["LLM 响应缺少 items 数组"], raw

    items: list[RequirementItem] = []
    issues: list[str] = []
    for raw_item in raw_items:
        item, err = coerce_item(raw_item, version_id, start_seq + len(items))
        if item is None:
            issues.append(f"item 丢弃: {err}")
            continue
        items.append(item)
    return items, issues, raw


def parse_requirement(client, raw_text: str, version_id: str, images: list[dict] | None = None) -> ParseResult:
    """调用 LLM 抽取 IR（超长自动分段），返回校验后的 RequirementItem 列表 + 问题记录。

    client 需实现 .chat(system_prompt, user_prompt, images=None) -> str（V1 LLMClient 接口）。
    图片只随首段发送（全局视觉上下文），后续段为纯文本续抽。
    """
    segments = split_segments(raw_text)
    all_items: list[RequirementItem] = []
    all_issues: list[str] = []
    raws: list[str] = []
    for idx, seg in enumerate(segments, 1):
        hint = SEGMENT_HINT.format(idx=idx, total=len(segments)) if len(segments) > 1 else ""
        seg_images = images if idx == 1 else None
        items, issues, raw = _parse_segment(client, seg, version_id, seg_images, len(all_items) + 1, hint)
        all_items.extend(items)
        all_issues.extend(issues)
        if raw:
            raws.append(raw)

    if not all_items:
        all_issues.append("未产出任何有效需求项")
    return ParseResult(
        items=all_items, issues=all_issues, raw_response="\n---SEGMENT---\n".join(raws), segments=len(segments)
    )
