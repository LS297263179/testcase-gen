"""V2 多态目标 Domain Validator 测试（P1-5）。

对应 docs/v2/step1-data-model.md §6 + §13 验收标准第 4 条。
DB 无法约束 (target_type,target_id)，由 TargetResolver + ReferentialValidator 保障。
"""

import pytest

from core.schemas import (
    RequirementDoc,
    SourceType,
    TargetType,
    TestCase,
    TestCaseType,
    TestDimension,
    TestPoint,
    new_ulid,
)
from core.v2.resolver import ReferentialIntegrityError, ReferentialValidator, TargetResolver

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


@pytest.fixture
def resolver_env(v2_db):
    """建表 + 预置用户/一条用例/一个测试点，返回 (repo, resolver, validator, ids)"""
    v2_db.save_user(USER_ID, "alice", "hash")
    resolver = TargetResolver()
    validator = ReferentialValidator(resolver)

    doc = RequirementDoc(user_id=USER_ID, title="t", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)

    tp = TestPoint(module="m", subcategory="s", title="tp", description="d", dimension=TestDimension.FUNCTIONAL)
    v2_db.save_test_point(tp)
    return v2_db, resolver, validator, {"doc": doc.id, "testpoint": tp.id}


class TestTargetResolver:
    def test_table_mapping(self, resolver_env):
        _, resolver, _, _ = resolver_env
        assert resolver.table_for(TargetType.TESTPOINT) == "test_points"
        assert resolver.table_for("testpoint") == "test_points"
        assert resolver.table_for(TargetType.OBLIGATION) == "coverage_obligations"

    def test_exists_true_and_false(self, resolver_env):
        _, resolver, _, ids = resolver_env
        assert resolver.exists(TargetType.TESTPOINT, ids["testpoint"]) is True
        assert resolver.exists(TargetType.TESTPOINT, new_ulid()) is False


class TestReferentialValidator:
    def test_validate_existing_passes(self, resolver_env):
        _, _, validator, ids = resolver_env
        validator.validate(TargetType.TESTPOINT, ids["testpoint"])  # 不抛异常

    def test_validate_missing_raises(self, resolver_env):
        _, _, validator, _ = resolver_env
        with pytest.raises(ReferentialIntegrityError):
            validator.validate(TargetType.TESTCASE, new_ulid())  # 不存在的用例

    def test_cross_type_not_confused(self, resolver_env):
        """同一个 id 在 testpoint 存在，但当作 testcase 查应不存在（多态按类型分表）"""
        _, _, validator, ids = resolver_env
        validator.validate(TargetType.TESTPOINT, ids["testpoint"])  # ok
        with pytest.raises(ReferentialIntegrityError):
            validator.validate(TargetType.TESTCASE, ids["testpoint"])  # 该 id 不是 testcase

    def test_validate_after_insert(self, resolver_env):
        """插入实体后，多态引用立即可通过校验"""
        repo, _, validator, _ = resolver_env
        # 需要一个 run 才能建 test_case（run_id 非空外键）
        from core.schemas import GenerationConfig, RequirementVersion, Run

        doc = RequirementDoc(user_id=USER_ID, title="t2", source_type=SourceType.TEXT)
        repo.save_doc(doc)
        ver = RequirementVersion(doc_id=doc.id, version_no=1)
        repo.save_version(ver)
        cfg = GenerationConfig(
            model_provider="openai", model_name="m", temperature=0.3, prompt_version="p-v1", generator_version="1.0.0"
        )
        repo.save_generation_config(cfg)
        run = Run(user_id=USER_ID, doc_id=doc.id, requirement_version_id=ver.id, generation_config_id=cfg.id)
        repo.save_run(run)

        tc = TestCase(
            run_id=run.id, display_id="TC_001", module="m", title="t", expected="e", type=TestCaseType.FUNCTIONAL
        )
        with pytest.raises(ReferentialIntegrityError):
            validator.validate(TargetType.TESTCASE, tc.id)  # 尚未入库
        repo.save_test_case(tc)
        validator.validate(TargetType.TESTCASE, tc.id)  # 入库后通过
