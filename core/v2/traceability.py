"""V2 Step 6 追溯链查询 - 正向（item→TP→TC→obligation）+ 反向（TC→TP→item→version→原文）。

复用 Step 1 落地的关联表（test_point_items / test_case_points）与 resolver.TargetResolver（多态目标存在性校验）。
★ 纯只读查询，不修改任何实体（Step 6 只读原则）。对应 PROGRESS.md §9 蓝图 Traceability。

追溯链（docs/v2/step1-data-model.md §4.2）：
  正向：RequirementItem → TestPoint → TestCase（+ strategy TP 回链的 CoverageObligation）
  反向：TestCase → TestPoint → RequirementItem → RequirementVersion → RequirementDoc → source_ref（原文定位）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import (
    CoverageObligation,
    RequirementDoc,
    RequirementItem,
    RequirementVersion,
    SourceRef,
    TargetType,
    TestCase,
    TestPoint,
)
from core.v2 import repository as repo
from core.v2.resolver import TargetResolver

logger = logging.getLogger("v2.traceability")


@dataclass
class ForwardTrace:
    """正向追溯产物：RequirementItem → TestPoint[] → TestCase[] → CoverageObligation[]。"""

    item_id: str
    item: RequirementItem | None = None
    test_points: list[TestPoint] = field(default_factory=list)
    test_cases: list[TestCase] = field(default_factory=list)
    obligations: list[CoverageObligation] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def tp_ids(self) -> list[str]:
        return [tp.id for tp in self.test_points]

    @property
    def tc_ids(self) -> list[str]:
        return [tc.id for tc in self.test_cases]


@dataclass
class BackwardTrace:
    """反向追溯产物：TestCase → TestPoint[] → RequirementItem[] → Version → Doc → source_ref。"""

    case_id: str
    test_case: TestCase | None = None
    test_points: list[TestPoint] = field(default_factory=list)
    requirement_items: list[RequirementItem] = field(default_factory=list)
    version: RequirementVersion | None = None
    doc: RequirementDoc | None = None
    source_refs: list[SourceRef] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def tp_ids(self) -> list[str]:
        return [tp.id for tp in self.test_points]

    @property
    def item_ids(self) -> list[str]:
        return [it.id for it in self.requirement_items]


# ============================================================
# 正向追溯：item → TP → TC → obligation
# ============================================================


def trace_forward_from_item(item_id: str) -> ForwardTrace:
    """给定 RequirementItem，正向追溯其关联的 TestPoint → TestCase → CoverageObligation。

    - TestPoint：经 test_point_items 反查（repo.list_test_points_by_item）
    - TestCase：经 test_case_points 反查每个 TP（去重，一个 TC 可能关联多个 TP）
    - Obligation：strategy TestPoint 的 obligation_id 回链（去重）
    """
    resolver = TargetResolver()
    if not resolver.exists(TargetType.REQUIREMENT_ITEM, item_id):
        return ForwardTrace(item_id=item_id, issues=[f"RequirementItem 不存在: {item_id}"])

    item = repo.get_item(item_id)
    test_points = repo.list_test_points_by_item(item_id)

    # TP → TC（去重：同一 TC 可能被多个 TP 关联）
    cases_by_id: dict[str, TestCase] = {}
    for tp in test_points:
        for tc in repo.list_test_cases_by_test_point(tp.id):
            cases_by_id[tc.id] = tc

    # strategy TP 的 obligation 回链（去重）
    obligations_by_id: dict[str, CoverageObligation] = {}
    for tp in test_points:
        if tp.obligation_id and tp.obligation_id not in obligations_by_id:
            ob = repo.get_obligation(tp.obligation_id)
            if ob is not None:
                obligations_by_id[ob.id] = ob

    logger.debug(
        "正向追溯 item=%s: TP=%d TC=%d obligation=%d",
        item_id,
        len(test_points),
        len(cases_by_id),
        len(obligations_by_id),
    )
    return ForwardTrace(
        item_id=item_id,
        item=item,
        test_points=test_points,
        test_cases=list(cases_by_id.values()),
        obligations=list(obligations_by_id.values()),
    )


# ============================================================
# 反向追溯：TC → TP → item → version → doc → 原文
# ============================================================


def trace_backward_from_case(case_id: str) -> BackwardTrace:
    """给定 TestCase，反向追溯其 TestPoint → RequirementItem → Version → Doc → source_ref（原文定位）。

    用途：从一条用例回溯"它基于哪个需求版本的哪些需求项、原文在哪"，
    是变更影响分析（受影响资产定位）与人工审查的依据链。
    """
    resolver = TargetResolver()
    if not resolver.exists(TargetType.TESTCASE, case_id):
        return BackwardTrace(case_id=case_id, issues=[f"TestCase 不存在: {case_id}"])

    tc = repo.get_test_case(case_id)
    tp_ids = list(tc.test_point_ids) if tc else []
    test_points = [tp for tp in (repo.get_test_point(tp_id) for tp_id in tp_ids) if tp is not None]

    # TP → item（去重）
    items_by_id: dict[str, RequirementItem] = {}
    for tp in test_points:
        for iid in tp.item_ids:
            if iid not in items_by_id:
                it = repo.get_item(iid)
                if it is not None:
                    items_by_id[iid] = it
    items = list(items_by_id.values())

    # item → version → doc（取首个 item 的 version；正常同一 case 的 items 属同一 version）
    version = None
    doc = None
    if items:
        version = repo.get_version(items[0].version_id)
        if version is not None:
            doc = repo.get_doc(version.doc_id)

    source_refs = [it.source_ref for it in items if it.source_ref is not None]

    logger.debug(
        "反向追溯 case=%s: TP=%d item=%d version=%s",
        case_id,
        len(test_points),
        len(items),
        version.id if version else None,
    )
    return BackwardTrace(
        case_id=case_id,
        test_case=tc,
        test_points=test_points,
        requirement_items=items,
        version=version,
        doc=doc,
        source_refs=source_refs,
    )
