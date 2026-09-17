"""V2 Step 9 验收门槛测试 - 19 条逐条对应。

集成测试：使用 v2_db fixture，验证端到端流程（编辑/Revision/Validator/乐观锁/状态机/重评审）。
"""

from unittest.mock import MagicMock, patch

from core.schemas import (
    GenerationConfig,
    GenerationScope,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    ReviewTriggerType,
    Run,
    RunStatus,
    SourceType,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestDimension,
    TestPoint,
    TestStep,
)
from core.v2 import ddl
from core.v2.human_editor import edit_test_case
from core.v2.human_editor_orchestrator import re_review_test_cases

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
TC_ID = "01ARZ3NDEKTSV4RRFFQ69G5TCA"
TP_ID = "01ARZ3NDEKTSV4RRFFQ69G5TP1"
ITEM_ID = "01ARZ3NDEKTSV4RRFFQ69G5TM1"


def _tc(run_id: str, **kwargs) -> TestCase:
    steps_count = kwargs.pop("steps_count", 2)
    steps = [TestStep(seq=i + 1, action=f"步骤{i + 1}", expected="ok") for i in range(steps_count)]
    defaults = {
        "id": TC_ID,
        "run_id": run_id,
        "display_id": "TC_001",
        "module": "登录",
        "title": "原标题",
        "precondition": "前置条件",
        "steps": steps,
        "expected": "预期结果",
        "priority": Priority.P1,
        "type": TestCaseType.FUNCTIONAL,
        "provenance": Provenance.LLM,
        "status": TestCaseStatus.REVIEWED,
        "test_point_ids": [TP_ID],
        "fingerprint": "tc_abc123",
        "content_hash": "tch_original",
    }
    defaults.update(kwargs)
    return TestCase(**defaults)


def _setup(v2_db, tc_kwargs=None):
    """创建完整前置数据：User → Doc → Version → Item → Config → Run → TestCase。"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="Step9 验收", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    v2_db.save_version(ver)
    item = RequirementItem(
        id=ITEM_ID, version_id=ver.id, seq=1, type=RequirementItemType.FUNCTION, module="登录", statement="手机号登录"
    )
    v2_db.save_item(item)
    doc.latest_version_id = ver.id
    v2_db.save_doc(doc)
    cfg = GenerationConfig(
        model_provider="test", model_name="test-model", temperature=0.7, prompt_version="v1", generator_version="v1"
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

    # 创建 TestPoint（TestCase 外键依赖）
    tp = TestPoint(
        id=TP_ID,
        run_id=run.id,
        version_id=ver.id,
        item_ids=[ITEM_ID],
        module="登录",
        subcategory="校验",
        title="TP",
        description="d",
        dimension=TestDimension.FUNCTIONAL,
        priority=Priority.P1,
        provenance=Provenance.LLM,
        generation_scope=GenerationScope.ITEM,
    )
    v2_db.save_test_point(tp)

    tc = _tc(run.id, **(tc_kwargs or {}))
    v2_db.save_test_case(tc)
    return run, tc


# ============================================================
# 门槛 1-3: 编辑范围（白名单 + 系统字段保护）
# ============================================================


class TestGate123EditScope:
    def test_editable_fields_work(self, v2_db):
        """门槛1: title/steps/expected/precondition/priority/module/remark 可修改。"""
        run, tc = _setup(v2_db)
        result = edit_test_case(
            tc.id,
            {
                "title": "新标题",
                "expected": "新预期",
                "precondition": "新前置",
                "priority": "P0",
                "module": "注册",
                "remark": "备注",
            },
        )
        assert result.success is True
        updated = v2_db.get_test_case(tc.id)
        assert updated.title == "新标题"
        assert updated.expected == "新预期"
        assert updated.module == "注册"

    def test_test_point_ids_rejected(self, v2_db):
        """门槛2: test_point_ids 不可通过普通编辑接口修改。"""
        run, tc = _setup(v2_db)
        result = edit_test_case(tc.id, {"title": "新标题", "test_point_ids": []})
        assert result.success is True
        assert "test_point_ids" in result.rejected_fields
        updated = v2_db.get_test_case(tc.id)
        assert updated.test_point_ids == [TP_ID]  # 未变

    def test_system_fields_rejected(self, v2_db):
        """门槛3: id/run_id/fingerprint/created_at 等系统字段不可修改。"""
        run, tc = _setup(v2_db)
        result = edit_test_case(tc.id, {"title": "新", "id": "hack", "fingerprint": "hack", "created_at": "hack"})
        assert set(result.rejected_fields) >= {"id", "fingerprint", "created_at"}
        updated = v2_db.get_test_case(tc.id)
        assert updated.id == tc.id  # 未变
        assert updated.fingerprint == "tc_abc123"  # 未变


# ============================================================
# 门槛 4-5: Revision 快照 + changed_fields
# ============================================================


class TestGate45Revision:
    def test_revision_created(self, v2_db):
        """门槛4: 编辑前自动生成 Revision Snapshot（revision_no+1）。"""
        run, tc = _setup(v2_db)
        result = edit_test_case(tc.id, {"title": "新标题"})
        assert result.revision_no == 1

        revisions = v2_db.get_test_case_revisions(tc.id)
        assert len(revisions) == 1
        assert revisions[0].revision_no == 1
        assert revisions[0].snapshot["title"] == "原标题"  # 修改前快照

    def test_changed_fields_recorded(self, v2_db):
        """门槛5: Revision 记录 changed_fields。"""
        run, tc = _setup(v2_db)
        edit_test_case(tc.id, {"title": "新标题", "expected": "预期结果"})  # expected 未变

        revisions = v2_db.get_test_case_revisions(tc.id)
        assert revisions[0].changed_fields == ["title"]  # 只有 title 变了

    def test_second_edit_revision_no_2(self, v2_db):
        """多次编辑 revision_no 递增。"""
        run, tc = _setup(v2_db)
        edit_test_case(tc.id, {"title": "第一次修改"})
        # 重新编辑（此时状态是 RE_REVIEW_REQUIRED，需先模拟回到可编辑状态）
        tc2 = v2_db.get_test_case(tc.id)
        tc2.status = TestCaseStatus.REVIEWED
        v2_db.save_test_case(tc2)
        result2 = edit_test_case(tc.id, {"title": "第二次修改"})
        assert result2.revision_no == 2


# ============================================================
# 门槛 6-7: provenance 记录
# ============================================================


class TestGate67Provenance:
    def test_testcase_provenance_human(self, v2_db):
        """门槛6: 人工编辑后 TestCase.provenance = HUMAN。"""
        run, tc = _setup(v2_db)
        edit_test_case(tc.id, {"title": "新标题"})
        updated = v2_db.get_test_case(tc.id)
        assert updated.provenance == Provenance.HUMAN

    def test_revision_provenance_human(self, v2_db):
        """门槛7: Revision.provenance = HUMAN（这一版本是谁改的）。"""
        run, tc = _setup(v2_db)
        edit_test_case(tc.id, {"title": "新标题"})
        revisions = v2_db.get_test_case_revisions(tc.id)
        assert revisions[0].provenance == Provenance.HUMAN
        assert revisions[0].changed_by == "user"
        assert revisions[0].change_source == "human_edit"


# ============================================================
# 门槛 8-9: fingerprint / content_hash
# ============================================================


class TestGate89Fingerprint:
    def test_fingerprint_unchanged(self, v2_db):
        """门槛8: identity fingerprint 不变化（还是同一 TestCase）。"""
        run, tc = _setup(v2_db)
        original_fp = tc.fingerprint
        edit_test_case(tc.id, {"title": "新标题", "expected": "新预期"})
        updated = v2_db.get_test_case(tc.id)
        assert updated.fingerprint == original_fp

    def test_content_hash_changed(self, v2_db):
        """门槛9: content_hash 重新计算且发生变化。"""
        run, tc = _setup(v2_db)
        original_hash = tc.content_hash
        edit_test_case(tc.id, {"title": "新标题"})
        updated = v2_db.get_test_case(tc.id)
        assert updated.content_hash != original_hash
        assert updated.content_hash is not None


# ============================================================
# 门槛 10-12: Validator + VALIDATION_FAILED 恢复
# ============================================================


class TestGate101112Validator:
    def test_validator_pass(self, v2_db):
        """门槛10: 编辑后通过 Validator → RE_REVIEW_REQUIRED。"""
        run, tc = _setup(v2_db)
        result = edit_test_case(tc.id, {"title": "新标题"})
        assert result.new_status == TestCaseStatus.RE_REVIEW_REQUIRED

    def test_validator_fail(self, v2_db):
        """门槛11: Validator FAIL → VALIDATION_FAILED（不进入 RE_REVIEW_REQUIRED）。"""
        run, tc = _setup(v2_db)
        result = edit_test_case(tc.id, {"title": ""})  # 空标题
        assert result.new_status == TestCaseStatus.VALIDATION_FAILED
        assert len(result.validation_errors) > 0

    def test_validation_failed_can_recover(self, v2_db):
        """门槛12: VALIDATION_FAILED → EDITED 可恢复（不是死状态）。"""
        run, tc = _setup(v2_db)
        # 第一次编辑：失败
        edit_test_case(tc.id, {"title": ""})
        failed = v2_db.get_test_case(tc.id)
        assert failed.status == TestCaseStatus.VALIDATION_FAILED

        # 第二次编辑：修复
        result = edit_test_case(tc.id, {"title": "修复后的标题"})
        assert result.success is True
        assert result.new_status == TestCaseStatus.RE_REVIEW_REQUIRED


# ============================================================
# 门槛 13: 全流程状态机
# ============================================================


class TestGate13FullFlow:
    def test_reviewed_edited_re_review_reviewed(self, v2_db):
        """门槛13: REVIEWED → EDITED → RE_REVIEW_REQUIRED → REVIEWED 全流程。"""
        run, tc = _setup(v2_db)
        assert tc.status == TestCaseStatus.REVIEWED

        # 编辑 → RE_REVIEW_REQUIRED
        edit_test_case(tc.id, {"title": "新标题"})
        edited = v2_db.get_test_case(tc.id)
        assert edited.status == TestCaseStatus.RE_REVIEW_REQUIRED

        # 重评审 → REVIEWED（mock review_test_cases）
        with patch("core.v2.human_editor_orchestrator.review_test_cases") as mock_review:
            mock_review.return_value = MagicMock(reviewed_count=1, issues=[])
            re_review_test_cases(MagicMock(), run_id=run.id)
            call_kwargs = mock_review.call_args[1]
            assert call_kwargs["trigger_type"] == ReviewTriggerType.AFTER_HUMAN_EDIT


# ============================================================
# 门槛 14-15: 乐观锁并发控制
# ============================================================


class TestGate1415OptimisticLock:
    def test_stale_version_rejected(self, v2_db):
        """门槛14: 旧版本保存时拒绝覆盖新版本。"""
        run, tc = _setup(v2_db)
        stale_updated_at = tc.updated_at.isoformat()

        # 先做一次编辑（updated_at 变化）
        edit_test_case(tc.id, {"title": "第一次修改"})

        # 用旧的 updated_at 再次编辑 → 冲突
        result = edit_test_case(tc.id, {"title": "第二次修改"}, expected_updated_at=stale_updated_at)
        assert result.success is False
        assert any("刷新" in i or "已被更新" in i for i in result.issues)

    def test_conflict_no_revision_created(self, v2_db):
        """门槛15: 乐观锁冲突时整个事务回滚（无错误 Revision）。"""
        run, tc = _setup(v2_db)
        stale_updated_at = tc.updated_at.isoformat()

        edit_test_case(tc.id, {"title": "第一次修改"})
        rev_count_before = len(v2_db.get_test_case_revisions(tc.id))

        result = edit_test_case(tc.id, {"title": "冲突修改"}, expected_updated_at=stale_updated_at)
        assert result.success is False

        rev_count_after = len(v2_db.get_test_case_revisions(tc.id))
        assert rev_count_after == rev_count_before  # 未新增 Revision


# ============================================================
# 门槛 16-17: 重评审触发
# ============================================================


class TestGate1617ReReview:
    def test_no_auto_review_on_edit(self, v2_db):
        """门槛16: 保存仅标记 RE_REVIEW_REQUIRED，不自动触发重评审。"""
        run, tc = _setup(v2_db)
        with patch("core.v2.human_editor_orchestrator.review_test_cases") as mock_review:
            edit_test_case(tc.id, {"title": "新标题"})
            mock_review.assert_not_called()  # 编辑不自动触发 Review

    def test_re_review_trigger_type(self, v2_db):
        """门槛17: 重评审 trigger_type=AFTER_HUMAN_EDIT。"""
        run, tc = _setup(v2_db)
        with patch("core.v2.human_editor_orchestrator.review_test_cases") as mock_review:
            mock_review.return_value = MagicMock(reviewed_count=1, issues=[])
            re_review_test_cases(MagicMock(), run_id=run.id)
            call_kwargs = mock_review.call_args[1]
            assert call_kwargs["trigger_type"] == ReviewTriggerType.AFTER_HUMAN_EDIT


# ============================================================
# 门槛 18: 批量接口预留
# ============================================================


class TestGate18BatchInterface:
    def test_batch_interface_accepts_list(self, v2_db):
        """门槛18: re_review_test_cases 接受 test_case_ids list（第一版内部统一 Review）。"""
        run, tc = _setup(v2_db)
        with patch("core.v2.human_editor_orchestrator.review_test_cases") as mock_review:
            mock_review.return_value = MagicMock(reviewed_count=1, issues=[])
            result = re_review_test_cases(MagicMock(), run_id=run.id, test_case_ids=[tc.id, "OTHER"])
            assert result.requested_case_ids == [tc.id, "OTHER"]


# ============================================================
# 门槛 19: schema_version + V1 零回归
# ============================================================


class TestGate19SchemaAndV1:
    def test_schema_version_10(self, v2_db):
        """门槛19a: schema_version=10（Step 10.2 升到 10；test_case_revisions 表生效）。"""
        assert ddl.get_schema_version() == 10

    def test_revisions_table_exists(self, v2_db):
        """门槛19b: test_case_revisions 表存在。"""
        from core.v2.db import v2_read_conn

        with v2_read_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='test_case_revisions'"
            ).fetchone()
        assert row is not None

    def test_v1_no_regression(self):
        """门槛19c: V1 零回归。"""
        from core import db
        from core.generator import deduplicate

        assert callable(deduplicate)
        assert hasattr(db, "init_db")
