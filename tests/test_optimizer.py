"""V2 Step 8 Dedup Optimizer 测试 - canonicalize pairs / 双边状态检查 / survivor 优先级 / 去重归档。

optimizer 是纯代码逻辑（依赖 repo 做持久化），故用 mock repo 做单元测试。
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from core.schemas import (
    Priority,
    Provenance,
    ReviewDimension,
    ReviewFinding,
    Severity,
    TargetType,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestStep,
)
from core.v2.optimizer import (
    OptimizerAction,
    OptimizerResult,
    canonicalize_pairs,
    check_pair_status,
    deduplicate_by_findings,
    determine_survivor,
    survivor_sort_key,
)

RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

# 26 字符 ULID（符合 Ulid 类型约束）
TC_A = "01ARZ3NDEKTSV4RRFFQ69G5TCA"
TC_B = "01ARZ3NDEKTSV4RRFFQ69G5TCB"
TC_C = "01ARZ3NDEKTSV4RRFFQ69G5TCC"
TC_D = "01ARZ3NDEKTSV4RRFFQ69G5TCD"
F1 = "01ARZ3NDEKTSV4RRFFQ69G5F01"
F2 = "01ARZ3NDEKTSV4RRFFQ69G5F02"
F_COV = "01ARZ3NDEKTSV4RRFFQ69G5FCV"
ITEM_1 = "01ARZ3NDEKTSV4RRFFQ69G5TM1"  # 排除 I/L/O/U


def _tc(
    tc_id: str,
    display_id: str,
    *,
    title: str = "测试用例",
    status: TestCaseStatus = TestCaseStatus.REVIEWED,
    provenance: Provenance = Provenance.LLM,
    priority: Priority = Priority.P1,
    created_at: datetime | None = None,
    steps_count: int = 2,
) -> TestCase:
    """构造测试用例（可控 id/display_id/status/provenance/priority/created_at）。"""
    steps = [TestStep(seq=i + 1, action=f"步骤{i + 1}", expected="ok") for i in range(steps_count)]
    return TestCase(
        id=tc_id,
        run_id=RUN_ID,
        display_id=display_id,
        module="登录",
        title=title,
        precondition="",
        steps=steps,
        expected="预期结果",
        priority=priority,
        type=TestCaseType.FUNCTIONAL,
        provenance=provenance,
        status=status,
        created_at=created_at or datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC),
    )


def _dup_finding(
    finding_id: str,
    target_id: str,
    counterpart_id: str,
    *,
    duplicate_level: str = "semantic",
    similarity: float = 0.90,
) -> ReviewFinding:
    """构造 duplication finding（模拟 Step 7 review_hard 产物）。"""
    return ReviewFinding(
        id=finding_id,
        dimension=ReviewDimension.DUPLICATION,
        severity=Severity.MINOR,
        target_type=TargetType.TESTCASE,
        target_id=target_id,
        issue=f"与用例 {counterpart_id} 重复",
        suggestion="建议去重",
        provenance=Provenance.VALIDATOR,
        auto_fixable=True,
        detail={
            "duplicate_level": duplicate_level,
            "similarity": similarity,
            "counterpart_id": counterpart_id,
            "counterpart_display_id": f"TC_{counterpart_id[-3:]}",
        },
    )


# ============================================================
# canonicalize_pairs（P0 坑防护）
# ============================================================


class TestCanonicalizePairs:
    def test_single_finding(self):
        """单条 finding → 唯一 pair。"""
        f = _dup_finding(F1, TC_B, TC_A)
        pairs = canonicalize_pairs([f])
        assert len(pairs) == 1
        assert (TC_A, TC_B) in pairs  # min_id, max_id
        assert pairs[(TC_A, TC_B)] == f

    def test_bidirectional_findings_dedup(self):
        """A↔B 两条 finding → 唯一 pair（防止两边都被归档）。"""
        f1 = _dup_finding(F1, TC_B, TC_A)  # B duplicate A
        f2 = _dup_finding(F2, TC_A, TC_B)  # A duplicate B
        pairs = canonicalize_pairs([f1, f2])
        assert len(pairs) == 1  # canonicalize 后只保留一条
        assert (TC_A, TC_B) in pairs
        assert pairs[(TC_A, TC_B)] == f1  # 保留第一个 finding

    def test_filter_non_duplication(self):
        """非 duplication 维度被过滤。"""
        f_cov = ReviewFinding(
            id=F_COV,
            dimension=ReviewDimension.COVERAGE,
            severity=Severity.MAJOR,
            target_type=TargetType.REQUIREMENT_ITEM,
            target_id=ITEM_1,
            issue="未覆盖",
            provenance=Provenance.VALIDATOR,
            auto_fixable=False,
        )
        f_dup = _dup_finding(F1, TC_B, TC_A)
        pairs = canonicalize_pairs([f_cov, f_dup])
        assert len(pairs) == 1
        assert (TC_A, TC_B) in pairs

    def test_filter_non_auto_fixable(self):
        """auto_fixable=False 被过滤。"""
        f = _dup_finding(F1, TC_B, TC_A)
        f.auto_fixable = False
        pairs = canonicalize_pairs([f])
        assert len(pairs) == 0

    def test_filter_missing_counterpart(self):
        """detail 缺 counterpart_id 被过滤。"""
        f = _dup_finding(F1, TC_B, TC_A)
        f.detail = {"duplicate_level": "exact"}  # 无 counterpart_id
        pairs = canonicalize_pairs([f])
        assert len(pairs) == 0

    def test_multiple_pairs(self):
        """多对重复 → 多个唯一 pair。"""
        f1 = _dup_finding(F1, TC_B, TC_A)
        f2 = _dup_finding(F2, TC_D, TC_C)
        pairs = canonicalize_pairs([f1, f2])
        assert len(pairs) == 2
        assert (TC_A, TC_B) in pairs
        assert (TC_C, TC_D) in pairs


# ============================================================
# check_pair_status（双边状态检查，幂等核心）
# ============================================================


class TestCheckPairStatus:
    def test_both_reviewed(self):
        """双方都是 REVIEWED → 应该处理。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.REVIEWED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.REVIEWED)
        should, reason = check_pair_status(a, b)
        assert should is True
        assert reason == ""

    def test_a_archived(self):
        """A 已归档 → skip(already_resolved)。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.ARCHIVED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.REVIEWED)
        should, reason = check_pair_status(a, b)
        assert should is False
        assert reason == "already_resolved"

    def test_b_archived(self):
        """B 已归档 → skip(already_resolved)。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.REVIEWED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.ARCHIVED)
        should, reason = check_pair_status(a, b)
        assert should is False
        assert reason == "already_resolved"

    def test_both_archived(self):
        """双方都已归档 → skip(already_resolved)。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.ARCHIVED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.ARCHIVED)
        should, reason = check_pair_status(a, b)
        assert should is False
        assert reason == "already_resolved"

    def test_a_not_reviewed(self):
        """A 非 REVIEWED（如 VALIDATED）→ skip(source_not_reviewed)。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.VALIDATED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.REVIEWED)
        should, reason = check_pair_status(a, b)
        assert should is False
        assert reason == "source_not_reviewed"

    def test_b_confirmed(self):
        """B 已 CONFIRMED → skip(source_not_reviewed)。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.REVIEWED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.CONFIRMED)
        should, reason = check_pair_status(a, b)
        assert should is False
        assert reason == "source_not_reviewed"


# ============================================================
# survivor_sort_key / determine_survivor（确定性可解释）
# ============================================================


class TestSurvivorPriority:
    def test_human_beats_llm(self):
        """HUMAN provenance 优先于 LLM（不覆盖人工意图）。"""
        a = _tc(TC_A, "TC_001", provenance=Provenance.HUMAN)
        b = _tc(TC_B, "TC_002", provenance=Provenance.LLM)
        survivor, loser = determine_survivor(a, b)
        assert survivor.id == TC_A
        assert loser.id == TC_B

    def test_optimizer_beats_llm(self):
        """OPTIMIZER provenance 优先于 LLM。"""
        a = _tc(TC_A, "TC_001", provenance=Provenance.OPTIMIZER)
        b = _tc(TC_B, "TC_002", provenance=Provenance.LLM)
        survivor, loser = determine_survivor(a, b)
        assert survivor.id == TC_A

    def test_llm_beats_strategy(self):
        """LLM provenance 优先于 STRATEGY。"""
        a = _tc(TC_A, "TC_001", provenance=Provenance.LLM)
        b = _tc(TC_B, "TC_002", provenance=Provenance.STRATEGY)
        survivor, loser = determine_survivor(a, b)
        assert survivor.id == TC_A

    def test_p0_beats_p1(self):
        """P0 priority 优先于 P1。"""
        a = _tc(TC_A, "TC_001", priority=Priority.P0)
        b = _tc(TC_B, "TC_002", priority=Priority.P1)
        survivor, loser = determine_survivor(a, b)
        assert survivor.id == TC_A

    def test_p1_beats_p2(self):
        """P1 priority 优先于 P2。"""
        a = _tc(TC_A, "TC_001", priority=Priority.P1)
        b = _tc(TC_B, "TC_002", priority=Priority.P2)
        survivor, loser = determine_survivor(a, b)
        assert survivor.id == TC_A

    def test_earlier_created_at_wins(self):
        """created_at 越早优先（生成序早的更稳定）。"""
        early = datetime(2026, 9, 16, 10, 0, 0, tzinfo=UTC)
        late = datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC)
        a = _tc(TC_A, "TC_001", created_at=early)
        b = _tc(TC_B, "TC_002", created_at=late)
        survivor, loser = determine_survivor(a, b)
        assert survivor.id == TC_A  # 早者优先

    def test_smaller_display_id_wins(self):
        """display_id 越小优先（最终 tie-break）。"""
        same_time = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
        a = _tc(TC_A, "TC_001", created_at=same_time)
        b = _tc(TC_B, "TC_002", created_at=same_time)
        survivor, loser = determine_survivor(a, b)
        assert survivor.id == TC_A  # TC_001 < TC_002

    def test_provenance_beats_priority(self):
        """provenance 权重高于 priority（HUMAN P3 > LLM P0）。"""
        a = _tc(TC_A, "TC_001", provenance=Provenance.HUMAN, priority=Priority.P3)
        b = _tc(TC_B, "TC_002", provenance=Provenance.LLM, priority=Priority.P0)
        survivor, loser = determine_survivor(a, b)
        assert survivor.id == TC_A  # HUMAN 优先，即使 P3

    def test_priority_beats_created_at(self):
        """priority 权重高于 created_at（P0 晚生成 > P1 早生成）。"""
        early = datetime(2026, 9, 16, 10, 0, 0, tzinfo=UTC)
        late = datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC)
        a = _tc(TC_A, "TC_001", priority=Priority.P0, created_at=late)
        b = _tc(TC_B, "TC_002", priority=Priority.P1, created_at=early)
        survivor, loser = determine_survivor(a, b)
        assert survivor.id == TC_A  # P0 优先，即使晚生成

    def test_sort_key_deterministic(self):
        """sort_key 确定性：同一用例多次调用结果一致。"""
        a = _tc(TC_A, "TC_001")
        key1 = survivor_sort_key(a)
        key2 = survivor_sort_key(a)
        assert key1 == key2


# ============================================================
# deduplicate_by_findings（端到端，mock repo）
# ============================================================


class TestDeduplicateByFindings:
    @pytest.fixture
    def mock_repo(self):
        """Mock repository（get_test_case / update_test_case_status）。"""
        with patch("core.v2.optimizer.repo") as mock:
            mock.get_test_case = MagicMock()
            mock.update_test_case_status = MagicMock()
            yield mock

    def test_no_findings(self, mock_repo):
        """无 duplication finding → 空结果。"""
        result = deduplicate_by_findings(RUN_ID, [])
        assert result.processed_findings == 0
        assert result.archived_cases == 0
        assert result.actions == []

    def test_exact_duplicate_archive(self, mock_repo):
        """EXACT 重复 → 归档 loser。"""
        a = _tc(TC_A, "TC_001", provenance=Provenance.LLM)
        b = _tc(TC_B, "TC_002", provenance=Provenance.LLM)
        mock_repo.get_test_case.side_effect = lambda tc_id: a if tc_id == TC_A else b

        f = _dup_finding(F1, TC_B, TC_A, duplicate_level="exact", similarity=1.0)
        result = deduplicate_by_findings(RUN_ID, [f])

        assert result.processed_findings == 1
        assert result.archived_cases == 1
        assert len(result.actions) == 1
        action = result.actions[0]
        assert action.case_a_id == TC_A
        assert action.case_b_id == TC_B
        assert action.kept_case_id == TC_A  # display_id 小者优先
        assert action.archived_case_id == TC_B
        assert action.reason == "exact_duplicate"
        assert action.similarity == 1.0
        # 验证归档调用
        mock_repo.update_test_case_status.assert_called_once_with(TC_B, "archived")

    def test_semantic_duplicate_archive(self, mock_repo):
        """SEMANTIC 重复 → 归档 loser，记录 similarity。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        mock_repo.get_test_case.side_effect = lambda tc_id: a if tc_id == TC_A else b

        f = _dup_finding(F1, TC_B, TC_A, duplicate_level="semantic", similarity=0.92)
        result = deduplicate_by_findings(RUN_ID, [f])

        assert result.archived_cases == 1
        action = result.actions[0]
        assert action.reason == "semantic_duplicate"
        assert action.similarity == 0.92

    def test_human_survives_llm(self, mock_repo):
        """HUMAN 用例与 LLM 重复 → HUMAN 保留，LLM 归档（不覆盖人工意图）。"""
        a = _tc(TC_A, "TC_001", provenance=Provenance.LLM)
        b = _tc(TC_B, "TC_002", provenance=Provenance.HUMAN)
        mock_repo.get_test_case.side_effect = lambda tc_id: a if tc_id == TC_A else b

        f = _dup_finding(F1, TC_B, TC_A)
        result = deduplicate_by_findings(RUN_ID, [f])

        action = result.actions[0]
        assert action.kept_case_id == TC_B  # HUMAN 保留
        assert action.archived_case_id == TC_A  # LLM 归档

    def test_skip_already_archived(self, mock_repo):
        """任一方已归档 → skip(already_resolved)。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.ARCHIVED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.REVIEWED)
        mock_repo.get_test_case.side_effect = lambda tc_id: a if tc_id == TC_A else b

        f = _dup_finding(F1, TC_B, TC_A)
        result = deduplicate_by_findings(RUN_ID, [f])

        assert result.archived_cases == 0
        assert result.skipped_findings == 1
        assert result.skip_reasons.get("already_resolved") == 1
        mock_repo.update_test_case_status.assert_not_called()

    def test_skip_not_reviewed(self, mock_repo):
        """任一方非 REVIEWED → skip(source_not_reviewed)。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.VALIDATED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.REVIEWED)
        mock_repo.get_test_case.side_effect = lambda tc_id: a if tc_id == TC_A else b

        f = _dup_finding(F1, TC_B, TC_A)
        result = deduplicate_by_findings(RUN_ID, [f])

        assert result.archived_cases == 0
        assert result.skip_reasons.get("source_not_reviewed") == 1

    def test_skip_case_not_found(self, mock_repo):
        """用例不存在 → skip(case_not_found)。"""
        mock_repo.get_test_case.return_value = None

        f = _dup_finding(F1, TC_B, TC_A)
        result = deduplicate_by_findings(RUN_ID, [f])

        assert result.archived_cases == 0
        assert result.skip_reasons.get("case_not_found") == 1

    def test_bidirectional_findings_single_archive(self, mock_repo):
        """A↔B 两条 finding → 只归档一次（canonicalize 防护）。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        mock_repo.get_test_case.side_effect = lambda tc_id: a if tc_id == TC_A else b

        f1 = _dup_finding(F1, TC_B, TC_A)  # B duplicate A
        f2 = _dup_finding(F2, TC_A, TC_B)  # A duplicate B
        result = deduplicate_by_findings(RUN_ID, [f1, f2])

        assert result.processed_findings == 1  # canonicalize 后只有一对
        assert result.archived_cases == 1  # 只归档一次
        assert mock_repo.update_test_case_status.call_count == 1

    def test_idempotent_multiple_runs(self, mock_repo):
        """幂等：多次运行结果一致（已归档的不再动）。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")

        # 第一次运行：B 被归档
        mock_repo.get_test_case.side_effect = lambda tc_id: a if tc_id == TC_A else b
        f = _dup_finding(F1, TC_B, TC_A)
        result1 = deduplicate_by_findings(RUN_ID, [f])
        assert result1.archived_cases == 1

        # 模拟 B 已被归档
        b.status = TestCaseStatus.ARCHIVED

        # 第二次运行：skip(already_resolved)
        result2 = deduplicate_by_findings(RUN_ID, [f])
        assert result2.archived_cases == 0
        assert result2.skip_reasons.get("already_resolved") == 1

    def test_multiple_pairs(self, mock_repo):
        """多对重复 → 各自独立处理。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        c = _tc(TC_C, "TC_003")
        d = _tc(TC_D, "TC_004")

        def get_tc(tc_id):
            return {TC_A: a, TC_B: b, TC_C: c, TC_D: d}.get(tc_id)

        mock_repo.get_test_case.side_effect = get_tc

        f1 = _dup_finding(F1, TC_B, TC_A)
        f2 = _dup_finding(F2, TC_D, TC_C)
        result = deduplicate_by_findings(RUN_ID, [f1, f2])

        assert result.processed_findings == 2
        assert result.archived_cases == 2
        assert len(result.actions) == 2

    def test_content_not_modified(self, mock_repo):
        """去重只改 status，不改内容（title/steps/expected/priority 不变）。"""
        a = _tc(TC_A, "TC_001", title="原标题", steps_count=3)
        b = _tc(TC_B, "TC_002", title="原标题", steps_count=3)
        mock_repo.get_test_case.side_effect = lambda tc_id: a if tc_id == TC_A else b

        # 记录 b 的原始内容
        original_title = b.title
        original_steps_count = len(b.steps)
        original_expected = b.expected
        original_priority = b.priority

        f = _dup_finding(F1, TC_B, TC_A)
        deduplicate_by_findings(RUN_ID, [f])

        # 验证 b 的内容未变（只有 status 变）
        assert b.title == original_title
        assert len(b.steps) == original_steps_count
        assert b.expected == original_expected
        assert b.priority == original_priority
        assert b.status == TestCaseStatus.ARCHIVED  # 只有 status 变


# ============================================================
# OptimizerAction / OptimizerResult 数据结构
# ============================================================


class TestDataStructures:
    def test_optimizer_action_fields(self):
        """OptimizerAction 包含完整审计字段。"""
        action = OptimizerAction(
            finding_id=F1,
            case_a_id=TC_A,
            case_b_id=TC_B,
            kept_case_id=TC_A,
            archived_case_id=TC_B,
            action="archive",
            reason="exact_duplicate",
            similarity=1.0,
        )
        assert action.finding_id == F1
        assert action.case_a_id == TC_A
        assert action.case_b_id == TC_B
        assert action.kept_case_id == TC_A
        assert action.archived_case_id == TC_B
        assert action.action == "archive"
        assert action.reason == "exact_duplicate"
        assert action.similarity == 1.0

    def test_optimizer_result_defaults(self):
        """OptimizerResult 默认值正确。"""
        result = OptimizerResult(run_id=RUN_ID)
        assert result.run_id == RUN_ID
        assert result.processed_findings == 0
        assert result.archived_cases == 0
        assert result.skipped_findings == 0
        assert result.actions == []
        assert result.skip_reasons == {}
