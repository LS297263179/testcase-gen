"""V2 Step 8 Optimizer Orchestrator 测试 - 编排：拉 report → 执行去重 → 有条件触发重评审。

orchestrator 依赖 repo + review_orchestrator，故用 mock 做单元测试。
"""

from unittest.mock import MagicMock, patch

import pytest

from core.schemas import (
    Priority,
    Provenance,
    ReviewDimension,
    ReviewFinding,
    ReviewReport,
    ReviewScores,
    ReviewTriggerType,
    RunStatus,
    Severity,
    TargetType,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestStep,
)
from core.v2.optimizer import OptimizerAction, OptimizerResult
from core.v2.optimizer_orchestrator import OptimizeResult, optimize_duplicates
from core.v2.review_orchestrator import ReviewResult

RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

# 26 字符 ULID
TC_A = "01ARZ3NDEKTSV4RRFFQ69G5TCA"
TC_B = "01ARZ3NDEKTSV4RRFFQ69G5TCB"
F1 = "01ARZ3NDEKTSV4RRFFQ69G5F01"
RPT_1 = "01ARZ3NDEKTSV4RRFFQ69G5RP1"
USER_1 = "01ARZ3NDEKTSV4RRFFQ69G5SR1"  # 排除 I/L/O/U
DOC_1 = "01ARZ3NDEKTSV4RRFFQ69G5DC1"
VER_1 = "01ARZ3NDEKTSV4RRFFQ69G5VR1"
CFG_1 = "01ARZ3NDEKTSV4RRFFQ69G5CF1"


def _tc(tc_id: str, display_id: str, *, status=TestCaseStatus.REVIEWED) -> TestCase:
    steps = [TestStep(seq=1, action="步骤1", expected="ok")]
    return TestCase(
        id=tc_id,
        run_id=RUN_ID,
        display_id=display_id,
        module="登录",
        title="测试用例",
        precondition="",
        steps=steps,
        expected="预期结果",
        priority=Priority.P1,
        type=TestCaseType.FUNCTIONAL,
        status=status,
    )


def _dup_finding(target_id: str, counterpart_id: str) -> ReviewFinding:
    return ReviewFinding(
        id=F1,
        dimension=ReviewDimension.DUPLICATION,
        severity=Severity.MINOR,
        target_type=TargetType.TESTCASE,
        target_id=target_id,
        issue="重复",
        provenance=Provenance.VALIDATOR,
        auto_fixable=True,
        detail={"duplicate_level": "semantic", "similarity": 0.90, "counterpart_id": counterpart_id},
    )


def _review_report(findings: list[ReviewFinding], revision: int = 1) -> ReviewReport:
    return ReviewReport(
        id=RPT_1,
        run_id=RUN_ID,
        revision=revision,
        trigger_type=ReviewTriggerType.INITIAL,
        scores=ReviewScores(
            coverage=80.0,
            accuracy=75.0,
            executability=85.0,
            consistency=90.0,
            missing_risk=70.0,
            duplication=70.0,
        ),
        overall_score=78.0,
        findings=findings,
    )


def _run(status=RunStatus.DONE):
    from core.schemas import Run

    return Run(
        id=RUN_ID,
        user_id=USER_1,
        doc_id=DOC_1,
        requirement_version_id=VER_1,
        generation_config_id=CFG_1,
        status=status,
    )


# ============================================================
# optimize_duplicates 编排测试
# ============================================================


class TestOptimizeDuplicates:
    @pytest.fixture
    def mock_repo(self):
        """Mock repository。"""
        with patch("core.v2.optimizer_orchestrator.repo") as mock:
            mock.get_latest_review_report = MagicMock()
            mock.get_test_case = MagicMock()
            mock.update_test_case_status = MagicMock()
            mock.get_run = MagicMock()
            mock.save_run = MagicMock()
            yield mock

    @pytest.fixture
    def mock_review(self):
        """Mock review_orchestrator.review_test_cases。"""
        with patch("core.v2.optimizer_orchestrator.review_test_cases") as mock:
            mock.return_value = ReviewResult(run_id=RUN_ID, reviewed_count=5)
            yield mock

    @pytest.fixture
    def mock_dedup(self):
        """Mock optimizer.deduplicate_by_findings。"""
        with patch("core.v2.optimizer_orchestrator.deduplicate_by_findings") as mock:
            yield mock

    def test_no_review_report(self, mock_repo):
        """无 ReviewReport → 返回 issues，不执行去重。"""
        mock_repo.get_latest_review_report.return_value = None

        result = optimize_duplicates(MagicMock(), run_id=RUN_ID)

        assert result.run_id == RUN_ID
        assert result.optimizer_result is None
        assert result.review_result is None
        assert len(result.issues) == 1
        assert "无 ReviewReport" in result.issues[0]

    def test_archive_triggers_review(self, mock_repo, mock_review, mock_dedup):
        """有归档 → 触发 AFTER_OPTIMIZER 重评审。"""
        # 准备 ReviewReport
        report = _review_report([_dup_finding(TC_B, TC_A)])
        mock_repo.get_latest_review_report.return_value = report
        mock_repo.get_run.return_value = _run()

        # 模拟去重结果：归档 1 条
        dedup_result = OptimizerResult(
            run_id=RUN_ID,
            processed_findings=1,
            archived_cases=1,
            actions=[
                OptimizerAction(
                    finding_id=F1,
                    case_a_id=TC_A,
                    case_b_id=TC_B,
                    kept_case_id=TC_A,
                    archived_case_id=TC_B,
                    reason="semantic_duplicate",
                )
            ],
        )
        mock_dedup.return_value = dedup_result

        result = optimize_duplicates(MagicMock(), run_id=RUN_ID)

        # 验证触发了重评审
        mock_review.assert_called_once()
        call_kwargs = mock_review.call_args[1]
        assert call_kwargs["run_id"] == RUN_ID
        assert call_kwargs["trigger_type"] == ReviewTriggerType.AFTER_OPTIMIZER

        # 验证结果
        assert result.optimizer_result == dedup_result
        assert result.review_result is not None
        assert result.review_result.reviewed_count == 5

    def test_no_archive_no_review(self, mock_repo, mock_review, mock_dedup):
        """无归档 → 不触发重评审。"""
        report = _review_report([_dup_finding(TC_B, TC_A)])
        mock_repo.get_latest_review_report.return_value = report
        mock_repo.get_run.return_value = _run()

        # 模拟去重结果：无归档（全部 skip）
        dedup_result = OptimizerResult(
            run_id=RUN_ID,
            processed_findings=1,
            archived_cases=0,
            skipped_findings=1,
            skip_reasons={"already_resolved": 1},
        )
        mock_dedup.return_value = dedup_result

        result = optimize_duplicates(MagicMock(), run_id=RUN_ID)

        # 验证未触发重评审
        mock_review.assert_not_called()
        assert result.review_result is None
        assert result.optimizer_result.archived_cases == 0

    def test_run_status_done(self, mock_repo, mock_review, mock_dedup):
        """Run 状态机 → DONE。"""
        report = _review_report([])
        mock_repo.get_latest_review_report.return_value = report
        run = _run(status=RunStatus.REVIEWING)
        mock_repo.get_run.return_value = run
        mock_dedup.return_value = OptimizerResult(run_id=RUN_ID)

        optimize_duplicates(MagicMock(), run_id=RUN_ID)

        # 验证 Run 状态变为 DONE
        assert run.status == RunStatus.DONE
        mock_repo.save_run.assert_called_once_with(run)

    def test_run_not_found(self, mock_repo, mock_dedup):
        """Run 不存在 → 不报错，正常返回。"""
        report = _review_report([])
        mock_repo.get_latest_review_report.return_value = report
        mock_repo.get_run.return_value = None
        mock_dedup.return_value = OptimizerResult(run_id=RUN_ID)

        result = optimize_duplicates(MagicMock(), run_id=RUN_ID)

        assert result.run_id == RUN_ID
        mock_repo.save_run.assert_not_called()


# ============================================================
# OptimizeResult 数据结构
# ============================================================


class TestOptimizeResult:
    def test_defaults(self):
        """OptimizeResult 默认值正确。"""
        result = OptimizeResult(run_id=RUN_ID)
        assert result.run_id == RUN_ID
        assert result.optimizer_result is None
        assert result.review_result is None
        assert result.issues == []

    def test_with_all_fields(self):
        """OptimizeResult 包含所有字段。"""
        opt_result = OptimizerResult(run_id=RUN_ID, archived_cases=1)
        rev_result = ReviewResult(run_id=RUN_ID, reviewed_count=5)
        result = OptimizeResult(
            run_id=RUN_ID,
            optimizer_result=opt_result,
            review_result=rev_result,
            issues=["test issue"],
        )
        assert result.optimizer_result == opt_result
        assert result.review_result == rev_result
        assert result.issues == ["test issue"]
