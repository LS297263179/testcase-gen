"""V2 测试点校验器 - 代码侧规范化 / Step 3 硬性约束覆写 / 去重 / 覆盖率（Step 3）。

职责（对应 PROGRESS.md §1 "代码负责确定性关注点"）：
  - 白名单过滤 + Pydantic 严格校验
  - **Step 3 硬性覆写**：provenance=LLM / technique=None / obligation_id=None / status=DRAFT /
    generation_scope 由 phase 参数强制指定
  - **item_ids 归一**：
      Phase A（scope=item）      → 强制覆写为 [当前 item.id]，忽略 LLM 给的
      Phase B（scope=cross_item）→ 过滤非法 id；若过滤后 len < 2 → 丢弃该 TestPoint
  - **module 强约束**：TestPoint.module 必须来自关联 RequirementItem 的 module 集合；
    Phase A 直接覆写为 item.module；Phase B 校验 LLM 给的 module ∈ items.module 集合，否则取第一个 item 的 module
  - **subcategory** 允许 LLM 语义归纳（不做集合约束，仅做非空校验）
  - 枚举兜底：dimension 非法→FUNCTIONAL；priority 非法→P1
  - fingerprint 计算（业务幂等身份）
  - 去重合并：按 fingerprint 严格去重；跨 phase 不做语义合并（generation_scope 不同即业务身份不同）
  - 覆盖率报告：软指标，列出未被任何 TestPoint 引用的 RequirementItem
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import ValidationError

from core.schemas import (
    EntityStatus,
    GenerationScope,
    Priority,
    Provenance,
    RequirementItem,
    TestDimension,
    TestPoint,
)
from core.v2.fingerprint import compute_testpoint_fingerprint, normalize_text

logger = logging.getLogger("v2.tp_validator")

_VALID_DIMENSIONS = {e.value for e in TestDimension}
_VALID_PRIORITIES = {p.value for p in Priority}

# TestPoint 允许的输入字段白名单（LLM 多给的噪声键在此丢弃，Pydantic 保持 extra=forbid）
_ALLOWED_FIELDS = {"module", "subcategory", "title", "description", "dimension", "priority", "item_ids"}


@dataclass
class CoverageReport:
    """测试点覆盖率软报告（Step 3 软指标，Step 4 策略引擎负责硬兜底）。"""

    total_items: int
    covered_item_ids: list[str] = field(default_factory=list)
    uncovered_item_ids: list[str] = field(default_factory=list)
    coverage_ratio: float = 0.0


@dataclass
class ValidationResult:
    """校验产物：合法 TestPoint 列表 + 问题记录。"""

    points: list[TestPoint] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


# ============================================================
# 单条 raw dict → TestPoint（含 Step 3 硬性覆写）
# ============================================================


def _coerce_one(
    raw: dict,
    *,
    phase: GenerationScope,
    items_index: dict[str, RequirementItem],
    forced_item_id: str | None = None,
) -> tuple[TestPoint | None, str | None]:
    """把单个 LLM 候选 dict 规范化为 TestPoint。失败返回 (None, 原因)。

    - phase=ITEM：forced_item_id 必须给（当前正在处理的 item.id），代码强制覆写 item_ids 与 module。
    - phase=CROSS_ITEM：从 raw.item_ids 过滤非法 id；若过滤后 len < 2 → 丢弃。
    """
    if not isinstance(raw, dict):
        return None, "非对象"

    # 1. 白名单过滤
    d = {k: v for k, v in raw.items() if k in _ALLOWED_FIELDS}

    # 2. 必填字段校验（title/description 非空）
    title = str(d.get("title") or "").strip()
    description = str(d.get("description") or "").strip()
    if not title:
        return None, "缺少 title"
    if not description:
        return None, "缺少 description"
    d["title"] = title
    d["description"] = description

    # 3. subcategory 非空兜底（允许 LLM 语义归纳，缺失时给默认值）
    subcategory = str(d.get("subcategory") or "").strip()
    if not subcategory:
        subcategory = "未分类"
        d["subcategory"] = subcategory

    # 4. item_ids 归一（Phase A/B 分别处理）
    if phase == GenerationScope.ITEM:
        if not forced_item_id:
            return None, "Phase A 缺少 forced_item_id"
        item_ids = [forced_item_id]
        # module 强制覆写为当前 item 的 module（不接受 LLM 给的）
        item = items_index.get(forced_item_id)
        if item is None:
            return None, f"Phase A forced_item_id 不在 items_index: {forced_item_id}"
        d["module"] = item.module or "未分类"
    else:  # CROSS_ITEM
        raw_ids = d.get("item_ids") or []
        if not isinstance(raw_ids, list):
            return None, "Phase B item_ids 非数组"
        # 过滤非法 id（不在 items_index 里的）
        item_ids = [str(i) for i in raw_ids if isinstance(i, str) and i in items_index]
        dropped = len(raw_ids) - len(item_ids)
        if dropped:
            logger.debug("Phase B 丢弃 %d 个非法 item_ids", dropped)
        # 去重（保持顺序）
        seen = set()
        deduped = []
        for i in item_ids:
            if i not in seen:
                seen.add(i)
                deduped.append(i)
        item_ids = deduped
        if len(item_ids) < 2:
            return None, f"Phase B item_ids 过滤后 <2（实际 {len(item_ids)}），丢弃跨项测试点"
        # module 校验：必须 ∈ items.module 集合；否则取第一个 item 的 module 兜底
        allowed_modules = {items_index[i].module or "未分类" for i in item_ids}
        llm_module = str(d.get("module") or "").strip()
        if llm_module in allowed_modules:
            d["module"] = llm_module
        else:
            d["module"] = items_index[item_ids[0]].module or "未分类"

    d["item_ids"] = item_ids

    # 5. 枚举兜底
    dim_raw = str(d.get("dimension") or "").strip().lower()
    if dim_raw not in _VALID_DIMENSIONS:
        d["dimension"] = TestDimension.FUNCTIONAL.value
    pri_raw = str(d.get("priority") or "").strip().upper()
    if pri_raw not in _VALID_PRIORITIES:
        d["priority"] = Priority.P1.value

    # 6. Step 3 硬性覆写（Validator 强制，不接受 LLM 给的值）
    d["provenance"] = Provenance.LLM.value
    d["technique"] = None
    d["obligation_id"] = None
    d["status"] = EntityStatus.DRAFT.value
    d["generation_scope"] = phase.value

    # 7. Pydantic 严格校验
    try:
        tp = TestPoint.model_validate(d)
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        return None, f"Schema 校验失败: {first.get('loc', '')} {first.get('msg', '')}".strip()

    # 8. 计算 fingerprint（业务幂等身份）
    tp.fingerprint = compute_testpoint_fingerprint(
        version_id=tp.version_id,
        generation_scope=tp.generation_scope.value,
        item_ids=list(tp.item_ids),
        module=tp.module,
        subcategory=tp.subcategory,
        title=tp.title,
    )
    return tp, None


# ============================================================
# 批量校验入口
# ============================================================


def validate_phase_a(
    raw_points: list[dict],
    *,
    item: RequirementItem,
    items_index: dict[str, RequirementItem] | None = None,
) -> ValidationResult:
    """Phase A 校验：单 item 的候选 → TestPoint 列表。

    raw_points 里每个 dict 都被强制关联到 `item.id`（LLM 给的 item_ids 一律忽略）。
    """
    idx = items_index if items_index is not None else {item.id: item}
    result = ValidationResult()
    for raw in raw_points:
        tp, err = _coerce_one(raw, phase=GenerationScope.ITEM, items_index=idx, forced_item_id=item.id)
        if tp is None:
            result.issues.append(f"Phase A 丢弃: {err}")
            continue
        tp.version_id = item.version_id  # 冗余字段，便于按需求版本查询
        tp.run_id = None  # orchestrator 后续统一注入
        # 重新计算 fingerprint（version_id 变了）
        tp.fingerprint = compute_testpoint_fingerprint(
            version_id=tp.version_id,
            generation_scope=tp.generation_scope.value,
            item_ids=list(tp.item_ids),
            module=tp.module,
            subcategory=tp.subcategory,
            title=tp.title,
        )
        result.points.append(tp)
    return result


def validate_phase_b(
    raw_points: list[dict],
    *,
    items: list[RequirementItem],
    version_id: str | None = None,
) -> ValidationResult:
    """Phase B 校验：跨项候选 → TestPoint 列表。

    - item_ids 必须在 items 里存在；过滤后 <2 的丢弃。
    - module 必须 ∈ items.module 集合；否则取第一个 item 的 module 兜底。
    """
    idx = {it.id: it for it in items}
    # version_id 兜底：从 items 里取第一个的 version_id
    vid = version_id or (items[0].version_id if items else None)
    result = ValidationResult()
    for raw in raw_points:
        tp, err = _coerce_one(raw, phase=GenerationScope.CROSS_ITEM, items_index=idx)
        if tp is None:
            result.issues.append(f"Phase B 丢弃: {err}")
            continue
        tp.version_id = vid
        tp.run_id = None  # orchestrator 后续统一注入
        # 重新计算 fingerprint（version_id 可能变了）
        tp.fingerprint = compute_testpoint_fingerprint(
            version_id=tp.version_id,
            generation_scope=tp.generation_scope.value,
            item_ids=list(tp.item_ids),
            module=tp.module,
            subcategory=tp.subcategory,
            title=tp.title,
        )
        result.points.append(tp)
    return result


# ============================================================
# 去重合并
# ============================================================


def dedupe_points(points: list[TestPoint]) -> tuple[list[TestPoint], list[str]]:
    """按 fingerprint 严格去重；冲突时保留首个、后续记入 issues。

    设计决策：**不做跨 phase 语义合并**。理由：
      - Phase A 的 generation_scope=item，item_ids 长度必为 1；
      - Phase B 的 generation_scope=cross_item，item_ids 长度必 ≥ 2；
      - fingerprint 包含 generation_scope + sorted(item_ids)，两 phase 天然不会撞 fingerprint；
      - 若强行合并（把 Phase B 的 item_ids 并到 Phase A），会破坏 Phase A 的"单点追溯"语义
        并使 generation_scope 与 item_ids 长度不一致，违反 Schema 约束。
      - 语义相似但 generation_scope 不同的 TestPoint 保留两者是合理的：单点强调"该 item 自身"，
        跨点强调"多个 item 的联动"。
    """
    seen: dict[str, TestPoint] = {}
    issues: list[str] = []
    for tp in points:
        if not tp.fingerprint:
            issues.append(f"缺少 fingerprint，跳过: {tp.title}")
            continue
        if tp.fingerprint in seen:
            issues.append(f"重复 fingerprint 丢弃: {tp.module}/{tp.subcategory}/{tp.title}")
            continue
        seen[tp.fingerprint] = tp
    return list(seen.values()), issues


# ============================================================
# 覆盖率报告
# ============================================================


def build_coverage_report(points: list[TestPoint], items: list[RequirementItem]) -> CoverageReport:
    """软指标：统计哪些 RequirementItem 已被至少一个 TestPoint 引用。

    未覆盖的 item 在 Step 4 由策略引擎硬兜底（例如 field_spec 有 min/max 但 Phase A 没生成
    boundary 测试点，Step 4 会派生 obligation → strategy TestPoint）。
    """
    covered: set[str] = set()
    for tp in points:
        covered.update(tp.item_ids)
    all_ids = [it.id for it in items]
    uncovered = [i for i in all_ids if i not in covered]
    ratio = (len(all_ids) - len(uncovered)) / len(all_ids) if all_ids else 0.0
    return CoverageReport(
        total_items=len(all_ids),
        covered_item_ids=[i for i in all_ids if i in covered],
        uncovered_item_ids=uncovered,
        coverage_ratio=ratio,
    )


# ============================================================
# 语义相似 warning（非阻塞，仅记录）
# ============================================================


def warn_semantic_duplicates(points: list[TestPoint]) -> list[str]:
    """按 (module, subcategory, normalize(title)) 检查语义相似，跨 phase 也不合并、只记录 warning。

    用于 orchestrator 返回给调用方作为软提示（Step 5 评审阶段可据此提示用户）。
    """
    seen: dict[tuple[str, str, str], list[str]] = {}
    for tp in points:
        key = (normalize_text(tp.module), normalize_text(tp.subcategory), normalize_text(tp.title))
        seen.setdefault(key, []).append(tp.generation_scope.value)
    warnings: list[str] = []
    for key, scopes in seen.items():
        if len(scopes) > 1:
            warnings.append(f"语义相似（{key[0]}/{key[1]}/{key[2]}）出现在多个 generation_scope: {scopes}")
    return warnings
