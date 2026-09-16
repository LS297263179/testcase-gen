"""V2 Step 6 追溯链查询测试 - 正向（item→TP→TC→obligation）+ 反向（TC→TP→item→version→原文）。

全代码，不调 LLM。用直接构造实体的方式搭建链路（不依赖 Step 3/4/5 orchestrator，聚焦追溯本身）。
"""

from core.schemas import (
    CoverageObligation,
    DataType,
    FieldSpec,
    GenerationConfig,
    GenerationScope,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    Run,
    RunStatus,
    SourceRef,
    SourceType,
    TargetType,
    Technique,
    TestCase,
    TestCaseType,
    TestDimension,
    TestPoint,
    TestStep,
)
from core.v2.traceability import (
    BackwardTrace,
    ForwardTrace,
    trace_backward_from_case,
    trace_forward_from_item,
)

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _base(v2_db, *, source_ref=None):
    """建 Doc + Version + Run + Config，返回 (doc, ver, run)。"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="追溯测试", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求原文", provenance=Provenance.LLM)
    v2_db.save_version(ver)
    cfg = GenerationConfig(
        model_provider="openai",
        model_name="gpt-x",
        temperature=0.3,
        prompt_version="t",
        generator_version="t",
    )
    v2_db.save_generation_config(cfg)
    run = Run(
        user_id=USER_ID,
        doc_id=doc.id,
        requirement_version_id=ver.id,
        generation_config_id=cfg.id,
        status=RunStatus.DONE,
    )
    v2_db.save_run(run)
    doc.latest_version_id = ver.id
    v2_db.save_doc(doc)
    return doc, ver, run


def _item(
    v2_db, ver, *, seq=1, module="登录", statement="手机号 11 位", fields=None, source_ref=None
) -> RequirementItem:
    it = RequirementItem(
        version_id=ver.id,
        seq=seq,
        type=RequirementItemType.DATA_FIELD,
        module=module,
        statement=statement,
        fields=fields or [],
        source_ref=source_ref,
        confidence=0.9,
    )
    v2_db.save_item(it)
    return it


def _tp(
    v2_db,
    run,
    ver,
    item_ids,
    *,
    module="登录",
    title="TP",
    provenance=Provenance.LLM,
    technique=None,
    obligation_id=None,
    dimension=TestDimension.FUNCTIONAL,
    scope=GenerationScope.ITEM,
) -> TestPoint:
    tp = TestPoint(
        run_id=run.id,
        version_id=ver.id,
        item_ids=item_ids,
        module=module,
        subcategory="校验",
        title=title,
        description="描述",
        dimension=dimension,
        technique=technique,
        obligation_id=obligation_id,
        priority=Priority.P1,
        provenance=provenance,
        generation_scope=scope,
    )
    v2_db.save_test_point(tp)
    return tp


def _tc(v2_db, run, tp_ids, *, display_id="TC_001", module="登录") -> TestCase:
    tc = TestCase(
        run_id=run.id,
        display_id=display_id,
        test_point_ids=tp_ids,
        module=module,
        title="用例",
        precondition="",
        steps=[TestStep(seq=1, action="操作", expected="结果")],
        expected="预期",
        priority=Priority.P1,
        type=TestCaseType.FUNCTIONAL,
    )
    v2_db.save_test_case(tc)
    return tc


# ============================================================
# 正向追溯 item → TP → TC → obligation
# ============================================================


class TestForwardTrace:
    def test_item_to_tp_to_tc(self, v2_db):
        doc, ver, run = _base(v2_db)
        item = _item(v2_db, ver)
        tp = _tp(v2_db, run, ver, [item.id])
        tc = _tc(v2_db, run, [tp.id])
        trace = trace_forward_from_item(item.id)
        assert isinstance(trace, ForwardTrace)
        assert trace.item is not None and trace.item.id == item.id
        assert trace.tp_ids == [tp.id]
        assert trace.tc_ids == [tc.id]
        assert trace.issues == []

    def test_nonexistent_item(self, v2_db):
        _base(v2_db)
        trace = trace_forward_from_item("01ARZ3NDEKTSV4RRFFQ69G5ZZZ")
        assert trace.item is None
        assert any("不存在" in i for i in trace.issues)

    def test_multiple_tp_and_tc(self, v2_db):
        doc, ver, run = _base(v2_db)
        item = _item(v2_db, ver)
        tp1 = _tp(v2_db, run, ver, [item.id], title="TP1")
        tp2 = _tp(v2_db, run, ver, [item.id], title="TP2")
        _tc(v2_db, run, [tp1.id], display_id="TC_001")
        _tc(v2_db, run, [tp2.id], display_id="TC_002")
        trace = trace_forward_from_item(item.id)
        assert set(trace.tp_ids) == {tp1.id, tp2.id}
        assert len(trace.tc_ids) == 2

    def test_tc_dedup_when_shared_by_two_tp(self, v2_db):
        """一个 TC 关联同一 item 的两个 TP → 正向追溯 TC 去重"""
        doc, ver, run = _base(v2_db)
        item = _item(v2_db, ver)
        tp1 = _tp(v2_db, run, ver, [item.id], title="TP1")
        tp2 = _tp(v2_db, run, ver, [item.id], title="TP2")
        tc = _tc(v2_db, run, [tp1.id, tp2.id], display_id="TC_001")
        trace = trace_forward_from_item(item.id)
        assert trace.tc_ids == [tc.id]  # 去重后只 1 个

    def test_strategy_tp_obligation_traced(self, v2_db):
        """strategy TP 回链的 obligation 出现在正向追溯里"""
        doc, ver, run = _base(v2_db)
        item = _item(
            v2_db,
            ver,
            fields=[
                FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True)
            ],
        )
        ob = CoverageObligation(
            run_id=run.id,
            item_id=item.id,
            technique=Technique.BOUNDARY_VALUE,
            target="age",
            description="边界",
            params={"field": "age"},
        )
        v2_db.save_obligation(ob)
        _tp(
            v2_db,
            run,
            ver,
            [item.id],
            provenance=Provenance.STRATEGY,
            technique=Technique.BOUNDARY_VALUE,
            obligation_id=ob.id,
            dimension=TestDimension.BOUNDARY,
            scope=GenerationScope.STRATEGY,
        )
        trace = trace_forward_from_item(item.id)
        assert [o.id for o in trace.obligations] == [ob.id]

    def test_item_without_tp(self, v2_db):
        doc, ver, run = _base(v2_db)
        item = _item(v2_db, ver)
        trace = trace_forward_from_item(item.id)
        assert trace.item is not None
        assert trace.test_points == [] and trace.test_cases == []


# ============================================================
# 反向追溯 TC → TP → item → version → doc → 原文
# ============================================================


class TestBackwardTrace:
    def test_case_to_item_to_version_to_doc(self, v2_db):
        doc, ver, run = _base(v2_db)
        item = _item(v2_db, ver)
        tp = _tp(v2_db, run, ver, [item.id])
        tc = _tc(v2_db, run, [tp.id])
        trace = trace_backward_from_case(tc.id)
        assert isinstance(trace, BackwardTrace)
        assert trace.test_case.id == tc.id
        assert trace.tp_ids == [tp.id]
        assert trace.item_ids == [item.id]
        assert trace.version.id == ver.id
        assert trace.doc.id == doc.id

    def test_nonexistent_case(self, v2_db):
        _base(v2_db)
        trace = trace_backward_from_case("01ARZ3NDEKTSV4RRFFQ69G5ZZZ")
        assert trace.test_case is None
        assert any("不存在" in i for i in trace.issues)

    def test_source_ref_traced(self, v2_db):
        """item 带 source_ref → 反向追溯可取到原文定位"""
        doc, ver, run = _base(v2_db)
        sref = SourceRef(locator="para-3", value="手机号必须 11 位")
        item = _item(v2_db, ver, source_ref=sref)
        tp = _tp(v2_db, run, ver, [item.id])
        tc = _tc(v2_db, run, [tp.id])
        trace = trace_backward_from_case(tc.id)
        assert len(trace.source_refs) == 1
        assert trace.source_refs[0].locator == "para-3"

    def test_cross_item_tp_multiple_items(self, v2_db):
        """跨项 TP（2 items）→ 反向追溯得到 2 个 item"""
        doc, ver, run = _base(v2_db)
        it1 = _item(v2_db, ver, seq=1, module="订单", statement="下单")
        it2 = _item(v2_db, ver, seq=2, module="库存", statement="扣减库存")
        tp = _tp(
            v2_db,
            run,
            ver,
            [it1.id, it2.id],
            module="订单",
            scope=GenerationScope.CROSS_ITEM,
            dimension=TestDimension.LINKAGE,
        )
        tc = _tc(v2_db, run, [tp.id])
        trace = trace_backward_from_case(tc.id)
        assert set(trace.item_ids) == {it1.id, it2.id}

    def test_resolver_reuse_exists_check(self, v2_db):
        """输入校验复用 resolver.TargetResolver.exists（不存在的 id 走 issue 分支）"""
        from core.v2.resolver import TargetResolver

        _base(v2_db)
        assert TargetResolver().exists(TargetType.TESTCASE, "01ARZ3NDEKTSV4RRFFQ69G5ZZZ") is False
        trace = trace_backward_from_case("01ARZ3NDEKTSV4RRFFQ69G5ZZZ")
        assert trace.issues
