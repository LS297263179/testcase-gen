"""V2 Repository 字段级往返保真测试。

Step1 验收 #3 的强证据：逐个实体、逐个字段设独特值，save→get 后必须完全一致，
防止"某字段漏了列映射 → 静默丢数据"这类地基级隐患（类似迁移孤儿 bug）。
"""

from types import SimpleNamespace

import pytest

from core.schemas import (
    BusinessRule,
    ConfidenceLevel,
    CoverageObligation,
    DataType,
    EntityStatus,
    ExpressionType,
    FieldSpec,
    GenerationConfig,
    GenerationScope,
    ObligationStatus,
    PermissionRule,
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
    RunCounts,
    RunStatus,
    Severity,
    SourceRef,
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

USER = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


@pytest.fixture
def chain(v2_db):
    """建好 FK 上游链（user→doc→version→item→config→run），返回各 id"""
    v2_db.save_user(USER, "alice", "hash")
    doc = RequirementDoc(user_id=USER, title="基础doc", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1)
    v2_db.save_version(ver)
    item = RequirementItem(version_id=ver.id, seq=1, type=RequirementItemType.FUNCTION, module="m", statement="s")
    v2_db.save_item(item)
    cfg = GenerationConfig(
        model_provider="openai",
        model_name="m",
        temperature=0.3,
        prompt_version="p",
        generator_version="g",
    )
    v2_db.save_generation_config(cfg)
    run = Run(user_id=USER, doc_id=doc.id, requirement_version_id=ver.id, generation_config_id=cfg.id)
    v2_db.save_run(run)
    return SimpleNamespace(user=USER, doc=doc.id, ver=ver.id, item=item.id, cfg=cfg.id, run=run.id, repo=v2_db)


def test_requirement_doc_all_fields(chain):
    repo = chain.repo
    doc = RequirementDoc(
        user_id=USER,
        title="完整doc",
        source_type=SourceType.PDF,
        asset_ids=[new_ulid(), new_ulid()],
        material_ids=[new_ulid()],
        latest_version_id=new_ulid(),
        status=EntityStatus.CONFIRMED,
    )
    repo.save_doc(doc)
    got = repo.get_doc(doc.id)
    assert got.title == "完整doc"
    assert got.source_type == SourceType.PDF
    assert got.asset_ids == doc.asset_ids
    assert got.material_ids == doc.material_ids
    assert got.latest_version_id == doc.latest_version_id
    assert got.status == EntityStatus.CONFIRMED


def test_requirement_version_all_fields(chain):
    repo = chain.repo
    ref = SourceRef(locator="line", value="12", asset_id=new_ulid())
    ver = RequirementVersion(
        doc_id=chain.doc,
        version_no=7,
        raw_text="原始需求全文",
        change_summary="改了有效期",
        source_ref=ref,
        provenance=Provenance.MIGRATED,
        status=EntityStatus.CONFIRMED,
    )
    repo.save_version(ver)
    got = repo.get_version(ver.id)
    assert got.version_no == 7
    assert got.raw_text == "原始需求全文"
    assert got.change_summary == "改了有效期"
    assert got.source_ref.locator == "line" and got.source_ref.value == "12"
    assert got.source_ref.asset_id == ref.asset_id
    assert got.provenance == Provenance.MIGRATED
    assert got.status == EntityStatus.CONFIRMED


def test_requirement_item_all_fields(chain):
    repo = chain.repo
    field = FieldSpec(
        name="amount",
        label="金额",
        data_type=DataType.FLOAT,
        required=True,
        nullable=True,
        min_length=1,
        max_length=10,
        min_value=0.01,
        max_value=99999.99,
        pattern=r"^\d+$",
        enum_values=["a", "b"],
        precision=2,
        unique=True,
        default="0",
        unit="元",
        format="currency",
        example="100.00",
    )
    rule = BusinessRule(
        name="总额",
        expression_type=ExpressionType.FORMULA,
        expression="total = price * qty",
        inputs=["price", "qty"],
        expected="总价",
    )
    perm = PermissionRule(role="admin", resource="order", action="refund", allowed=True, condition="仅本人")
    ref = SourceRef(locator="paragraph", value="3")
    item = RequirementItem(
        version_id=chain.ver,
        seq=5,
        type=RequirementItemType.DATA_FIELD,
        module="订单",
        statement="金额字段校验",
        fields=[field],
        rules=[rule],
        permissions=[perm],
        acceptance_criteria=["AC1", "AC2"],
        source_ref=ref,
        priority_hint=Priority.P2,
        confidence=0.9,
        confidence_level=ConfidenceLevel.HIGH,
        provenance=Provenance.STRATEGY,
        status=EntityStatus.CONFIRMED,
    )
    repo.save_item(item)
    got = repo.get_item(item.id)
    assert got.seq == 5 and got.type == RequirementItemType.DATA_FIELD and got.module == "订单"
    assert got.statement == "金额字段校验"
    f = got.fields[0]
    assert (f.name, f.label, f.data_type) == ("amount", "金额", DataType.FLOAT)
    assert f.required is True and f.nullable is True
    assert (f.min_length, f.max_length) == (1, 10)
    assert (f.min_value, f.max_value) == (0.01, 99999.99)
    assert f.pattern == r"^\d+$" and f.enum_values == ["a", "b"] and f.precision == 2
    assert f.unique is True and f.default == "0" and f.unit == "元"
    assert f.format == "currency" and f.example == "100.00"
    r = got.rules[0]
    assert r.expression_type == ExpressionType.FORMULA and r.expression == "total = price * qty"
    assert r.inputs == ["price", "qty"] and r.expected == "总价"
    p = got.permissions[0]
    assert (p.role, p.resource, p.action, p.allowed, p.condition) == ("admin", "order", "refund", True, "仅本人")
    assert got.acceptance_criteria == ["AC1", "AC2"]
    assert got.source_ref.locator == "paragraph" and got.source_ref.value == "3"
    assert got.priority_hint == Priority.P2
    assert got.confidence == 0.9 and got.confidence_level == ConfidenceLevel.HIGH
    assert got.provenance == Provenance.STRATEGY and got.status == EntityStatus.CONFIRMED


def test_testpoint_all_fields(chain):
    repo = chain.repo
    tp = TestPoint(
        run_id=chain.run,
        version_id=chain.ver,
        item_ids=[chain.item],
        module="订单",
        subcategory="边界",
        title="金额边界",
        description="0.01/99999.99",
        dimension=TestDimension.BOUNDARY,
        technique=Technique.BOUNDARY_VALUE,
        obligation_id=new_ulid(),
        priority=Priority.P0,
        provenance=Provenance.STRATEGY,
        status=EntityStatus.CONFIRMED,
        generation_scope=GenerationScope.CROSS_ITEM,
        fingerprint="tp_deadbeefdeadbeefdeadbeefdeadbeef",
    )
    repo.save_test_point(tp)
    got = repo.get_test_point(tp.id)
    assert got.run_id == chain.run and got.version_id == chain.ver and got.item_ids == [chain.item]
    assert (got.module, got.subcategory, got.title, got.description) == ("订单", "边界", "金额边界", "0.01/99999.99")
    assert got.dimension == TestDimension.BOUNDARY and got.technique == Technique.BOUNDARY_VALUE
    assert got.obligation_id == tp.obligation_id
    assert got.priority == Priority.P0 and got.provenance == Provenance.STRATEGY
    assert got.status == EntityStatus.CONFIRMED
    # Step 3 新增字段往返保真
    assert got.generation_scope == GenerationScope.CROSS_ITEM
    assert got.fingerprint == "tp_deadbeefdeadbeefdeadbeefdeadbeef"


def test_testcase_all_fields(chain):
    repo = chain.repo
    tp = TestPoint(module="m", subcategory="s", title="t", description="d", dimension=TestDimension.FUNCTIONAL)
    repo.save_test_point(tp)
    steps = [
        TestStep(seq=1, action="输入金额", data="100", expected="显示100"),
        TestStep(seq=2, action="提交", data=None, expected="成功"),
    ]
    tc = TestCase(
        run_id=chain.run,
        display_id="TC_042",
        test_point_ids=[tp.id],
        module="订单",
        title="金额边界用例",
        precondition="已登录",
        steps=steps,
        expected="提交成功",
        priority=Priority.P0,
        type=TestCaseType.BOUNDARY,
        remark="备注X",
        fingerprint="order|amount|boundary",
        provenance=Provenance.STRATEGY,
        status=TestCaseStatus.CONFIRMED,
        confidence_level=ConfidenceLevel.HIGH,
    )
    repo.save_test_case(tc)
    got = repo.get_test_case(tc.id)
    assert got.run_id == chain.run and got.display_id == "TC_042" and got.test_point_ids == [tp.id]
    assert (got.module, got.title, got.precondition, got.expected) == ("订单", "金额边界用例", "已登录", "提交成功")
    assert len(got.steps) == 2
    assert (got.steps[0].seq, got.steps[0].action, got.steps[0].data, got.steps[0].expected) == (
        1,
        "输入金额",
        "100",
        "显示100",
    )
    assert got.steps[1].data is None
    assert got.priority == Priority.P0 and got.type == TestCaseType.BOUNDARY
    assert got.remark == "备注X" and got.fingerprint == "order|amount|boundary"
    assert got.provenance == Provenance.STRATEGY and got.status == TestCaseStatus.CONFIRMED
    assert got.confidence_level == ConfidenceLevel.HIGH


def test_coverage_obligation_all_fields(chain):
    repo = chain.repo
    ob = CoverageObligation(
        run_id=chain.run,
        item_id=chain.item,
        technique=Technique.EQUIVALENCE_CLASS,
        target="amount.range",
        description="金额等价类",
        params={"min": 0.01, "max": 99999.99, "classes": 3},
        status=ObligationStatus.COVERED,
    )
    repo.save_obligation(ob)
    got = repo.get_obligation(ob.id)
    assert got.item_id == chain.item and got.technique == Technique.EQUIVALENCE_CLASS
    assert got.target == "amount.range" and got.description == "金额等价类"
    assert got.params == {"min": 0.01, "max": 99999.99, "classes": 3}
    assert got.status == ObligationStatus.COVERED


def test_run_all_fields(chain):
    repo = chain.repo
    run = Run(
        user_id=USER,
        doc_id=chain.doc,
        requirement_version_id=chain.ver,
        generation_config_id=chain.cfg,
        strategy_profile="full-coverage",
        status=RunStatus.DONE,
        counts=RunCounts(items=3, points=12, cases=40, obligations=8),
        legacy_session_id=99,
    )
    repo.save_run(run)
    got = repo.get_run(run.id)
    assert got.strategy_profile == "full-coverage" and got.status == RunStatus.DONE
    assert (got.counts.items, got.counts.points, got.counts.cases, got.counts.obligations) == (3, 12, 40, 8)
    assert got.legacy_session_id == 99
    assert got.requirement_version_id == chain.ver and got.generation_config_id == chain.cfg


def test_generation_config_all_fields(chain):
    repo = chain.repo
    cfg = GenerationConfig(
        model_provider="anthropic",
        model_name="claude-x",
        temperature=0.7,
        max_tokens=4096,
        enable_thinking=True,
        prompt_version="case-generator-v9",
        generator_version="3.2.1",
        reviewer_version="1.5.0",
    )
    repo.save_generation_config(cfg)
    got = repo.get_generation_config(cfg.id)
    assert (got.model_provider, got.model_name, got.temperature, got.max_tokens) == ("anthropic", "claude-x", 0.7, 4096)
    assert got.enable_thinking is True
    assert got.prompt_version == "case-generator-v9" and got.generator_version == "3.2.1"
    assert got.reviewer_version == "1.5.0"


def test_review_report_all_fields(chain):
    repo = chain.repo
    scores = ReviewScores(coverage=91, accuracy=82, executability=73, consistency=64, missing_risk=55, duplication=46)
    finding = ReviewFinding(
        dimension=ReviewDimension.MISSING_RISK,
        severity=Severity.CRITICAL,
        target_type=TargetType.REQUIREMENT_ITEM,
        target_id=chain.item,
        issue="遗漏并发场景",
        suggestion="补充并发用例",
        provenance=Provenance.LLM,
        auto_fixable=True,
    )
    report = ReviewReport(
        run_id=chain.run,
        revision=3,
        trigger_type=ReviewTriggerType.AFTER_HUMAN_EDIT,
        scores=scores,
        overall_score=68.5,
        summary="总体尚可",
        obligation_coverage=0.75,
        findings=[finding],
    )
    repo.save_review_report(report)
    got = repo.get_review_report(report.id)
    assert got.revision == 3 and got.trigger_type == ReviewTriggerType.AFTER_HUMAN_EDIT
    assert got.scores.coverage == 91 and got.scores.duplication == 46
    assert got.overall_score == 68.5 and got.summary == "总体尚可" and got.obligation_coverage == 0.75
    assert len(got.findings) == 1
    f = got.findings[0]
    assert f.dimension == ReviewDimension.MISSING_RISK and f.severity == Severity.CRITICAL
    assert f.target_type == TargetType.REQUIREMENT_ITEM and f.target_id == chain.item
    assert f.issue == "遗漏并发场景" and f.suggestion == "补充并发用例"
    assert f.provenance == Provenance.LLM and f.auto_fixable is True


def test_preference_all_fields(chain):
    repo = chain.repo
    pref = Preference(
        user_id=USER,
        category="naming",
        pattern="标题用动宾结构",
        weight=0.75,
        active=False,
        source_diff={"field": "title", "from": "a", "to": "b"},
    )
    repo.save_preference(pref)
    got = repo.get_preference(pref.id)
    assert got.category == "naming" and got.pattern == "标题用动宾结构"
    assert got.weight == 0.75 and got.active is False
    assert got.source_diff == {"field": "title", "from": "a", "to": "b"}
