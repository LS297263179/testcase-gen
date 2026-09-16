"""V2 Step 6 变更影响分析 - diff 两个 RequirementVersion → 定位受影响测试资产 → 只读报告。

对应 PROGRESS.md §9 蓝图 `Traceability + 变更影响分析`。核心流程（用户设计）：

    RequirementVersion V_old + V_new（同一 doc_id）
      ↓ ItemMatcher（identity fingerprint → content_hash → 相似度兜底）
      ├── UNCHANGED  ├── MODIFIED  ├── ADDED  ├── DELETED
      ↓ ImpactResolver（trace_forward_from_item 追溯 old_item 的 TP/TC + affected_reason + impact_level）
      ↓ ChangeImpactReport（只读返回值）

★ Step 6 铁律（用户冻结）：
  - **纯只读**：只调 repo.get_*/list_*，绝不 save/改状态/重生成测试资产。
  - 职责边界：Step 6 只回答"发生了什么变化、哪些资产受影响"；Review 是 Step 7、人工确认是 Step 9。
  - identity fingerprint 不含 doc_id（跨版本匹配键）；content_hash 含 fields/rules/permissions/acceptance，
    解决"statement 未改但 FieldSpec 18~60→18~65"的隐性变更难点。
"""

from __future__ import annotations

import difflib
import json
import logging
from dataclasses import dataclass, field

from core.schemas import (
    AffectedReason,
    ChangeType,
    ImpactLevel,
    RequirementItem,
    RequirementVersion,
)
from core.v2 import repository as repo
from core.v2.fingerprint import compute_item_content_hash, compute_item_identity_fingerprint, normalize_text
from core.v2.traceability import trace_forward_from_item

logger = logging.getLogger("v2.change_impact")

# 相似度兜底阈值：identity 漂移（statement 微调）时，ratio ≥ 阈值判为 MODIFIED（可配）
DEFAULT_SIMILARITY_THRESHOLD = 0.6


# ============================================================
# 工具：canonical 序列化 + item 双 hash 兜底 + 相似度
# ============================================================


def _canon(items: object) -> str:
    """列表（Pydantic 模型或普通值）→ 确定性字符串（用于分量比对，与 fingerprint._canonical_list 同构）。"""
    dumped = [it.model_dump(mode="json") if hasattr(it, "model_dump") else it for it in (items or [])]
    return json.dumps(dumped, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _en(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _item_fingerprint(item: RequirementItem) -> str:
    """identity fingerprint（缺失则兜底计算，保证 match 可用）。"""
    return item.fingerprint or compute_item_identity_fingerprint(
        module=item.module, type=_en(item.type), statement=item.statement
    )


def _item_content_hash(item: RequirementItem) -> str:
    """content_hash（缺失则兜底计算）。"""
    return item.content_hash or compute_item_content_hash(
        statement=item.statement,
        fields=item.fields,
        rules=item.rules,
        permissions=item.permissions,
        acceptance_criteria=item.acceptance_criteria,
    )


def _similarity(a: str, b: str) -> float:
    """两条 statement 的归一化相似度 [0,1]（difflib.SequenceMatcher.ratio）。"""
    return difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


# ============================================================
# 数据结构
# ============================================================


@dataclass
class MatchResult:
    """ItemMatcher 的单条匹配结果（内部中间产物）。"""

    change_type: ChangeType
    old_item: RequirementItem | None = None
    new_item: RequirementItem | None = None
    similarity: float | None = None  # identity 漂移时的 statement 相似度（仅相似度兜底匹配的 MODIFIED 有值）


@dataclass
class ItemChange:
    """一条需求变更 + 其影响（ChangeImpactReport 的元素，对应用户的影响清单结构）。"""

    change_type: ChangeType
    old_item_id: str | None = None
    new_item_id: str | None = None
    old_statement: str = ""
    new_statement: str = ""
    similarity: float | None = None
    affected_reasons: list[AffectedReason] = field(default_factory=list)
    impact_level: ImpactLevel = ImpactLevel.LOW
    affected_test_points: list[str] = field(default_factory=list)  # 追溯 old_item 关联的 TP（ADDED 为空）
    affected_test_cases: list[str] = field(default_factory=list)  # 追溯 TP 关联的 TC
    recommended_actions: dict = field(default_factory=dict)


@dataclass
class ChangeImpactReport:
    """变更影响报告（只读返回值，不持久化、不改任何实体状态）。"""

    doc_id: str = ""
    version_old_id: str = ""
    version_new_id: str = ""
    changes: list[ItemChange] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


# ============================================================
# ItemMatcher：identity → content → 相似度兜底
# ============================================================


class ItemMatcher:
    """跨版本匹配 RequirementItem，产出 UNCHANGED / MODIFIED / ADDED / DELETED。

    匹配逻辑（用户冻结）：
      1. identity fingerprint 相同 → 比 content_hash：同=UNCHANGED，异=MODIFIED
      2. identity 不同的剩余项 → statement 相似度 ≥ 阈值的最优配对 = MODIFIED（identity 漂移）
      3. 仍剩余：old=DELETED，new=ADDED
    """

    def __init__(self, similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> None:
        self.similarity_threshold = similarity_threshold

    def match(self, old_items: list[RequirementItem], new_items: list[RequirementItem]) -> list[MatchResult]:
        results: list[MatchResult] = []

        # 1. 按 identity fingerprint 建索引（同版本内 fingerprint 理论唯一，重复取首个）
        old_by_fp: dict[str, RequirementItem] = {}
        for it in old_items:
            old_by_fp.setdefault(_item_fingerprint(it), it)
        new_by_fp: dict[str, RequirementItem] = {}
        for it in new_items:
            new_by_fp.setdefault(_item_fingerprint(it), it)

        # identity 交集：比 content_hash
        old_remaining: list[RequirementItem] = []
        matched_new_ids: set[str] = set()
        for fp, old_it in old_by_fp.items():
            new_it = new_by_fp.get(fp)
            if new_it is not None:
                if _item_content_hash(old_it) == _item_content_hash(new_it):
                    results.append(MatchResult(ChangeType.UNCHANGED, old_it, new_it))
                else:
                    results.append(MatchResult(ChangeType.MODIFIED, old_it, new_it))
                matched_new_ids.add(new_it.id)
            else:
                old_remaining.append(old_it)
        new_remaining = [
            it for it in new_items if it.id not in matched_new_ids and _item_fingerprint(it) not in old_by_fp
        ]

        # 2. 相似度兜底：old_remaining × new_remaining 最优配对
        pairs: list[tuple[float, RequirementItem, RequirementItem]] = []
        for oi in old_remaining:
            for ni in new_remaining:
                ratio = _similarity(oi.statement, ni.statement)
                if ratio >= self.similarity_threshold:
                    pairs.append((ratio, oi, ni))
        pairs.sort(key=lambda x: x[0], reverse=True)  # 贪心取最优
        used_old: set[str] = set()
        used_new: set[str] = set()
        for ratio, oi, ni in pairs:
            if oi.id in used_old or ni.id in used_new:
                continue
            used_old.add(oi.id)
            used_new.add(ni.id)
            results.append(MatchResult(ChangeType.MODIFIED, oi, ni, similarity=ratio))

        # 3. 剩余：DELETED / ADDED
        for oi in old_remaining:
            if oi.id not in used_old:
                results.append(MatchResult(ChangeType.DELETED, oi, None))
        for ni in new_remaining:
            if ni.id not in used_new:
                results.append(MatchResult(ChangeType.ADDED, None, ni))

        return results


# ============================================================
# ImpactResolver：追溯受影响资产 + affected_reason + impact_level
# ============================================================


def _diff_reasons(old_it: RequirementItem | None, new_it: RequirementItem | None) -> list[AffectedReason]:
    """比对 old/new item 的各分量，定位变化原因（可解释性）。"""
    if old_it is None or new_it is None:
        return []
    reasons: list[AffectedReason] = []
    if _canon(old_it.fields) != _canon(new_it.fields):
        reasons.append(AffectedReason.FIELD_CONSTRAINT_CHANGED)
    if _canon(old_it.rules) != _canon(new_it.rules):
        reasons.append(AffectedReason.BUSINESS_RULE_CHANGED)
    if _canon(old_it.permissions) != _canon(new_it.permissions):
        reasons.append(AffectedReason.PERMISSION_CHANGED)
    if _canon(old_it.acceptance_criteria) != _canon(new_it.acceptance_criteria):
        reasons.append(AffectedReason.ACCEPTANCE_CHANGED)
    if not reasons:
        # content_hash 不同但四个结构化分量都相同 → 仅 statement 措辞变化
        reasons.append(AffectedReason.REQUIREMENT_MODIFIED)
    return reasons


def _impact_level(change_type: ChangeType, reasons: list[AffectedReason]) -> ImpactLevel:
    """影响等级（首批代码规则；更细语义判断留给 Step 7 AI）。"""
    if change_type in (ChangeType.ADDED, ChangeType.DELETED):
        return ImpactLevel.HIGH
    high = {
        AffectedReason.FIELD_CONSTRAINT_CHANGED,
        AffectedReason.PERMISSION_CHANGED,
        AffectedReason.BUSINESS_RULE_CHANGED,
    }
    if any(r in high for r in reasons):
        return ImpactLevel.HIGH
    if AffectedReason.ACCEPTANCE_CHANGED in reasons:
        return ImpactLevel.MEDIUM
    return ImpactLevel.LOW  # 仅措辞


class ImpactResolver:
    """把 MatchResult 解析为 ItemChange（追溯受影响 TP/TC + 原因 + 等级 + 建议动作）。

    ★ 只读：仅用 trace_forward_from_item（内部全是 repo.get_*/list_*），不修改任何实体。
    """

    def resolve(self, match: MatchResult) -> ItemChange:
        ct = match.change_type

        if ct == ChangeType.UNCHANGED:
            return ItemChange(
                change_type=ct,
                old_item_id=match.old_item.id if match.old_item else None,
                new_item_id=match.new_item.id if match.new_item else None,
                old_statement=match.old_item.statement if match.old_item else "",
                new_statement=match.new_item.statement if match.new_item else "",
                affected_reasons=[],
                impact_level=ImpactLevel.LOW,
                recommended_actions={},
            )

        if ct == ChangeType.ADDED:
            # 新增需求：尚无既有测试资产，建议新增覆盖（仅建议，Step 6 不触发 Step 3/4/5）
            return ItemChange(
                change_type=ct,
                new_item_id=match.new_item.id if match.new_item else None,
                new_statement=match.new_item.statement if match.new_item else "",
                affected_reasons=[AffectedReason.REQUIREMENT_ADDED],
                impact_level=ImpactLevel.HIGH,
                affected_test_points=[],
                affected_test_cases=[],
                recommended_actions={"test_points": "generate_new", "test_cases": "generate_new"},
            )

        # MODIFIED / DELETED：追溯 old_item 关联的 TP/TC（基于旧版本的资产受影响，需复审）
        old_it = match.old_item
        trace = trace_forward_from_item(old_it.id) if old_it else None
        affected_tp = trace.tp_ids if trace else []
        affected_tc = trace.tc_ids if trace else []

        if ct == ChangeType.DELETED:
            reasons = [AffectedReason.REQUIREMENT_DELETED]
            actions = {"test_points": "review_or_archive", "test_cases": "review_or_archive"}
        else:  # MODIFIED
            reasons = _diff_reasons(match.old_item, match.new_item)
            actions = {"test_points": "review", "test_cases": "review"}

        return ItemChange(
            change_type=ct,
            old_item_id=old_it.id if old_it else None,
            new_item_id=match.new_item.id if match.new_item else None,
            old_statement=old_it.statement if old_it else "",
            new_statement=match.new_item.statement if match.new_item else "",
            similarity=match.similarity,
            affected_reasons=reasons,
            impact_level=_impact_level(ct, reasons),
            affected_test_points=affected_tp,
            affected_test_cases=affected_tc,
            recommended_actions=actions,
        )


# ============================================================
# 顶层入口
# ============================================================


def analyze_change_impact(
    version_old_id: str,
    version_new_id: str,
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> ChangeImpactReport:
    """对比同一 Doc 的两个 RequirementVersion，产出只读的变更影响报告。

    流程：
      1. 载入两个 version；校验 doc_id 相同（否则不可比，返回带 issue 的空报告）
      2. 载入各自 items（repo.list_items）
      3. ItemMatcher.match → UNCHANGED/MODIFIED/ADDED/DELETED
      4. ImpactResolver.resolve 每条 → 追溯受影响 TP/TC + affected_reason + impact_level
      5. 汇总 summary；返回 ChangeImpactReport（不持久化、不改状态）

    ★ 全程只读：不调用任何 repo.save_*，不修改 test_points/test_cases/statuses。
    """
    v_old: RequirementVersion | None = repo.get_version(version_old_id)
    v_new: RequirementVersion | None = repo.get_version(version_new_id)
    if v_old is None or v_new is None:
        missing = version_old_id if v_old is None else version_new_id
        return ChangeImpactReport(
            version_old_id=version_old_id,
            version_new_id=version_new_id,
            issues=[f"RequirementVersion 不存在: {missing}"],
        )
    if v_old.doc_id != v_new.doc_id:
        return ChangeImpactReport(
            doc_id=v_old.doc_id,
            version_old_id=version_old_id,
            version_new_id=version_new_id,
            issues=[f"两个 version 不属于同一 Doc（{v_old.doc_id} != {v_new.doc_id}），不可比"],
        )

    old_items = repo.list_items(version_old_id)
    new_items = repo.list_items(version_new_id)

    matcher = ItemMatcher(similarity_threshold=similarity_threshold)
    matches = matcher.match(old_items, new_items)

    resolver = ImpactResolver()
    changes = [resolver.resolve(m) for m in matches]

    # 排序：变化的排前面（MODIFIED/ADDED/DELETED），UNCHANGED 靠后；再按 impact_level
    order = {ChangeType.MODIFIED: 0, ChangeType.DELETED: 1, ChangeType.ADDED: 2, ChangeType.UNCHANGED: 3}
    level_order = {ImpactLevel.HIGH: 0, ImpactLevel.MEDIUM: 1, ImpactLevel.LOW: 2}
    changes.sort(key=lambda c: (order.get(c.change_type, 9), level_order.get(c.impact_level, 9)))

    affected_tp = {tp for c in changes for tp in c.affected_test_points}
    affected_tc = {tc for c in changes for tc in c.affected_test_cases}
    summary = {
        "unchanged": sum(1 for c in changes if c.change_type == ChangeType.UNCHANGED),
        "modified": sum(1 for c in changes if c.change_type == ChangeType.MODIFIED),
        "added": sum(1 for c in changes if c.change_type == ChangeType.ADDED),
        "deleted": sum(1 for c in changes if c.change_type == ChangeType.DELETED),
        "affected_test_point_count": len(affected_tp),
        "affected_test_case_count": len(affected_tc),
    }

    logger.info(
        "变更影响分析: doc=%s v_old=%s v_new=%s → unchanged=%d modified=%d added=%d deleted=%d 受影响 TP=%d TC=%d",
        v_old.doc_id,
        version_old_id,
        version_new_id,
        summary["unchanged"],
        summary["modified"],
        summary["added"],
        summary["deleted"],
        summary["affected_test_point_count"],
        summary["affected_test_case_count"],
    )

    return ChangeImpactReport(
        doc_id=v_old.doc_id,
        version_old_id=version_old_id,
        version_new_id=version_new_id,
        changes=changes,
        summary=summary,
    )
