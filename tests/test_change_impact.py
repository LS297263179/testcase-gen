"""V2 Step 6 变更影响分析测试 - ItemMatcher 四态 + content 分量 + 相似度兜底 + reason/level + 只读。

全代码，不调 LLM。核心验证"statement 未改但 FieldSpec 改了"能被识别为 MODIFIED（门槛 3）。
"""

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
from core.v2.change_impact import (
    ChangeImpactReport,
    ItemMatcher,
    analyze_change_impact,
)

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _mk_item(
    v2_db,
    ver,
    *,
    seq,
    module="登录",
    statement="手机号 11 位",
    type=RequirementItemType.DATA_FIELD,
    fields=None,
    rules=None,
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
        rules=rules or [],
        permissions=permissions or [],
        acceptance_criteria=acceptance or [],
        confidence=0.9,
    )
    v2_db.save_item(it)
    return it


def _two_versions(v2_db, v1_specs, v2_specs, *, with_assets=False):
    """建 Doc + v1(items) + v2(items)；with_assets 时在 v1 的 item 上挂 TP+TC。"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="变更影响测试", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    v1 = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="v1", provenance=Provenance.LLM)
    v2_db.save_version(v1)
    v1_items = [_mk_item(v2_db, v1, seq=i + 1, **spec) for i, spec in enumerate(v1_specs)]
    v2 = RequirementVersion(doc_id=doc.id, version_no=2, raw_text="v2", provenance=Provenance.LLM)
    v2_db.save_version(v2)
    v2_items = [_mk_item(v2_db, v2, seq=i + 1, **spec) for i, spec in enumerate(v2_specs)]
    doc.latest_version_id = v2.id
    v2_db.save_doc(doc)

    run = None
    if with_assets:
        cfg = GenerationConfig(
            model_provider="openai", model_name="gpt-x", temperature=0.3, prompt_version="t", generator_version="t"
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
                title=f"TP-{it.statement}",
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


def _age_field(min_v=18, max_v=60):
    return FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=min_v, max_value=max_v, nullable=True)


# ============================================================
# ItemMatcher 四态识别（门槛 2）
# ============================================================


class TestItemMatcher:
    def test_unchanged(self, v2_db):
        doc, v1, v2, i1, i2, _ = _two_versions(
            v2_db,
            [{"statement": "手机号 11 位", "fields": [_age_field()]}],
            [{"statement": "手机号 11 位", "fields": [_age_field()]}],
        )
        report = analyze_change_impact(v1.id, v2.id)
        assert report.summary["unchanged"] == 1
        assert report.changes[0].change_type == ChangeType.UNCHANGED

    def test_modified_by_field_constraint(self, v2_db):
        """门槛 3：statement 不变但 age 18~60 → 18~65 必须判 MODIFIED"""
        doc, v1, v2, i1, i2, _ = _two_versions(
            v2_db,
            [{"statement": "年龄必须符合规定范围", "fields": [_age_field(18, 60)]}],
            [{"statement": "年龄必须符合规定范围", "fields": [_age_field(18, 65)]}],
        )
        report = analyze_change_impact(v1.id, v2.id)
        assert report.summary["modified"] == 1
        ch = report.changes[0]
        assert ch.change_type == ChangeType.MODIFIED
        assert AffectedReason.FIELD_CONSTRAINT_CHANGED in ch.affected_reasons
        assert ch.impact_level == ImpactLevel.HIGH

    def test_added(self, v2_db):
        doc, v1, v2, i1, i2, _ = _two_versions(
            v2_db,
            [{"statement": "手机号 11 位"}],
            [{"statement": "手机号 11 位"}, {"module": "订单", "statement": "订单金额不得超过库存"}],
        )
        report = analyze_change_impact(v1.id, v2.id)
        assert report.summary["added"] == 1
        added = [c for c in report.changes if c.change_type == ChangeType.ADDED][0]
        assert AffectedReason.REQUIREMENT_ADDED in added.affected_reasons
        assert added.impact_level == ImpactLevel.HIGH
        assert added.affected_test_points == []  # 新增项无既有资产

    def test_deleted(self, v2_db):
        doc, v1, v2, i1, i2, _ = _two_versions(
            v2_db,
            [{"statement": "手机号 11 位"}, {"module": "订单", "statement": "订单金额不得超过库存"}],
            [{"statement": "手机号 11 位"}],
        )
        report = analyze_change_impact(v1.id, v2.id)
        assert report.summary["deleted"] == 1
        deleted = [c for c in report.changes if c.change_type == ChangeType.DELETED][0]
        assert AffectedReason.REQUIREMENT_DELETED in deleted.affected_reasons

    def test_modified_by_similarity_fallback(self, v2_db):
        """门槛 8：statement 微调致 identity 漂移，相似度高 → MODIFIED（带 similarity）"""
        doc, v1, v2, i1, i2, _ = _two_versions(
            v2_db,
            [{"statement": "用户手机号必须是 11 位数字"}],
            [{"statement": "用户的手机号必须是 11 位数字"}],
        )
        report = analyze_change_impact(v1.id, v2.id)
        assert report.summary["modified"] == 1
        ch = report.changes[0]
        assert ch.change_type == ChangeType.MODIFIED
        assert ch.similarity is not None and ch.similarity >= 0.6

    def test_dissimilar_becomes_added_deleted(self, v2_db):
        """相似度低于阈值 → 不配对，各自 ADDED / DELETED"""
        doc, v1, v2, i1, i2, _ = _two_versions(
            v2_db,
            [{"statement": "用户手机号必须是 11 位数字"}],
            [{"module": "支付", "statement": "支持微信支付和支付宝两种渠道"}],
        )
        report = analyze_change_impact(v1.id, v2.id)
        assert report.summary["added"] == 1
        assert report.summary["deleted"] == 1
        assert report.summary["modified"] == 0


# ============================================================
# affected_reason 细分（门槛 9）+ impact_level（门槛 10）
# ============================================================


class TestReasonAndLevel:
    def test_reason_permission_changed(self, v2_db):
        doc, v1, v2, *_ = _two_versions(
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
        assert ch.impact_level == ImpactLevel.HIGH

    def test_reason_acceptance_changed_medium(self, v2_db):
        doc, v1, v2, *_ = _two_versions(
            v2_db,
            [{"statement": "登录成功", "acceptance": ["跳转首页"]}],
            [{"statement": "登录成功", "acceptance": ["跳转首页并显示用户名"]}],
        )
        ch = analyze_change_impact(v1.id, v2.id).changes[0]
        assert AffectedReason.ACCEPTANCE_CHANGED in ch.affected_reasons
        assert ch.impact_level == ImpactLevel.MEDIUM

    def test_reason_wording_only_low(self, v2_db):
        """门槛 10：仅 statement 措辞变（结构化分量全同）→ REQUIREMENT_MODIFIED + LOW"""
        doc, v1, v2, *_ = _two_versions(
            v2_db,
            [{"statement": "用户手机号必须是 11 位数字"}],
            [{"statement": "用户的手机号必须是 11 位数字"}],
        )
        ch = analyze_change_impact(v1.id, v2.id).changes[0]
        assert ch.affected_reasons == [AffectedReason.REQUIREMENT_MODIFIED]
        assert ch.impact_level == ImpactLevel.LOW

    def test_content_hash_component_isolation(self, v2_db):
        """门槛 11：仅 fields 变（statement/rules/perms/acceptance 同）也能定位并判 MODIFIED"""
        doc, v1, v2, *_ = _two_versions(
            v2_db,
            [{"statement": "年龄范围", "fields": [_age_field(18, 60)], "acceptance": ["校验通过"]}],
            [{"statement": "年龄范围", "fields": [_age_field(18, 65)], "acceptance": ["校验通过"]}],
        )
        ch = analyze_change_impact(v1.id, v2.id).changes[0]
        assert ch.change_type == ChangeType.MODIFIED
        assert ch.affected_reasons == [AffectedReason.FIELD_CONSTRAINT_CHANGED]


# ============================================================
# 追溯受影响资产（门槛 4）+ 跨 doc（门槛 7）+ 只读（门槛 1/5）
# ============================================================


class TestImpactAndReadOnly:
    def test_affected_tp_tc_traced(self, v2_db):
        """门槛 4：modified item → affected TP → affected TC 一个不漏"""
        doc, v1, v2, i1, i2, run = _two_versions(
            v2_db,
            [{"statement": "年龄范围", "fields": [_age_field(18, 60)]}],
            [{"statement": "年龄范围", "fields": [_age_field(18, 65)]}],
            with_assets=True,
        )
        report = analyze_change_impact(v1.id, v2.id)
        ch = report.changes[0]
        assert ch.change_type == ChangeType.MODIFIED
        assert len(ch.affected_test_points) == 1  # v1 item 挂的 TP
        assert len(ch.affected_test_cases) == 1  # TP 挂的 TC
        assert report.summary["affected_test_point_count"] == 1
        assert report.summary["affected_test_case_count"] == 1

    def test_deleted_item_assets_traced(self, v2_db):
        doc, v1, v2, i1, i2, run = _two_versions(
            v2_db,
            [{"statement": "手机号 11 位"}, {"module": "订单", "statement": "订单金额不得超过库存"}],
            [{"statement": "手机号 11 位"}],
            with_assets=True,
        )
        report = analyze_change_impact(v1.id, v2.id)
        deleted = [c for c in report.changes if c.change_type == ChangeType.DELETED][0]
        assert len(deleted.affected_test_points) == 1
        assert len(deleted.affected_test_cases) == 1

    def test_cross_doc_incomparable(self, v2_db):
        """门槛 7：两个不同 doc 的 version 不可比 → 带 issue 的空报告"""
        v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
        doc_a = RequirementDoc(user_id=USER_ID, title="A", source_type=SourceType.TEXT)
        v2_db.save_doc(doc_a)
        ver_a = RequirementVersion(doc_id=doc_a.id, version_no=1, provenance=Provenance.LLM)
        v2_db.save_version(ver_a)
        doc_b = RequirementDoc(user_id=USER_ID, title="B", source_type=SourceType.TEXT)
        v2_db.save_doc(doc_b)
        ver_b = RequirementVersion(doc_id=doc_b.id, version_no=1, provenance=Provenance.LLM)
        v2_db.save_version(ver_b)
        report = analyze_change_impact(ver_a.id, ver_b.id)
        assert report.changes == []
        assert any("不属于同一 Doc" in i for i in report.issues)

    def test_nonexistent_version(self, v2_db):
        _two_versions(v2_db, [{"statement": "x"}], [{"statement": "x"}])
        report = analyze_change_impact("01ARZ3NDEKTSV4RRFFQ69G5ZZZ", "01ARZ3NDEKTSV4RRFFQ69G5FYY")
        assert report.changes == []
        assert any("不存在" in i for i in report.issues)

    def test_readonly_no_db_change(self, v2_db):
        """门槛 1/5：analyze 前后 TP/TC/status 不变（Step 6 只读）"""
        doc, v1, v2, i1, i2, run = _two_versions(
            v2_db,
            [{"statement": "年龄范围", "fields": [_age_field(18, 60)]}],
            [{"statement": "年龄范围", "fields": [_age_field(18, 65)]}],
            with_assets=True,
        )
        tps_before = [(t.id, t.status) for t in repo.list_test_points_by_run(run.id)]
        tcs_before = [(c.id, c.status.value) for c in repo.list_test_cases(run.id)]
        analyze_change_impact(v1.id, v2.id)
        tps_after = [(t.id, t.status) for t in repo.list_test_points_by_run(run.id)]
        tcs_after = [(c.id, c.status.value) for c in repo.list_test_cases(run.id)]
        assert tps_before == tps_after
        assert tcs_before == tcs_after

    def test_report_is_dataclass_not_persisted(self, v2_db):
        doc, v1, v2, *_ = _two_versions(v2_db, [{"statement": "x"}], [{"statement": "y"}])
        report = analyze_change_impact(v1.id, v2.id)
        assert isinstance(report, ChangeImpactReport)
        assert isinstance(report.summary, dict)


# ============================================================
# identity fingerprint 不含 doc_id（门槛 6）
# ============================================================


class TestIdentityFingerprint:
    def test_same_item_across_versions_same_fingerprint(self, v2_db):
        """门槛 6：同 doc 跨版本的同一 item（module+type+statement 同）fingerprint 相同"""
        doc, v1, v2, i1, i2, _ = _two_versions(
            v2_db,
            [{"statement": "手机号 11 位", "fields": [_age_field(18, 60)]}],
            [{"statement": "手机号 11 位", "fields": [_age_field(18, 65)]}],
        )
        # fields 不同（content 变），但 identity（module+type+statement）相同
        assert i1[0].fingerprint == i2[0].fingerprint
        assert i1[0].content_hash != i2[0].content_hash

    def test_fingerprint_not_unique_across_versions(self, v2_db):
        """门槛 12：fingerprint 非 UNIQUE，跨版本可重复（DB 允许同 fingerprint 多行）"""
        doc, v1, v2, i1, i2, _ = _two_versions(v2_db, [{"statement": "手机号 11 位"}], [{"statement": "手机号 11 位"}])
        # 两个版本的 item 都成功入库（未因 UNIQUE 冲突失败）
        assert repo.get_item(i1[0].id) is not None
        assert repo.get_item(i2[0].id) is not None
        assert i1[0].fingerprint == i2[0].fingerprint


# ============================================================
# ItemMatcher 单元（阈值可配）
# ============================================================


class TestMatcherUnit:
    def test_threshold_configurable(self, v2_db):
        doc, v1, v2, i1, i2, _ = _two_versions(
            v2_db,
            [{"statement": "用户手机号必须是 11 位数字"}],
            [{"statement": "用户的手机号必须是 11 位数字"}],
        )
        # 阈值 0.99 → 相似度不够 → 不配对为 MODIFIED，变成 ADDED+DELETED
        matcher = ItemMatcher(similarity_threshold=0.99)
        matches = matcher.match(i1, i2)
        types = {m.change_type for m in matches}
        assert ChangeType.ADDED in types and ChangeType.DELETED in types
