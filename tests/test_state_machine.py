"""V2 测试用例生命周期状态机测试（P0-6）。

对应 docs/v2/step1-data-model.md §3.1 ALLOWED_TRANSITIONS + §13 验收标准。
"""

import pytest

from core.schemas import TestCase, TestCaseStatus, TestCaseType, new_ulid
from core.schemas.common import ALLOWED_TRANSITIONS, can_transition


def _make_case(status: TestCaseStatus = TestCaseStatus.GENERATED) -> TestCase:
    """构造一条指定状态的用例"""
    return TestCase(
        run_id=new_ulid(),
        display_id="TC_001",
        module="m",
        title="t",
        expected="e",
        type=TestCaseType.FUNCTIONAL,
        status=status,
    )


class TestTransitions:
    def test_happy_path(self):
        """正常链路：GENERATED→VALIDATED→REVIEWED→CONFIRMED→ARCHIVED"""
        tc = _make_case()
        for target in (
            TestCaseStatus.VALIDATED,
            TestCaseStatus.REVIEWED,
            TestCaseStatus.CONFIRMED,
            TestCaseStatus.ARCHIVED,
        ):
            tc.transition_to(target)
            assert tc.status == target

    def test_validation_failed_then_regenerate(self):
        tc = _make_case()
        tc.transition_to(TestCaseStatus.VALIDATION_FAILED)
        tc.transition_to(TestCaseStatus.GENERATED)  # 修复后重生成
        assert tc.status == TestCaseStatus.GENERATED

    def test_human_edit_forces_re_review(self):
        """核心场景：已评审用例被人工编辑 → 必须重审"""
        tc = _make_case(TestCaseStatus.REVIEWED)
        tc.transition_to(TestCaseStatus.EDITED)
        tc.transition_to(TestCaseStatus.RE_REVIEW_REQUIRED)
        tc.transition_to(TestCaseStatus.REVIEWED)
        assert tc.status == TestCaseStatus.REVIEWED

    def test_confirmed_then_edited_requires_re_review(self):
        """已确认用例被改 → EDITED → RE_REVIEW_REQUIRED（不是原 AI Review 结果了）"""
        tc = _make_case(TestCaseStatus.CONFIRMED)
        tc.transition_to(TestCaseStatus.EDITED)
        tc.transition_to(TestCaseStatus.RE_REVIEW_REQUIRED)
        assert tc.status == TestCaseStatus.RE_REVIEW_REQUIRED

    def test_illegal_skip_raises(self):
        """GENERATED 不能直接跳到 CONFIRMED"""
        tc = _make_case()
        with pytest.raises(ValueError, match="非法状态转移"):
            tc.transition_to(TestCaseStatus.CONFIRMED)

    def test_illegal_from_archived_raises(self):
        """ARCHIVED 是终态，不能再转移"""
        tc = _make_case(TestCaseStatus.ARCHIVED)
        with pytest.raises(ValueError):
            tc.transition_to(TestCaseStatus.GENERATED)

    def test_illegal_validated_to_confirmed_raises(self):
        """VALIDATED 必须先 REVIEWED 才能 CONFIRMED"""
        tc = _make_case(TestCaseStatus.VALIDATED)
        with pytest.raises(ValueError):
            tc.transition_to(TestCaseStatus.CONFIRMED)

    def test_status_unchanged_after_illegal(self):
        """非法转移不应改变当前状态"""
        tc = _make_case()
        with pytest.raises(ValueError):
            tc.transition_to(TestCaseStatus.CONFIRMED)
        assert tc.status == TestCaseStatus.GENERATED


class TestTransitionTable:
    def test_archived_is_terminal(self):
        assert ALLOWED_TRANSITIONS[TestCaseStatus.ARCHIVED] == set()

    def test_can_transition_helper(self):
        assert can_transition(TestCaseStatus.GENERATED, TestCaseStatus.VALIDATED) is True
        assert can_transition(TestCaseStatus.GENERATED, TestCaseStatus.CONFIRMED) is False

    def test_all_statuses_have_entry(self):
        """每个状态都应在转移表中有定义（含终态空集）"""
        for status in TestCaseStatus:
            assert status in ALLOWED_TRANSITIONS
