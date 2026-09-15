"""V2 数据模型 Schema 测试 - 验证 Pydantic 校验、字段约束、序列化往返。

对应 docs/v2/step1-data-model.md §13 验收标准第 2 条。
"""

import pytest
from pydantic import ValidationError

from core.schemas import (
    BusinessRule,
    CoverageObligation,
    DataType,
    ExpressionType,
    FieldSpec,
    GenerationConfig,
    ObligationStatus,
    PermissionRule,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    ReviewFinding,
    ReviewReport,
    ReviewScores,
    ReviewTriggerType,
    Run,
    RunStatus,
    Severity,
    SourceType,
    TargetType,
    TestCase,
    TestCaseType,
    TestPoint,
    TestStep,
    new_ulid,
)

# ============================================================
# ULID
# ============================================================


class TestUlid:
    def test_length_and_charset(self):
        u = new_ulid()
        assert len(u) == 26
        assert all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in u)

    def test_uniqueness(self):
        ids = {new_ulid() for _ in range(1000)}
        assert len(ids) == 1000

    def test_time_ordered(self):
        import time

        a = new_ulid()
        time.sleep(0.002)
        b = new_ulid()
        assert a < b  # ULID 时间有序


# ============================================================
# 需求层：Doc → Version → Item
# ============================================================


class TestRequirement:
    def test_doc_auto_id_and_defaults(self):
        doc = RequirementDoc(user_id=new_ulid(), title="登录模块", source_type=SourceType.MARKDOWN)
        assert len(doc.id) == 26
        assert doc.status.value == "draft"
        assert doc.schema_version == 2

    def test_version_no_must_be_positive(self):
        with pytest.raises(ValidationError):
            RequirementVersion(doc_id=new_ulid(), version_no=0)

    def test_item_belongs_to_version(self):
        item = RequirementItem(
            version_id=new_ulid(),
            seq=1,
            type=RequirementItemType.DATA_FIELD,
            module="登录",
            statement="手机号+密码登录",
        )
        assert item.version_id is not None
        assert item.provenance == Provenance.LLM

    def test_field_spec_range_validation(self):
        # min_length > max_length 应被代码拒绝
        with pytest.raises(ValidationError):
            FieldSpec(name="x", label="X", data_type=DataType.STRING, min_length=10, max_length=5)

    def test_field_spec_value_range_validation(self):
        with pytest.raises(ValidationError):
            FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=100, max_value=1)

    def test_field_spec_required_vs_nullable(self):
        f = FieldSpec(name="remark", label="备注", data_type=DataType.STRING, required=True, nullable=True)
        assert f.required is True
        assert f.nullable is True  # 二者语义独立

    def test_field_spec_data_type_enum_enforced(self):
        with pytest.raises(ValidationError):
            FieldSpec(name="x", label="X", data_type="not_a_type")

    def test_business_rule_computable(self):
        r_nl = BusinessRule(name="r1", expression="金额要合理")
        assert r_nl.is_computable is False
        r_cmp = BusinessRule(name="r2", expression="age >= 18", expression_type=ExpressionType.COMPARISON)
        assert r_cmp.is_computable is True

    def test_permission_rule_matrix_atom(self):
        p = PermissionRule(role="admin", resource="account", action="unlock", allowed=True)
        assert p.allowed is True

    def test_item_confidence_level_derived(self):
        item = RequirementItem(
            version_id=new_ulid(),
            seq=1,
            type=RequirementItemType.FUNCTION,
            module="m",
            statement="s",
            confidence=0.3,  # 低置信度
        )
        assert item.confidence_level.value == "low"  # 自动映射

    def test_item_confidence_bounds(self):
        with pytest.raises(ValidationError):
            RequirementItem(
                version_id=new_ulid(),
                seq=1,
                type=RequirementItemType.FUNCTION,
                module="m",
                statement="s",
                confidence=1.5,
            )


# ============================================================
# 严格模式：未知字段拒绝
# ============================================================


class TestStrictMode:
    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            RequirementDoc(
                user_id=new_ulid(),
                title="t",
                source_type=SourceType.TEXT,
                unknown_field="boom",
            )


# ============================================================
# 测试点 / 用例
# ============================================================


class TestTestAssets:
    def test_test_point_traceability(self):
        tp = TestPoint(
            item_ids=[new_ulid()],
            module="登录",
            subcategory="边界条件",
            title="手机号长度边界",
            description="验证10/11/12位",
            dimension="boundary",
        )
        assert len(tp.item_ids) == 1

    def test_test_case_type_is_enum(self):
        tc = TestCase(
            run_id=new_ulid(),
            display_id="TC_001",
            module="登录",
            title="t",
            steps=[TestStep(seq=1, action="输入手机号", data="1234567890")],
            expected="提示格式错误",
            type=TestCaseType.BOUNDARY,
        )
        assert tc.type == TestCaseType.BOUNDARY

    def test_test_case_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            TestCase(
                run_id=new_ulid(),
                display_id="TC_001",
                module="m",
                title="t",
                expected="e",
                type="随便写的类型",
            )

    def test_render_steps_text(self):
        tc = TestCase(
            run_id=new_ulid(),
            display_id="TC_001",
            module="m",
            title="t",
            steps=[
                TestStep(seq=2, action="点击登录"),
                TestStep(seq=1, action="输入手机号", data="123"),
            ],
            expected="e",
            type=TestCaseType.FUNCTIONAL,
        )
        text = tc.render_steps_text()
        assert text.index("输入手机号") < text.index("点击登录")  # 按 seq 排序

    def test_coverage_obligation_no_satisfied_by_fields(self):
        """P0-5：CoverageObligation 不再有 satisfied_by_* 持久化字段"""
        ob = CoverageObligation(
            run_id=new_ulid(),
            item_id=new_ulid(),
            technique="boundary_value",
            target="phone.length",
            description="10/11/12位",
            params={"min": 11, "max": 11},
        )
        assert ob.status == ObligationStatus.PENDING
        assert not hasattr(ob, "satisfied_by_cases")
        assert not hasattr(ob, "satisfied_by_points")
        with pytest.raises(ValidationError):
            CoverageObligation(
                run_id=new_ulid(),
                item_id=new_ulid(),
                technique="boundary_value",
                target="t",
                description="d",
                satisfied_by_cases=["x"],  # 该字段已移除，应被拒绝
            )


# ============================================================
# 评审
# ============================================================


class TestReview:
    def test_review_scores_bounds(self):
        with pytest.raises(ValidationError):
            ReviewScores(coverage=150, accuracy=90, executability=90, consistency=90, missing_risk=90, duplication=90)

    def test_review_scores_overall(self):
        s = ReviewScores(coverage=80, accuracy=90, executability=70, consistency=100, missing_risk=60, duplication=80)
        assert s.overall() == 80.0

    def test_review_report_revision(self):
        report = ReviewReport(
            run_id=new_ulid(),
            revision=2,
            trigger_type=ReviewTriggerType.AFTER_OPTIMIZER,
            scores=ReviewScores(
                coverage=80, accuracy=80, executability=80, consistency=80, missing_risk=80, duplication=80
            ),
            obligation_coverage=0.857,
        )
        assert report.revision == 2
        assert report.trigger_type == ReviewTriggerType.AFTER_OPTIMIZER

    def test_review_finding_polymorphic_target(self):
        f = ReviewFinding(
            dimension="coverage",
            severity=Severity.MAJOR,
            target_type=TargetType.OBLIGATION,
            target_id=new_ulid(),
            issue="义务未覆盖",
            provenance=Provenance.VALIDATOR,
        )
        assert f.id is not None  # 自动生成
        assert f.target_type == TargetType.OBLIGATION


# ============================================================
# 运行 + 生成审计
# ============================================================


class TestRun:
    def test_run_binds_version_and_config(self):
        """P0-2 / P0-3：Run 必须绑定 requirement_version_id 与 generation_config_id"""
        cfg = GenerationConfig(
            model_provider="openai",
            model_name="gpt-x",
            temperature=0.2,
            prompt_version="case-generator-v3",
            generator_version="2.1.0",
        )
        run = Run(
            user_id=new_ulid(),
            doc_id=new_ulid(),
            requirement_version_id=new_ulid(),
            generation_config_id=cfg.id,
        )
        assert run.requirement_version_id is not None
        assert run.generation_config_id == cfg.id
        assert run.status == RunStatus.INGESTING
        assert cfg.prompt_version == "case-generator-v3"

    def test_run_missing_version_rejected(self):
        with pytest.raises(ValidationError):
            Run(user_id=new_ulid(), doc_id=new_ulid(), generation_config_id=new_ulid())


# ============================================================
# 序列化往返（Repository 读写基础）
# ============================================================


class TestRoundTrip:
    def test_testcase_dump_validate(self):
        tc = TestCase(
            run_id=new_ulid(),
            display_id="TC_001",
            module="登录",
            title="t",
            steps=[TestStep(seq=1, action="a")],
            expected="e",
            type=TestCaseType.FUNCTIONAL,
            priority=Priority.P0,
        )
        dumped = tc.model_dump(mode="json")
        restored = TestCase.model_validate(dumped)
        assert restored.id == tc.id
        assert restored.type == TestCaseType.FUNCTIONAL
        assert restored.steps[0].action == "a"

    def test_item_nested_fields_roundtrip(self):
        item = RequirementItem(
            version_id=new_ulid(),
            seq=1,
            type=RequirementItemType.DATA_FIELD,
            module="登录",
            statement="s",
            fields=[FieldSpec(name="phone", label="手机号", data_type=DataType.PHONE, min_length=11, max_length=11)],
            rules=[BusinessRule(name="r", expression="x>1", expression_type=ExpressionType.COMPARISON)],
        )
        restored = RequirementItem.model_validate(item.model_dump(mode="json"))
        assert restored.fields[0].data_type == DataType.PHONE
        assert restored.rules[0].expression_type == ExpressionType.COMPARISON
