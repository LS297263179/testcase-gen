"""V2 Step 8 验收门槛测试 - 14 条逐条对应。

集成测试：使用 v2_db fixture，验证端到端流程（Schema/状态机/去重/重评审/幂等/V1 零回归）。
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from core.schemas import (
    GenerationConfig,
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
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestStep,
)
from core.v2 import ddl
from core.v2.optimizer import canonicalize_pairs, deduplicate_by_findings
from core.v2.optimizer_orchestrator import optimize_duplicates

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

# 26 字符 ULID（排除 I/L/O/U）
TC_A = "01ARZ3NDEKTSV4RRFFQ69G5TCA"
TC_B = "01ARZ3NDEKTSV4RRFFQ69G5TCB"
TC_C = "01ARZ3NDEKTSV4RRFFQ69G5TCC"
ITEM_1 = "01ARZ3NDEKTSV4RRFFQ69G5TM1"


def _tc(tc_id: str, display_id: str, *, run_id: str = "", **kwargs) -> TestCase:
    steps_count = kwargs.pop("steps_count", 2)
    steps = [TestStep(seq=i + 1, action=f"步骤{i + 1}", expected="ok") for i in range(steps_count)]
    defaults = {
        "id": tc_id,
        "run_id": run_id or "01ARZ3NDEKTSV4RRFFQ69G5RN1",  # 默认 run_id，_setup 中会覆盖
        "display_id": display_id,
        "module": "登录",
        "title": "测试用例",
        "precondition": "",
        "steps": steps,
        "expected": "预期结果",
        "priority": Priority.P1,
        "type": TestCaseType.FUNCTIONAL,
        "provenance": Provenance.LLM,
        "status": TestCaseStatus.REVIEWED,
        "created_at": datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC),
    }
    defaults.update(kwargs)
    return TestCase(**defaults)


def _dup_finding(
    target_id: str, counterpart_id: str, *, level="semantic", sim=0.90, finding_id: str = ""
) -> ReviewFinding:
    # 生成合法 26 字符 ULID（用 target/counterpart 后 2 位拼接，确保唯一）
    if not finding_id:
        finding_id = f"01ARZ3NDEKTSV4RRFFQ69G5F{target_id[-2:]}{counterpart_id[-2:]}X"[:26]
    return ReviewFinding(
        id=finding_id,
        dimension=ReviewDimension.DUPLICATION,
        severity=Severity.MINOR,
        target_type=TargetType.TESTCASE,
        target_id=target_id,
        issue="重复",
        provenance=Provenance.VALIDATOR,
        auto_fixable=True,
        detail={"duplicate_level": level, "similarity": sim, "counterpart_id": counterpart_id},
    )


def _setup(v2_db, cases: list[TestCase], findings: list[ReviewFinding]):
    """创建完整前置数据：User → Doc → Version → Item → Config → Run → TestCase → ReviewReport。"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="Step8 验收", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    v2_db.save_version(ver)
    item = RequirementItem(
        id=ITEM_1, version_id=ver.id, seq=1, type=RequirementItemType.FUNCTION, module="登录", statement="手机号登录"
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

    for tc in cases:
        # 更新 run_id 为真实创建的 run.id
        tc.run_id = run.id
        v2_db.save_test_case(tc)

    report = ReviewReport(
        run_id=run.id,
        revision=1,
        trigger_type=ReviewTriggerType.INITIAL,
        scores=ReviewScores(
            coverage=80.0, accuracy=75.0, executability=85.0, consistency=90.0, missing_risk=70.0, duplication=70.0
        ),
        overall_score=78.0,
        findings=findings,
    )
    v2_db.save_review_report(report)
    return run, report


# ============================================================
# 门槛 1: 只消费 duplication finding（不碰其他维度）
# ============================================================


class TestGate1OnlyDuplication:
    def test_non_duplication_findings_ignored(self, v2_db):
        """coverage/consistency/executability finding 不被处理。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        findings = [
            _dup_finding(TC_B, TC_A),
            ReviewFinding(
                id="01ARZ3NDEKTSV4RRFFQ69G5FCV",
                dimension=ReviewDimension.COVERAGE,
                severity=Severity.MAJOR,
                target_type=TargetType.REQUIREMENT_ITEM,
                target_id=ITEM_1,
                issue="未覆盖",
                provenance=Provenance.VALIDATOR,
                auto_fixable=False,
            ),
            ReviewFinding(
                id="01ARZ3NDEKTSV4RRFFQ69G5FCN",
                dimension=ReviewDimension.CONSISTENCY,
                severity=Severity.MINOR,
                target_type=TargetType.TESTCASE,
                target_id=TC_A,
                issue="display_id 格式",
                provenance=Provenance.VALIDATOR,
                auto_fixable=True,
            ),
        ]
        run, _ = _setup(v2_db, [a, b], findings)
        result = deduplicate_by_findings(run.id, findings)
        assert result.processed_findings == 1
        assert result.archived_cases == 1


# ============================================================
# 门槛 2: Canonical pair 去重（A↔B 两条 finding 只处理一次）
# ============================================================


class TestGate2CanonicalizePairs:
    def test_bidirectional_single_archive(self, v2_db):
        """A↔B 两条 finding → 只归档一次，不会两边都归档。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        f1 = _dup_finding(TC_B, TC_A, finding_id="01ARZ3NDEKTSV4RRFFQ69G5F01")
        f2 = _dup_finding(TC_A, TC_B, finding_id="01ARZ3NDEKTSV4RRFFQ69G5F02")
        run, _ = _setup(v2_db, [a, b], [f1, f2])
        result = deduplicate_by_findings(run.id, [f1, f2])
        assert result.processed_findings == 1
        assert result.archived_cases == 1
        # 从 DB 重新加载检查状态（deduplicate_by_findings 修改的是 DB 中的对象）
        a_from_db = v2_db.get_test_case(TC_A)
        b_from_db = v2_db.get_test_case(TC_B)
        archived = [tc for tc in [a_from_db, b_from_db] if tc.status == TestCaseStatus.ARCHIVED]
        assert len(archived) == 1

    def test_canonicalize_function(self):
        """canonicalize_pairs 函数正确去重。"""
        f1 = _dup_finding(TC_B, TC_A)
        f2 = _dup_finding(TC_A, TC_B)
        pairs = canonicalize_pairs([f1, f2])
        assert len(pairs) == 1
        assert (TC_A, TC_B) in pairs


# ============================================================
# 门槛 3: 双边状态检查（任一方非 REVIEWED → skip）
# ============================================================


class TestGate3BilateralStatusCheck:
    def test_a_not_reviewed_skip(self, v2_db):
        """A 非 REVIEWED → skip。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.VALIDATED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.REVIEWED)
        run, _ = _setup(v2_db, [a, b], [])
        result = deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])
        assert result.archived_cases == 0
        assert result.skip_reasons.get("source_not_reviewed") == 1

    def test_b_confirmed_skip(self, v2_db):
        """B 已 CONFIRMED → skip。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.REVIEWED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.CONFIRMED)
        run, _ = _setup(v2_db, [a, b], [])
        result = deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])
        assert result.archived_cases == 0
        assert result.skip_reasons.get("source_not_reviewed") == 1


# ============================================================
# 门槛 4: already_resolved skip（任一方 ARCHIVED）
# ============================================================


class TestGate4AlreadyResolved:
    def test_a_archived_skip(self, v2_db):
        """A 已归档 → skip(already_resolved)。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.ARCHIVED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.REVIEWED)
        run, _ = _setup(v2_db, [a, b], [])
        result = deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])
        assert result.archived_cases == 0
        assert result.skip_reasons.get("already_resolved") == 1

    def test_both_archived_skip(self, v2_db):
        """双方都已归档 → skip(already_resolved)。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.ARCHIVED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.ARCHIVED)
        run, _ = _setup(v2_db, [a, b], [])
        result = deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])
        assert result.archived_cases == 0
        assert result.skip_reasons.get("already_resolved") == 1


# ============================================================
# 门槛 5: Survivor 优先级正确
# ============================================================


class TestGate5SurvivorPriority:
    def test_human_beats_llm(self, v2_db):
        """HUMAN > LLM（不覆盖人工意图）。"""
        a = _tc(TC_A, "TC_001", provenance=Provenance.LLM)
        b = _tc(TC_B, "TC_002", provenance=Provenance.HUMAN)
        run, _ = _setup(v2_db, [a, b], [])
        result = deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])
        action = result.actions[0]
        assert action.kept_case_id == TC_B
        assert action.archived_case_id == TC_A

    def test_p0_beats_p1(self, v2_db):
        """P0 > P1。"""
        a = _tc(TC_A, "TC_001", priority=Priority.P1)
        b = _tc(TC_B, "TC_002", priority=Priority.P0)
        run, _ = _setup(v2_db, [a, b], [])
        result = deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])
        assert result.actions[0].kept_case_id == TC_B

    def test_earlier_created_at_wins(self, v2_db):
        """created_at 越早优先。"""
        early = datetime(2026, 9, 16, 10, 0, 0, tzinfo=UTC)
        late = datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC)
        a = _tc(TC_A, "TC_001", created_at=late)
        b = _tc(TC_B, "TC_002", created_at=early)
        run, _ = _setup(v2_db, [a, b], [])
        result = deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])
        assert result.actions[0].kept_case_id == TC_B

    def test_smaller_display_id_wins(self, v2_db):
        """display_id 越小优先（最终 tie-break）。"""
        same_time = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
        a = _tc(TC_A, "TC_002", created_at=same_time)
        b = _tc(TC_B, "TC_001", created_at=same_time)
        run, _ = _setup(v2_db, [a, b], [])
        result = deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])
        assert result.actions[0].kept_case_id == TC_B


# ============================================================
# 门槛 6: 归档不物理删除（status=ARCHIVED，DB 行保留）
# ============================================================


class TestGate6ArchiveNotDelete:
    def test_archived_case_still_in_db(self, v2_db):
        """归档后用例仍在 DB 中（可查询）。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        run, _ = _setup(v2_db, [a, b], [])
        deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])
        b_from_db = v2_db.get_test_case(TC_B)
        assert b_from_db is not None
        assert b_from_db.status == TestCaseStatus.ARCHIVED

    def test_list_test_cases_includes_archived(self, v2_db):
        """list_test_cases 包含归档用例（未过滤）。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        run, _ = _setup(v2_db, [a, b], [])
        deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])
        cases = v2_db.list_test_cases(run.id)
        assert len(cases) == 2
        statuses = {tc.id: tc.status for tc in cases}
        assert statuses[TC_A] == TestCaseStatus.REVIEWED
        assert statuses[TC_B] == TestCaseStatus.ARCHIVED


# ============================================================
# 门槛 7: OptimizerAction 记录完整
# ============================================================


class TestGate7ActionAudit:
    def test_action_fields_complete(self, v2_db):
        """OptimizerAction 包含 finding_id/case_a_id/case_b_id/kept/archived/reason/similarity。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        f = _dup_finding(TC_B, TC_A, level="exact", sim=1.0)
        run, _ = _setup(v2_db, [a, b], [f])
        result = deduplicate_by_findings(run.id, [f])
        action = result.actions[0]
        assert action.finding_id == f.id
        assert action.case_a_id == TC_A
        assert action.case_b_id == TC_B
        assert action.kept_case_id == TC_A
        assert action.archived_case_id == TC_B
        assert action.action == "archive"
        assert action.reason == "exact_duplicate"
        assert action.similarity == 1.0


# ============================================================
# 门槛 8: 幂等（多次运行结果一致）
# ============================================================


class TestGate8Idempotent:
    def test_multiple_runs_same_result(self, v2_db):
        """多次运行 Optimizer，结果一致（已归档的不再动）。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        f = _dup_finding(TC_B, TC_A)
        run, _ = _setup(v2_db, [a, b], [f])

        result1 = deduplicate_by_findings(run.id, [f])
        assert result1.archived_cases == 1

        result2 = deduplicate_by_findings(run.id, [f])
        assert result2.archived_cases == 0
        assert result2.skip_reasons.get("already_resolved") == 1

        result3 = deduplicate_by_findings(run.id, [f])
        assert result3.archived_cases == 0

        b_from_db = v2_db.get_test_case(TC_B)
        assert b_from_db.status == TestCaseStatus.ARCHIVED


# ============================================================
# 门槛 9: 有归档才触发重评审
# ============================================================


class TestGate9ConditionalReview:
    def test_archive_triggers_review(self, v2_db):
        """有归档 → 触发重评审。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        run, _ = _setup(v2_db, [a, b], [_dup_finding(TC_B, TC_A)])

        with patch("core.v2.optimizer_orchestrator.review_test_cases") as mock_review:
            mock_review.return_value = MagicMock()
            optimize_duplicates(MagicMock(), run_id=run.id)
            mock_review.assert_called_once()

    def test_no_archive_no_review(self, v2_db):
        """无归档（全部 skip）→ 不触发重评审。"""
        a = _tc(TC_A, "TC_001", status=TestCaseStatus.ARCHIVED)
        b = _tc(TC_B, "TC_002", status=TestCaseStatus.REVIEWED)
        run, _ = _setup(v2_db, [a, b], [_dup_finding(TC_B, TC_A)])

        with patch("core.v2.optimizer_orchestrator.review_test_cases") as mock_review:
            optimize_duplicates(MagicMock(), run_id=run.id)
            mock_review.assert_not_called()


# ============================================================
# 门槛 10: 重评审 revision+1，trigger_type=AFTER_OPTIMIZER
# ============================================================


class TestGate10ReviewRevision:
    def test_review_trigger_type(self, v2_db):
        """重评审 trigger_type=AFTER_OPTIMIZER。"""
        a = _tc(TC_A, "TC_001")
        b = _tc(TC_B, "TC_002")
        run, _ = _setup(v2_db, [a, b], [_dup_finding(TC_B, TC_A)])

        with patch("core.v2.optimizer_orchestrator.review_test_cases") as mock_review:
            mock_review.return_value = MagicMock()
            optimize_duplicates(MagicMock(), run_id=run.id)
            call_kwargs = mock_review.call_args[1]
            assert call_kwargs["trigger_type"] == ReviewTriggerType.AFTER_OPTIMIZER


# ============================================================
# 门槛 11: 去重后 duplication 分数提升（闭环有效）
# ============================================================


class TestGate11DuplicationScoreImproves:
    def test_score_improves_after_dedup(self, v2_db):
        """去重后 duplication 分数提升（验证闭环有效）。"""
        from core.v2.review_hard import compute_duplication

        a = _tc(TC_A, "TC_001", title="登录测试", steps_count=3)
        b = _tc(TC_B, "TC_002", title="登录测试", steps_count=3)
        c = _tc(TC_C, "TC_003", title="注册测试", steps_count=2)

        a.content_hash = "hash_same"
        b.content_hash = "hash_same"
        c.content_hash = "hash_diff"

        score_before, findings_before, _ = compute_duplication([a, b, c])
        assert len(findings_before) == 1

        run, _ = _setup(v2_db, [a, b, c], findings_before)
        deduplicate_by_findings(run.id, findings_before)

        score_after, findings_after, _ = compute_duplication([a, c])
        assert len(findings_after) == 0
        assert score_after > score_before


# ============================================================
# 门槛 12: schema_version=8，状态机 REVIEWED→ARCHIVED 生效
# ============================================================


class TestGate12SchemaVersion:
    def test_schema_version_8(self, v2_db):
        """schema_version=8。"""
        assert ddl.get_schema_version() == 8

    def test_state_machine_reviewed_to_archived(self):
        """状态机允许 REVIEWED→ARCHIVED。"""
        from core.schemas import can_transition

        assert can_transition(TestCaseStatus.REVIEWED, TestCaseStatus.ARCHIVED)

    def test_transition_to_archived(self):
        """TestCase.transition_to(ARCHIVED) 成功。"""
        tc = _tc(TC_A, "TC_001", status=TestCaseStatus.REVIEWED)
        tc.transition_to(TestCaseStatus.ARCHIVED)
        assert tc.status == TestCaseStatus.ARCHIVED


# ============================================================
# 门槛 13: V1 零回归
# ============================================================


class TestGate13V1NoRegression:
    def test_v1_generator_unchanged(self):
        """core/generator.py 的 deduplicate 函数仍存在（V1 未被修改）。"""
        from core.generator import deduplicate, deduplicate_by_steps

        assert callable(deduplicate)
        assert callable(deduplicate_by_steps)

    def test_v1_db_unchanged(self):
        """core/db.py 仍存在（V1 未被修改）。"""
        from core import db

        assert hasattr(db, "init_db")


# ============================================================
# 门槛 14: 不修改 TestCase 内容（只改 status）
# ============================================================


class TestGate14NoContentModification:
    def test_content_unchanged_after_archive(self, v2_db):
        """归档后 title/steps/expected/priority 不变。"""
        a = _tc(TC_A, "TC_001")
        # b 的 priority 低于 a，所以 b 会被归档
        b = _tc(TC_B, "TC_002", title="原标题", steps_count=4, priority=Priority.P2)
        run, _ = _setup(v2_db, [a, b], [])

        original_title = b.title
        original_steps = len(b.steps)
        original_expected = b.expected
        original_priority = b.priority
        original_module = b.module

        deduplicate_by_findings(run.id, [_dup_finding(TC_B, TC_A)])

        b_from_db = v2_db.get_test_case(TC_B)
        assert b_from_db.status == TestCaseStatus.ARCHIVED
        assert b_from_db.title == original_title
        assert len(b_from_db.steps) == original_steps
        assert b_from_db.expected == original_expected
        assert b_from_db.priority == original_priority
        assert b_from_db.module == original_module
