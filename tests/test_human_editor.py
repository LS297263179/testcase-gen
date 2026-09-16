"""V2 Step 9 Human Editor 测试 - 白名单过滤 / Revision 快照 / Validator / 乐观锁 / fingerprint。

human_editor 依赖 repo 做持久化，故用 mock repo 做单元测试。
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from core.schemas import (
    Priority,
    Provenance,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestStep,
)
from core.v2.human_editor import (
    EDITABLE_FIELDS,
    SYSTEM_FIELDS,
    EditResult,
    create_revision_snapshot,
    edit_test_case,
    filter_edit_payload,
    validate_edited_case,
)
from core.v2.repository import ConcurrentModificationError

TC_ID = "01ARZ3NDEKTSV4RRFFQ69G5TCA"
RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _tc(
    tc_id: str = TC_ID,
    display_id: str = "TC_001",
    *,
    title: str = "原标题",
    steps_count: int = 2,
    status: TestCaseStatus = TestCaseStatus.REVIEWED,
    provenance: Provenance = Provenance.LLM,
    fingerprint: str = "tc_abc123",
    content_hash: str = "tch_xyz789",
    updated_at: datetime | None = None,
) -> TestCase:
    steps = [TestStep(seq=i + 1, action=f"步骤{i + 1}", expected="ok") for i in range(steps_count)]
    return TestCase(
        id=tc_id,
        run_id=RUN_ID,
        display_id=display_id,
        module="登录",
        title=title,
        precondition="前置条件",
        steps=steps,
        expected="预期结果",
        priority=Priority.P1,
        type=TestCaseType.FUNCTIONAL,
        provenance=provenance,
        status=status,
        fingerprint=fingerprint,
        content_hash=content_hash,
        test_point_ids=["01ARZ3NDEKTSV4RRFFQ69G5TP1"],
        updated_at=updated_at or datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC),
    )


# ============================================================
# filter_edit_payload（白名单过滤）
# ============================================================


class TestFilterEditPayload:
    def test_editable_fields_pass(self):
        """白名单字段通过。"""
        payload = {"title": "新标题", "steps": [], "expected": "新预期"}
        updates, rejected = filter_edit_payload(payload)
        assert updates == payload
        assert rejected == []

    def test_system_fields_rejected(self):
        """系统字段被拒绝。"""
        payload = {"title": "新标题", "id": "hack", "fingerprint": "hack", "test_point_ids": []}
        updates, rejected = filter_edit_payload(payload)
        assert updates == {"title": "新标题"}
        assert set(rejected) == {"id", "fingerprint", "test_point_ids"}

    def test_unknown_fields_rejected(self):
        """未知字段被拒绝。"""
        payload = {"title": "新标题", "foobar": "hack"}
        updates, rejected = filter_edit_payload(payload)
        assert updates == {"title": "新标题"}
        assert rejected == ["foobar"]

    def test_all_editable_fields(self):
        """所有白名单字段：title/steps/expected/precondition/priority/module/remark。"""
        assert {"title", "steps", "expected", "precondition", "priority", "module", "remark"} == EDITABLE_FIELDS

    def test_system_fields_include_test_point_ids(self):
        """test_point_ids 在系统字段中（不可普通编辑）。"""
        assert "test_point_ids" in SYSTEM_FIELDS


# ============================================================
# create_revision_snapshot（Revision 快照 + changed_fields）
# ============================================================


class TestCreateRevisionSnapshot:
    def test_snapshot_is_pre_edit(self):
        """snapshot 保存修改前完整内容。"""
        tc = _tc(title="原标题")
        updates = {"title": "新标题"}
        rev = create_revision_snapshot(tc, updates, revision_no=1)

        assert rev.snapshot["title"] == "原标题"  # 修改前
        assert rev.revision_no == 1
        assert rev.test_case_id == TC_ID

    def test_changed_fields_detected(self):
        """changed_fields 记录实际变化的字段。"""
        tc = _tc(title="原标题")
        updates = {"title": "新标题", "expected": "预期结果"}  # expected 未变
        rev = create_revision_snapshot(tc, updates, revision_no=1)

        assert rev.changed_fields == ["title"]  # 只有 title 变了

    def test_changed_fields_steps(self):
        """steps 变化正确检测（list[dict] 比较）。"""
        tc = _tc(steps_count=2)
        new_steps = [{"seq": 1, "action": "新步骤", "expected": "ok"}]
        updates = {"steps": new_steps}
        rev = create_revision_snapshot(tc, updates, revision_no=1)

        assert "steps" in rev.changed_fields

    def test_provenance_human(self):
        """Revision.provenance = HUMAN。"""
        tc = _tc()
        rev = create_revision_snapshot(tc, {"title": "新"}, revision_no=1)
        assert rev.provenance == Provenance.HUMAN

    def test_changed_by_and_source(self):
        """changed_by=user, change_source=human_edit。"""
        tc = _tc()
        rev = create_revision_snapshot(tc, {"title": "新"}, revision_no=1)
        assert rev.changed_by == "user"
        assert rev.change_source == "human_edit"


# ============================================================
# validate_edited_case（编辑后 Validator）
# ============================================================


class TestValidateEditedCase:
    def test_valid_case(self):
        """完整用例通过校验。"""
        tc = _tc()
        is_valid, errors = validate_edited_case(tc)
        assert is_valid is True
        assert errors == []

    def test_empty_title(self):
        """title 为空 → FAIL。"""
        tc = _tc(title="")
        is_valid, errors = validate_edited_case(tc)
        assert is_valid is False
        assert any("title" in e for e in errors)

    def test_empty_steps(self):
        """steps 为空 → FAIL。"""
        tc = _tc(steps_count=0)
        is_valid, errors = validate_edited_case(tc)
        assert is_valid is False
        assert any("steps" in e for e in errors)

    def test_empty_action(self):
        """step.action 为空 → FAIL。"""
        tc = _tc()
        tc.steps[0].action = ""
        is_valid, errors = validate_edited_case(tc)
        assert is_valid is False
        assert any("action" in e for e in errors)

    def test_empty_expected(self):
        """expected 为空 → FAIL。"""
        tc = _tc()
        tc.expected = ""
        is_valid, errors = validate_edited_case(tc)
        assert is_valid is False
        assert any("expected" in e for e in errors)


# ============================================================
# edit_test_case（端到端，mock repo）
# ============================================================


class TestEditTestCase:
    @pytest.fixture
    def mock_repo(self):
        with patch("core.v2.human_editor.repo") as mock:
            mock.get_test_case = MagicMock()
            mock.get_latest_revision_no = MagicMock(return_value=0)
            mock.update_test_case_with_lock = MagicMock()
            mock.save_test_case_revision = MagicMock()
            mock.ConcurrentModificationError = ConcurrentModificationError
            yield mock

    def test_case_not_found(self, mock_repo):
        """用例不存在 → 返回 issues。"""
        mock_repo.get_test_case.return_value = None
        result = edit_test_case(TC_ID, {"title": "新"})
        assert result.success is False
        assert any("不存在" in i for i in result.issues)

    def test_successful_edit(self, mock_repo):
        """成功编辑 → RE_REVIEW_REQUIRED + Revision 创建。"""
        tc = _tc()
        mock_repo.get_test_case.return_value = tc

        result = edit_test_case(TC_ID, {"title": "新标题"})

        assert result.success is True
        assert result.new_status == TestCaseStatus.RE_REVIEW_REQUIRED
        assert result.revision_no == 1
        assert "title" in result.changed_fields
        mock_repo.save_test_case_revision.assert_called_once()
        mock_repo.update_test_case_with_lock.assert_called_once()

    def test_fingerprint_unchanged(self, mock_repo):
        """编辑后 identity fingerprint 不变。"""
        tc = _tc(fingerprint="tc_original")
        mock_repo.get_test_case.return_value = tc

        edit_test_case(TC_ID, {"title": "新标题"})

        assert tc.fingerprint == "tc_original"  # 不变

    def test_content_hash_changed(self, mock_repo):
        """编辑后 content_hash 重算（变化）。"""
        tc = _tc(content_hash="tch_original")
        mock_repo.get_test_case.return_value = tc

        edit_test_case(TC_ID, {"title": "新标题"})

        assert tc.content_hash != "tch_original"  # 变了

    def test_provenance_human(self, mock_repo):
        """编辑后 TestCase.provenance = HUMAN。"""
        tc = _tc(provenance=Provenance.LLM)
        mock_repo.get_test_case.return_value = tc

        edit_test_case(TC_ID, {"title": "新标题"})

        assert tc.provenance == Provenance.HUMAN

    def test_validation_fail(self, mock_repo):
        """Validator FAIL → VALIDATION_FAILED。"""
        tc = _tc()
        mock_repo.get_test_case.return_value = tc

        result = edit_test_case(TC_ID, {"title": ""})  # 空标题

        assert result.success is True  # 编辑成功，但校验失败
        assert result.new_status == TestCaseStatus.VALIDATION_FAILED
        assert len(result.validation_errors) > 0

    def test_rejected_fields(self, mock_repo):
        """系统字段被拒绝，但白名单字段仍生效。"""
        tc = _tc()
        mock_repo.get_test_case.return_value = tc

        result = edit_test_case(TC_ID, {"title": "新标题", "id": "hack", "test_point_ids": []})

        assert result.success is True
        assert set(result.rejected_fields) == {"id", "test_point_ids"}
        assert tc.title == "新标题"

    def test_optimistic_lock_conflict(self, mock_repo):
        """乐观锁冲突 → 整体回滚，无 Revision 保存。"""
        tc = _tc()
        mock_repo.get_test_case.return_value = tc
        mock_repo.update_test_case_with_lock.side_effect = ConcurrentModificationError("冲突")

        result = edit_test_case(TC_ID, {"title": "新标题"})

        assert result.success is False
        assert any("冲突" in i or "刷新" in i for i in result.issues)
        mock_repo.save_test_case_revision.assert_not_called()  # 未保存 Revision

    def test_status_not_editable(self, mock_repo):
        """非 REVIEWED/VALIDATION_FAILED 状态 → 拒绝编辑。"""
        tc = _tc(status=TestCaseStatus.GENERATED)
        mock_repo.get_test_case.return_value = tc

        result = edit_test_case(TC_ID, {"title": "新标题"})

        assert result.success is False
        assert any("不允许编辑" in i for i in result.issues)

    def test_validation_failed_can_re_edit(self, mock_repo):
        """VALIDATION_FAILED 可重新编辑（恢复路径）。"""
        tc = _tc(status=TestCaseStatus.VALIDATION_FAILED)
        mock_repo.get_test_case.return_value = tc

        result = edit_test_case(TC_ID, {"title": "修复后的标题"})

        assert result.success is True
        assert result.new_status == TestCaseStatus.RE_REVIEW_REQUIRED

    def test_no_effective_updates(self, mock_repo):
        """无有效编辑字段 → 返回 issues。"""
        tc = _tc()
        mock_repo.get_test_case.return_value = tc

        result = edit_test_case(TC_ID, {"id": "hack"})  # 全部被拒绝

        assert result.success is False
        assert any("无有效编辑" in i for i in result.issues)


# ============================================================
# EditResult 数据结构
# ============================================================


class TestEditResult:
    def test_defaults(self):
        result = EditResult(test_case_id=TC_ID)
        assert result.test_case_id == TC_ID
        assert result.success is False
        assert result.revision_no == 0
        assert result.changed_fields == []
        assert result.new_status is None
        assert result.rejected_fields == []
        assert result.validation_errors == []
        assert result.issues == []
