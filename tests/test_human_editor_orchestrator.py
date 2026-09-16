"""V2 Step 9 Human Editor Orchestrator 测试 - 编排：edit + re_review 批量接口。"""

from unittest.mock import MagicMock, patch

import pytest

from core.schemas import ReviewTriggerType
from core.v2.human_editor import EditResult
from core.v2.human_editor_orchestrator import ReReviewResult, edit_case, re_review_test_cases
from core.v2.review_orchestrator import ReviewResult

RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
TC_ID = "01ARZ3NDEKTSV4RRFFQ69G5TCA"


class TestEditCase:
    def test_wraps_edit_test_case(self):
        """edit_case 包装 human_editor.edit_test_case。"""
        with patch("core.v2.human_editor_orchestrator.edit_test_case") as mock_edit:
            mock_edit.return_value = EditResult(test_case_id=TC_ID, success=True)
            result = edit_case(TC_ID, {"title": "新标题"})
            mock_edit.assert_called_once_with(TC_ID, {"title": "新标题"}, expected_updated_at=None)
            assert result.success is True

    def test_passes_expected_updated_at(self):
        """expected_updated_at 透传。"""
        with patch("core.v2.human_editor_orchestrator.edit_test_case") as mock_edit:
            mock_edit.return_value = EditResult(test_case_id=TC_ID, success=True)
            edit_case(TC_ID, {"title": "新"}, expected_updated_at="2026-09-16T12:00:00")
            call_kwargs = mock_edit.call_args[1]
            assert call_kwargs["expected_updated_at"] == "2026-09-16T12:00:00"


class TestReReviewTestCases:
    @pytest.fixture
    def mock_review(self):
        with patch("core.v2.human_editor_orchestrator.review_test_cases") as mock:
            mock.return_value = ReviewResult(run_id=RUN_ID, reviewed_count=3)
            yield mock

    def test_calls_review_with_after_human_edit(self, mock_review):
        """re_review 调用 review_test_cases(trigger=AFTER_HUMAN_EDIT)。"""
        result = re_review_test_cases(MagicMock(), run_id=RUN_ID)
        mock_review.assert_called_once()
        call_kwargs = mock_review.call_args[1]
        assert call_kwargs["run_id"] == RUN_ID
        assert call_kwargs["trigger_type"] == ReviewTriggerType.AFTER_HUMAN_EDIT
        assert result.review_result.reviewed_count == 3

    def test_records_requested_case_ids(self, mock_review):
        """test_case_ids 记录到结果（第一版仅审计，内部统一 Review）。"""
        result = re_review_test_cases(MagicMock(), run_id=RUN_ID, test_case_ids=[TC_ID, "TC_B"])
        assert result.requested_case_ids == [TC_ID, "TC_B"]
        # 内部仍按 run_id 统一 Review（不传 test_case_ids 给 review_test_cases）
        call_kwargs = mock_review.call_args[1]
        assert "test_case_ids" not in call_kwargs

    def test_empty_case_ids(self, mock_review):
        """test_case_ids=None → 空列表。"""
        result = re_review_test_cases(MagicMock(), run_id=RUN_ID)
        assert result.requested_case_ids == []

    def test_issues_propagated(self, mock_review):
        """review_result.issues 传播到 ReReviewResult。"""
        mock_review.return_value = ReviewResult(run_id=RUN_ID, reviewed_count=0, issues=["无可用用例"])
        result = re_review_test_cases(MagicMock(), run_id=RUN_ID)
        assert result.issues == ["无可用用例"]


class TestReReviewResult:
    def test_defaults(self):
        result = ReReviewResult(run_id=RUN_ID)
        assert result.run_id == RUN_ID
        assert result.review_result is None
        assert result.requested_case_ids == []
        assert result.issues == []
