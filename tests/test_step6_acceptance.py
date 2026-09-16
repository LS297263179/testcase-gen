"""V2 Step 6 验收测试 - 13 条门槛逐条对应（用户 5 条 + 补充 8 条）。

对应 docs/v2/step6 设计文档与 plan §六。全代码，不调 LLM。核心：Step 6 只读、四态识别、
FieldSpec 隐性变更可发现、追溯影响不漏。
"""

import core.v2.change_impact as ci
from core.schemas import (
    AffectedReason,
    ChangeType,
    DataType,
    FieldSpec,
    GenerationConfig,
    GenerationScope,
    ImpactLevel,
    PermissionRule,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    Run,
    RunStatus,
    SourceType,
    TestCase,
    TestCaseType,
    TestDimension,
    TestPoint,
    TestStep,
)
from core.v2 import repository as repo
from core.v2.change_impact import analyze_change_impact
from core.v2.db import v2_read_conn
from core.v2.ddl import get_schema_version

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _age(min_v=18, max_v=60):
    return FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=min_v, max_value=max_v, nullable=True)


def _item(
    v2_db,
    ver,
    *,
    seq,
    module="登录",
    statement="年龄范围",
    type=RequirementItemType.DATA_FIELD,
    fields=None,
    permissions=None,
    acceptance=None,
) -> RequirementItem:
    it = RequirementItem(
        version_id=ver.id,
        seq=seq,
        type=type,
        module=module,
        statement=statement,
        fields=fields or [],
        permissions=permissions or [],
        acceptance_criteria=acceptance or [],
        confidence=0.9,
    )
    v2_db.save_item(it)
    return it


def _setup(v2_db, v1_specs, v2_specs, *, with_assets=False):
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="Step6 验收", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    v1 = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="v1", provenance=Provenance.LLM)
    v2_db.save_version(v1)
    v1_items = [_item(v2_db, v1, seq=i + 1, **s) for i, s in enumerate(v1_specs)]
    v2 = RequirementVersion(doc_id=doc.id, version_no=2, raw_text="v2", provenance=Provenance.LLM)
    v2_db.save_version(v2)
    v2_items = [_item(v2_db, v2, seq=i + 1, **s) for i, s in enumerate(v2_specs)]
    doc.latest_version_id = v2.id
    v2_db.save_doc(doc)
    run = None
    if with_assets:
        cfg = GenerationConfig(
            model_provider="o", model_name="g", temperature=0.3, prompt_version="t", generator_version="t"
        )
        v2_db.save_generation_config(cfg)
        run = Run(
            user_id=USER_ID,
            doc_id=doc.id,
            requirement_version_id=v1.id,
            generation_config_id=cfg.id,
            status=RunStatus.DONE,
        )
        v2_db.save_run(run)
        for i, it in enumerate(v1_items):
            tp = TestPoint(
                run_id=run.id,
                version_id=v1.id,
                item_ids=[it.id],
                module=it.module,
                subcategory="校验",
                title=f"TP{i}",
                description="d",
                dimension=TestDimension.FUNCTIONAL,
                priority=Priority.P1,
                provenance=Provenance.LLM,
                generation_scope=GenerationScope.ITEM,
            )
            v2_db.save_test_point(tp)
            tc = TestCase(
                run_id=run.id,
                display_id=f"TC_{i + 1:03d}",
                test_point_ids=[tp.id],
                module=it.module,
                title="用例",
                precondition="",
                steps=[TestStep(seq=1, action="操作", expected="结果")],
                expected="预期",
                priority=Priority.P1,
                type=TestCaseType.FUNCTIONAL,
            )
            v2_db.save_test_case(tc)
    return doc, v1, v2, v1_items, v2_items, run


# 门槛 1：V_old 的 items/TP/TC 执行前后不被修改
def test_01_old_version_not_modified(v2_db):
    doc, v1, v2, i1, i2, run = _setup(
        v2_db,
        [{"statement": "年龄范围", "fields": [_age(18, 60)]}],
        [{"statement": "年龄范围", "fields": [_age(18, 65)]}],
        with_assets=True,
    )
    items_before = [(it.id, it.statement, it.fingerprint, it.content_hash) for it in repo.list_items(v1.id)]
    analyze_change_impact(v1.id, v2.id)
    items_after = [(it.id, it.statement, it.fingerprint, it.content_hash) for it in repo.list_items(v1.id)]
    assert items_before == items_after


# 门槛 2：正确识别四种变化
def test_02_four_change_types(v2_db):
    doc, v1, v2, *_ = _setup(
        v2_db,
        [
            {"statement": "不变的项"},
            {"statement": "年龄范围", "fields": [_age(18, 60)]},
            {"module": "旧", "statement": "将被删除的项"},
        ],
        [
            {"statement": "不变的项"},
            {"statement": "年龄范围", "fields": [_age(18, 65)]},
            {"module": "新", "statement": "全新增加的项"},
        ],
    )
    report = analyze_change_impact(v1.id, v2.id)
    assert report.summary["unchanged"] == 1
    assert report.summary["modified"] == 1
    assert report.summary["added"] == 1
    assert report.summary["deleted"] == 1


# 门槛 3：FieldSpec 隐性变更（statement 不变，18~60→18~65）判 MODIFIED
def test_03_fieldspec_change_detected(v2_db):
    doc, v1, v2, *_ = _setup(
        v2_db,
        [{"statement": "年龄必须符合规定范围", "fields": [_age(18, 60)]}],
        [{"statement": "年龄必须符合规定范围", "fields": [_age(18, 65)]}],
    )
    report = analyze_change_impact(v1.id, v2.id)
    assert report.summary["modified"] == 1
    assert AffectedReason.FIELD_CONSTRAINT_CHANGED in report.changes[0].affected_reasons


# 门槛 4：modified RI → affected TP → affected TC 一个不漏
def test_04_affected_traced_completely(v2_db):
    doc, v1, v2, i1, i2, run = _setup(
        v2_db,
        [{"statement": "年龄范围", "fields": [_age(18, 60)]}],
        [{"statement": "年龄范围", "fields": [_age(18, 65)]}],
        with_assets=True,
    )
    report = analyze_change_impact(v1.id, v2.id)
    ch = report.changes[0]
    all_tp = repo.list_test_points_by_item(i1[0].id)
    all_tc = [c for tp in all_tp for c in repo.list_test_cases_by_test_point(tp.id)]
    assert set(ch.affected_test_points) == {tp.id for tp in all_tp}
    assert set(ch.affected_test_cases) == {tc.id for tc in all_tc}
    assert len(ch.affected_test_points) == 1 and len(ch.affected_test_cases) == 1


# 门槛 5：Step 6 只读，执行前后 TP/TC/status 无业务状态变化
def test_05_readonly_no_status_change(v2_db):
    doc, v1, v2, i1, i2, run = _setup(
        v2_db,
        [{"statement": "年龄范围", "fields": [_age(18, 60)]}],
        [{"statement": "年龄范围", "fields": [_age(18, 65)]}],
        with_assets=True,
    )
    tp_before = sorted((t.id, str(t.status)) for t in repo.list_test_points_by_run(run.id))
    tc_before = sorted((c.id, c.status.value) for c in repo.list_test_cases(run.id))
    analyze_change_impact(v1.id, v2.id)
    tp_after = sorted((t.id, str(t.status)) for t in repo.list_test_points_by_run(run.id))
    tc_after = sorted((c.id, c.status.value) for c in repo.list_test_cases(run.id))
    assert tp_before == tp_after
    assert tc_before == tc_after


# 门槛 6：identity fingerprint 不含 doc_id（同 doc 跨版本同一 item fingerprint 相同）
def test_06_identity_fingerprint_cross_version(v2_db):
    doc, v1, v2, i1, i2, _ = _setup(
        v2_db,
        [{"statement": "手机号 11 位", "fields": [_age(18, 60)]}],
        [{"statement": "手机号 11 位", "fields": [_age(18, 65)]}],
    )
    assert i1[0].fingerprint == i2[0].fingerprint  # identity 同
    assert i1[0].content_hash != i2[0].content_hash  # content 异
    assert i1[0].fingerprint.startswith("ri_")


# 门槛 7：跨 doc 不可比
def test_07_cross_doc_incomparable(v2_db):
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    da = RequirementDoc(user_id=USER_ID, title="A", source_type=SourceType.TEXT)
    v2_db.save_doc(da)
    va = RequirementVersion(doc_id=da.id, version_no=1, provenance=Provenance.LLM)
    v2_db.save_version(va)
    db = RequirementDoc(user_id=USER_ID, title="B", source_type=SourceType.TEXT)
    v2_db.save_doc(db)
    vb = RequirementVersion(doc_id=db.id, version_no=1, provenance=Provenance.LLM)
    v2_db.save_version(vb)
    report = analyze_change_impact(va.id, vb.id)
    assert report.changes == []
    assert any("不属于同一 Doc" in i for i in report.issues)


# 门槛 8：相似度兜底
def test_08_similarity_fallback(v2_db):
    doc, v1, v2, *_ = _setup(
        v2_db, [{"statement": "用户手机号必须是 11 位数字"}], [{"statement": "用户的手机号必须是 11 位数字"}]
    )
    report = analyze_change_impact(v1.id, v2.id)
    assert report.summary["modified"] == 1
    assert report.changes[0].similarity >= ci.DEFAULT_SIMILARITY_THRESHOLD


# 门槛 9：affected_reason 正确细分
def test_09_affected_reason_granular(v2_db):
    doc, v1, v2, *_ = _setup(
        v2_db,
        [
            {
                "statement": "退款权限",
                "permissions": [PermissionRule(role="admin", resource="order", action="refund", allowed=True)],
            }
        ],
        [
            {
                "statement": "退款权限",
                "permissions": [PermissionRule(role="admin", resource="order", action="refund", allowed=False)],
            }
        ],
    )
    ch = analyze_change_impact(v1.id, v2.id).changes[0]
    assert AffectedReason.PERMISSION_CHANGED in ch.affected_reasons


# 门槛 10：impact_level 代码化判定
def test_10_impact_level(v2_db):
    # field 变 → HIGH
    doc, v1, v2, *_ = _setup(
        v2_db,
        [{"statement": "年龄范围", "fields": [_age(18, 60)]}],
        [{"statement": "年龄范围", "fields": [_age(18, 65)]}],
    )
    assert analyze_change_impact(v1.id, v2.id).changes[0].impact_level == ImpactLevel.HIGH
    # 措辞变 → LOW
    doc2, w1, w2, *_ = _setup(
        v2_db, [{"statement": "用户手机号必须是 11 位数字"}], [{"statement": "用户的手机号必须是 11 位数字"}]
    )
    assert analyze_change_impact(w1.id, w2.id).changes[0].impact_level == ImpactLevel.LOW


# 门槛 11：content_hash 分量比对（仅 fields 变也判 MODIFIED）
def test_11_content_component_diff(v2_db):
    doc, v1, v2, *_ = _setup(
        v2_db,
        [{"statement": "年龄范围", "fields": [_age(18, 60)], "acceptance": ["通过"]}],
        [{"statement": "年龄范围", "fields": [_age(18, 65)], "acceptance": ["通过"]}],
    )
    ch = analyze_change_impact(v1.id, v2.id).changes[0]
    assert ch.change_type == ChangeType.MODIFIED
    assert ch.affected_reasons == [AffectedReason.FIELD_CONSTRAINT_CHANGED]


# 门槛 12：schema_version=6 + fingerprint 非 UNIQUE 普通索引
def test_12_schema_v6_and_index_non_unique(v2_db):
    assert get_schema_version() == 6
    with v2_read_conn() as conn:
        idxs = conn.execute("PRAGMA index_list(requirement_items)").fetchall()
    fp_idx = [i for i in idxs if i["name"] == "idx_items_fingerprint"]
    assert fp_idx and fp_idx[0]["unique"] == 0  # 非 UNIQUE（跨版本可重复）


# 门槛 13：V1 零回归
def test_13_v1_untouched(v2_db):
    from core import db as v1_db

    assert v1_db._DB_PATH.endswith("data.db")
    from web.data import TEST_POINTS_PROMPT

    assert "资深测试工程师" in TEST_POINTS_PROMPT
    from core.generator import ANALYSIS_PROMPT, MODULE_PROMPT, SYSTEM_PROMPT

    assert ANALYSIS_PROMPT and MODULE_PROMPT and SYSTEM_PROMPT
