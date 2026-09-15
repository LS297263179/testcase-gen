"""V2 Repository 持久化测试 - Pydantic ↔ SQLite 往返、追溯链、覆盖率、多轮评审。

对应 docs/v2/step1-data-model.md §13 验收标准第 3 条。
"""

import pytest

from core.schemas import (
    BusinessRule,
    CoverageObligation,
    DataType,
    ExpressionType,
    FieldSpec,
    GenerationConfig,
    GenerationScope,
    ObligationStatus,
    Preference,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    ReviewDimension,
    ReviewFinding,
    ReviewReport,
    ReviewScores,
    ReviewTriggerType,
    Run,
    RunStatus,
    Severity,
    SourceType,
    TargetType,
    Technique,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestDimension,
    TestPoint,
    TestStep,
    new_ulid,
)
from core.v2 import ddl


@pytest.fixture
def repo(v2_db):
    """v2_db fixture 已建表，直接产出 repository 模块；并预置一个用户"""
    v2_db.save_user("01ARZ3NDEKTSV4RRFFQ69G5FAV", "alice", "hash", legacy_int_id=1)
    return v2_db


USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _make_chain(repo):
    """构建 Doc→Version→Item→(Config,Run) 完整上游链，返回关键 id"""
    doc = RequirementDoc(user_id=USER_ID, title="登录模块", source_type=SourceType.MARKDOWN)
    repo.save_doc(doc)

    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="验证码5分钟有效")
    repo.save_version(ver)

    item = RequirementItem(
        version_id=ver.id,
        seq=1,
        type=RequirementItemType.DATA_FIELD,
        module="登录",
        statement="验证码有效期",
        fields=[
            FieldSpec(name="code_expiry_min", label="有效期(分)", data_type=DataType.INT, min_value=5, max_value=5),
            FieldSpec(
                name="phone", label="手机号", data_type=DataType.PHONE, required=True, min_length=11, max_length=11
            ),
        ],
        rules=[BusinessRule(name="锁定", expression="fail>=5", expression_type=ExpressionType.COMPARISON)],
    )
    repo.save_item(item)

    cfg = GenerationConfig(
        model_provider="openai",
        model_name="gpt-x",
        temperature=0.2,
        prompt_version="case-generator-v3",
        generator_version="2.1.0",
    )
    repo.save_generation_config(cfg)

    run = Run(user_id=USER_ID, doc_id=doc.id, requirement_version_id=ver.id, generation_config_id=cfg.id)
    repo.save_run(run)
    return doc, ver, item, cfg, run


class TestSchema:
    def test_schema_version(self, repo):
        assert ddl.get_schema_version() == 3

    def test_create_is_idempotent(self, repo):
        ddl.create_v2_schema()  # 再次执行不应报错
        assert ddl.get_schema_version() == 3


class TestRequirementChain:
    def test_doc_roundtrip(self, repo):
        doc = RequirementDoc(user_id=USER_ID, title="t", source_type=SourceType.PDF, asset_ids=[new_ulid()])
        repo.save_doc(doc)
        got = repo.get_doc(doc.id)
        assert got.title == "t"
        assert got.source_type == SourceType.PDF
        assert got.asset_ids == doc.asset_ids

    def test_version_roundtrip_and_listing(self, repo):
        doc = RequirementDoc(user_id=USER_ID, title="t", source_type=SourceType.TEXT)
        repo.save_doc(doc)
        v1 = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="5分钟")
        v2 = RequirementVersion(doc_id=doc.id, version_no=2, raw_text="10分钟", change_summary="5→10")
        repo.save_version(v1)
        repo.save_version(v2)
        versions = repo.list_versions(doc.id)
        assert [v.version_no for v in versions] == [1, 2]  # 按版本号排序
        assert versions[1].change_summary == "5→10"

    def test_item_with_fields_roundtrip(self, repo):
        _, _, item, _, _ = _make_chain(repo)
        got = repo.get_item(item.id)
        assert got.statement == "验证码有效期"
        assert len(got.fields) == 2
        phone = next(f for f in got.fields if f.name == "phone")
        assert phone.data_type == DataType.PHONE
        assert phone.min_length == 11 and phone.required is True
        assert got.rules[0].expression_type == ExpressionType.COMPARISON

    def test_items_by_version(self, repo):
        _, ver, item, _, _ = _make_chain(repo)
        items = repo.list_items(ver.id)
        assert len(items) == 1
        assert items[0].id == item.id


class TestRunAndAudit:
    def test_run_binds_version_and_config(self, repo):
        """P0-2/P0-3：Run 持久化后仍绑定 version 与 generation config"""
        _, ver, _, cfg, run = _make_chain(repo)
        got = repo.get_run(run.id)
        assert got.requirement_version_id == ver.id
        assert got.generation_config_id == cfg.id
        assert got.status == RunStatus.INGESTING

    def test_generation_config_roundtrip(self, repo):
        _, _, _, cfg, _ = _make_chain(repo)
        got = repo.get_generation_config(cfg.id)
        assert got.model_name == "gpt-x"
        assert got.prompt_version == "case-generator-v3"
        assert got.temperature == 0.2


class TestTraceability:
    def test_point_to_item_link(self, repo):
        """追溯：TestPoint.item_ids 往返"""
        _, ver, item, _, run = _make_chain(repo)
        tp = TestPoint(
            run_id=run.id,
            version_id=ver.id,
            item_ids=[item.id],
            module="登录",
            subcategory="边界条件",
            title="有效期边界",
            description="4:59/5:00/5:01",
            dimension=TestDimension.BOUNDARY,
            technique=Technique.BOUNDARY_VALUE,
            provenance=Provenance.STRATEGY,
        )
        repo.save_test_point(tp)
        got = repo.get_test_point(tp.id)
        assert got.item_ids == [item.id]
        assert got.technique == Technique.BOUNDARY_VALUE

    def test_testpoint_fingerprint_auto_computed_and_idempotent(self, repo):
        """Step 3：fingerprint 入库前兜底计算；同业务身份重存复用旧 id，不产生重复行"""
        _, ver, item, _, run = _make_chain(repo)
        tp1 = TestPoint(
            run_id=run.id,
            version_id=ver.id,
            item_ids=[item.id],
            module="登录",
            subcategory="边界",
            title="验证码有效期",
            description="4:59/5:00/5:01",
            dimension=TestDimension.BOUNDARY,
            generation_scope=GenerationScope.ITEM,
        )
        repo.save_test_point(tp1)
        assert tp1.fingerprint is not None and tp1.fingerprint.startswith("tp_")
        first_id = tp1.id

        # 重新构造一个业务身份完全相同但 ULID 不同的 TestPoint → 应复用旧 id
        tp2 = TestPoint(
            run_id=run.id,
            version_id=ver.id,
            item_ids=[item.id],
            module="登录",
            subcategory="边界",
            title="验证码有效期",
            description="4:59/5:00/5:01 更新后的描述",
            dimension=TestDimension.BOUNDARY,
            generation_scope=GenerationScope.ITEM,
        )
        repo.save_test_point(tp2)
        assert tp2.id == first_id, "同 fingerprint 应复用旧 ULID，保证下游链接不断"
        got = repo.get_test_point(first_id)
        assert got.description == "4:59/5:00/5:01 更新后的描述"  # upsert 更新了非身份字段

        # 按 fingerprint 查询
        by_fp = repo.get_test_point_by_fingerprint(tp1.fingerprint)
        assert by_fp is not None and by_fp.id == first_id

    def test_testpoint_different_scope_no_collision(self, repo):
        """Step 3：generation_scope 不同的两个 TestPoint 即使其他字段相同也不撞 fingerprint"""
        _, ver, item, _, run = _make_chain(repo)
        common = {
            "run_id": run.id,
            "version_id": ver.id,
            "item_ids": [item.id],
            "module": "登录",
            "subcategory": "边界",
            "title": "验证码有效期",
            "description": "d",
            "dimension": TestDimension.BOUNDARY,
        }
        tp_item = TestPoint(**common, generation_scope=GenerationScope.ITEM)
        tp_cross = TestPoint(**common, generation_scope=GenerationScope.CROSS_ITEM)
        repo.save_test_point(tp_item)
        repo.save_test_point(tp_cross)
        assert tp_item.fingerprint != tp_cross.fingerprint
        assert tp_item.id != tp_cross.id

    def test_case_to_point_link_and_steps(self, repo):
        """追溯：TestCase.test_point_ids + 结构化步骤往返"""
        _, ver, item, _, run = _make_chain(repo)
        tp = TestPoint(
            run_id=run.id,
            version_id=ver.id,
            item_ids=[item.id],
            module="登录",
            subcategory="边界",
            title="t",
            description="d",
            dimension=TestDimension.BOUNDARY,
        )
        repo.save_test_point(tp)
        tc = TestCase(
            run_id=run.id,
            display_id="TC_001",
            test_point_ids=[tp.id],
            module="登录",
            title="验证码4:59可用",
            precondition="已获取验证码",
            steps=[TestStep(seq=1, action="等待", data="4分59秒"), TestStep(seq=2, action="提交验证码")],
            expected="验证通过",
            type=TestCaseType.BOUNDARY,
            priority=Priority.P1,
        )
        repo.save_test_case(tc)
        got = repo.get_test_case(tc.id)
        assert got.test_point_ids == [tp.id]
        assert len(got.steps) == 2
        assert got.steps[0].data == "4分59秒"
        assert got.type == TestCaseType.BOUNDARY

    def test_full_chain_query(self, repo):
        """完整链：item → point → case 可逐级查到"""
        _, ver, item, _, run = _make_chain(repo)
        tp = TestPoint(
            run_id=run.id,
            version_id=ver.id,
            item_ids=[item.id],
            module="登录",
            subcategory="s",
            title="t",
            description="d",
            dimension=TestDimension.FUNCTIONAL,
        )
        repo.save_test_point(tp)
        tc = TestCase(
            run_id=run.id,
            display_id="TC_001",
            test_point_ids=[tp.id],
            module="登录",
            title="c",
            expected="e",
            type=TestCaseType.FUNCTIONAL,
        )
        repo.save_test_case(tc)
        # 反向：case → point → item
        got_case = repo.get_test_case(tc.id)
        got_point = repo.get_test_point(got_case.test_point_ids[0])
        assert got_point.item_ids == [item.id]
        # run 下用例列表
        cases = repo.list_test_cases(run.id)
        assert len(cases) == 1


class TestCoverage:
    def test_obligation_and_coverage_ratio(self, repo):
        """P0-5：覆盖义务 + obligation_coverage 唯一事实源 + 覆盖率硬指标"""
        _, _, item, _, run = _make_chain(repo)
        ob1 = CoverageObligation(
            run_id=run.id,
            item_id=item.id,
            technique=Technique.BOUNDARY_VALUE,
            target="code_expiry",
            description="5分钟边界",
            params={"min": 5, "max": 5},
        )
        ob2 = CoverageObligation(
            run_id=run.id,
            item_id=item.id,
            technique=Technique.EQUIVALENCE_CLASS,
            target="phone.format",
            description="手机号格式等价类",
        )
        repo.save_obligation(ob1)
        repo.save_obligation(ob2)

        tp = TestPoint(
            run_id=run.id, module="登录", subcategory="s", title="t", description="d", dimension=TestDimension.BOUNDARY
        )
        repo.save_test_point(tp)
        repo.add_coverage(ob1.id, TargetType.TESTPOINT, tp.id)

        assert repo.coverage_targets(ob1.id, TargetType.TESTPOINT) == [tp.id]
        assert repo.coverage_targets(ob2.id, TargetType.TESTPOINT) == []
        assert repo.obligation_coverage_ratio(run.id) == 0.5  # 1/2 覆盖

    def test_obligation_roundtrip(self, repo):
        _, _, item, _, run = _make_chain(repo)
        ob = CoverageObligation(
            run_id=run.id,
            item_id=item.id,
            technique=Technique.PERMISSION_MATRIX,
            target="account.unlock",
            description="权限矩阵",
            params={"roles": ["admin", "user"]},
        )
        repo.save_obligation(ob)
        got = repo.get_obligation(ob.id)
        assert got.status == ObligationStatus.PENDING
        assert got.params == {"roles": ["admin", "user"]}
        assert not hasattr(got, "satisfied_by_cases")  # P0-5：无该持久化字段


class TestReview:
    def test_multi_revision_reports(self, repo):
        """P0-4：同一 run 多轮评审按 revision 排序"""
        _, _, _, _, run = _make_chain(repo)
        scores = ReviewScores(
            coverage=80, accuracy=90, executability=85, consistency=95, missing_risk=70, duplication=90
        )
        r1 = ReviewReport(
            run_id=run.id,
            revision=1,
            trigger_type=ReviewTriggerType.INITIAL,
            scores=scores,
            overall_score=85.0,
            obligation_coverage=0.5,
        )
        r2 = ReviewReport(
            run_id=run.id,
            revision=2,
            trigger_type=ReviewTriggerType.AFTER_OPTIMIZER,
            scores=scores,
            overall_score=90.0,
            obligation_coverage=1.0,
        )
        repo.save_review_report(r1)
        repo.save_review_report(r2)
        reports = repo.list_review_reports(run.id)
        assert [r.revision for r in reports] == [1, 2]
        assert reports[1].trigger_type == ReviewTriggerType.AFTER_OPTIMIZER
        assert reports[1].obligation_coverage == 1.0

    def test_findings_roundtrip(self, repo):
        _, _, item, _, run = _make_chain(repo)
        scores = ReviewScores(
            coverage=80, accuracy=90, executability=85, consistency=95, missing_risk=70, duplication=90
        )
        finding = ReviewFinding(
            dimension=ReviewDimension.COVERAGE,
            severity=Severity.MAJOR,
            target_type=TargetType.REQUIREMENT_ITEM,
            target_id=item.id,
            issue="边界未覆盖",
            provenance=Provenance.VALIDATOR,
        )
        report = ReviewReport(
            run_id=run.id,
            revision=1,
            trigger_type=ReviewTriggerType.INITIAL,
            scores=scores,
            overall_score=85.0,
            findings=[finding],
        )
        repo.save_review_report(report)
        got = repo.get_review_report(report.id)
        assert len(got.findings) == 1
        assert got.findings[0].target_id == item.id
        assert got.findings[0].provenance == Provenance.VALIDATOR
        assert got.scores.coverage == 80


class TestPreferenceAndStatus:
    def test_preference_roundtrip(self, repo):
        pref = Preference(
            user_id=USER_ID, category="step_style", pattern="步骤用动词开头", weight=1.0, source_diff={"field": "steps"}
        )
        repo.save_preference(pref)
        got = repo.get_preference(pref.id)
        assert got.pattern == "步骤用动词开头"
        assert got.source_diff == {"field": "steps"}
        assert got.active is True

    def test_status_persisted(self, repo):
        """用例状态机结果可持久化"""
        _, _, _, _, run = _make_chain(repo)
        tc = TestCase(
            run_id=run.id, display_id="TC_001", module="m", title="t", expected="e", type=TestCaseType.FUNCTIONAL
        )
        tc.transition_to(TestCaseStatus.VALIDATED)
        tc.transition_to(TestCaseStatus.REVIEWED)
        repo.save_test_case(tc)
        got = repo.get_test_case(tc.id)
        assert got.status == TestCaseStatus.REVIEWED


class TestUpsertNoCascade:
    """回归：重存父实体不得级联删光子实体。

    防护 INSERT OR REPLACE + ON DELETE CASCADE 陷阱：REPLACE = 先 DELETE 再 INSERT，
    DELETE 会级联删光子表。Repository 已全面改用 upsert(ON CONFLICT DO UPDATE)。
    """

    def test_resave_doc_preserves_versions_and_items(self, repo):
        doc, ver, item, cfg, run = _make_chain(repo)
        doc.latest_version_id = ver.id
        repo.save_doc(doc)  # 重存 doc（更新指针）
        assert repo.get_version(ver.id) is not None
        assert repo.get_item(item.id) is not None
        assert repo.get_doc(doc.id).latest_version_id == ver.id

    def test_resave_version_preserves_items(self, repo):
        _, ver, item, _, _ = _make_chain(repo)
        ver.change_summary = "更新了描述"
        repo.save_version(ver)
        assert repo.get_item(item.id) is not None
        assert repo.get_version(ver.id).change_summary == "更新了描述"

    def test_resave_run_preserves_cases(self, repo):
        _, _, _, _, run = _make_chain(repo)
        tc = TestCase(
            run_id=run.id, display_id="TC_001", module="m", title="t", expected="e", type=TestCaseType.FUNCTIONAL
        )
        repo.save_test_case(tc)
        run.status = RunStatus.DONE
        repo.save_run(run)  # 重存 run（更新状态）
        assert repo.get_test_case(tc.id) is not None
        assert len(repo.list_test_cases(run.id)) == 1
