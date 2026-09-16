"""V2 Step 7 评审编排测试 - 端到端、ReviewReport 持久化、TestCase→REVIEWED、revision、加权、只读。

用 mock client（软维度返回可控分数），DB 用 v2_db fixture。
"""

import json

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
    TargetType,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestDimension,
    TestPoint,
    TestStep,
)
from core.v2 import repository as repo
from core.v2.review_orchestrator import (
    EXECUTABILITY_SEMANTIC_WEIGHT,
    EXECUTABILITY_STRUCTURAL_WEIGHT,
    ReviewResult,
    review_test_cases,
)
from core.v2.review_prompts import REVIEWER_PROMPT_VERSION

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class ReviewClient:
    def __init__(self, accuracy=80, missing=70, exec_sem=75, acc_findings=None):
        self.response = json.dumps(
            {
                "soft_reviews": [
                    {
                        "dimension": "accuracy",
                        "score": accuracy,
                        "reason": "准确性理由",
                        "findings": acc_findings or [],
                    },
                    {"dimension": "missing_risk", "score": missing, "reason": "遗漏理由", "findings": []},
                    {"dimension": "executability_semantic", "score": exec_sem, "reason": "可执行理由", "findings": []},
                ]
            },
            ensure_ascii=False,
        )
        self.calls = 0

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls += 1
        return self.response


def _setup(v2_db, *, n_cases=2, extra_uncovered_item=False):
    """建 Doc+Version+items+Run+Config+TestPoints+VALIDATED TestCases。"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="Step7 评审", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    v2_db.save_version(ver)
    items = [
        RequirementItem(
            version_id=ver.id,
            seq=1,
            type=RequirementItemType.FUNCTION,
            module="登录",
            statement="手机号登录",
            confidence=0.9,
        ),
    ]
    if extra_uncovered_item:
        items.append(
            RequirementItem(
                version_id=ver.id,
                seq=2,
                type=RequirementItemType.FUNCTION,
                module="登录",
                statement="未被覆盖的需求",
                confidence=0.9,
            )
        )
    for it in items:
        v2_db.save_item(it)
    doc.latest_version_id = ver.id
    v2_db.save_doc(doc)

    cfg = GenerationConfig(
        model_provider="o", model_name="g", temperature=0.3, prompt_version="p", generator_version="g"
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

    tps, cases = [], []
    for i in range(n_cases):
        tp = TestPoint(
            run_id=run.id,
            version_id=ver.id,
            item_ids=[items[0].id],
            module="登录",
            subcategory="功能",
            title=f"TP{i}",
            description="d",
            dimension=TestDimension.FUNCTIONAL,
            priority=Priority.P1,
            provenance=Provenance.LLM,
            generation_scope=GenerationScope.ITEM,
        )
        v2_db.save_test_point(tp)
        tps.append(tp)
        tc = TestCase(
            run_id=run.id,
            display_id=f"TC_{i + 1:03d}",
            test_point_ids=[tp.id],
            module="登录",
            title=f"用例{i}",
            precondition="",
            steps=[TestStep(seq=1, action="操作", expected="结果")],
            expected="预期",
            priority=Priority.P1,
            type=TestCaseType.FUNCTIONAL,
            status=TestCaseStatus.VALIDATED,
        )
        v2_db.save_test_case(tc)
        cases.append(tc)
    return run, ver, items, tps, cases


# ============================================================
# 端到端 + 持久化（门槛 1）
# ============================================================


class TestEndToEnd:
    def test_report_generated_and_persisted(self, v2_db):
        run, ver, items, tps, cases = _setup(v2_db)
        result = review_test_cases(ReviewClient(), run_id=run.id)
        assert isinstance(result, ReviewResult)
        assert result.report is not None
        assert result.report.revision == 1
        assert result.report.trigger_type == ReviewTriggerType.INITIAL
        assert result.reviewed_count == 2
        # 持久化后可读回
        got = repo.get_review_report(result.report.id)
        assert got is not None
        assert got.scores.coverage >= 0 and got.scores.accuracy == 80

    def test_six_scores_present(self, v2_db):
        run, *_ = _setup(v2_db)
        report = review_test_cases(ReviewClient(), run_id=run.id).report
        s = report.scores
        for v in (s.coverage, s.accuracy, s.executability, s.consistency, s.missing_risk, s.duplication):
            assert 0 <= v <= 100
        assert 0 <= report.overall_score <= 100

    def test_run_status_done(self, v2_db):
        run, *_ = _setup(v2_db)
        review_test_cases(ReviewClient(), run_id=run.id)
        assert repo.get_run(run.id).status == RunStatus.DONE

    def test_reviewer_version_augmented(self, v2_db):
        run, *_ = _setup(v2_db)
        review_test_cases(ReviewClient(), run_id=run.id)
        cfg = repo.get_generation_config(run.generation_config_id)
        assert cfg.reviewer_version == REVIEWER_PROMPT_VERSION


# ============================================================
# TestCase 状态转移（门槛 9：REVIEWED≠合格）
# ============================================================


class TestStatusTransition:
    def test_validated_to_reviewed(self, v2_db):
        run, ver, items, tps, cases = _setup(v2_db)
        review_test_cases(ReviewClient(), run_id=run.id)
        for c in cases:
            assert repo.get_test_case(c.id).status == TestCaseStatus.REVIEWED

    def test_low_score_still_reviewed(self, v2_db):
        """门槛 9：低分 + 有 findings 的用例同样转 REVIEWED（REVIEWED 仅代表评审完成）"""
        run, ver, items, tps, cases = _setup(v2_db, extra_uncovered_item=True)
        result = review_test_cases(ReviewClient(accuracy=30, missing=20, exec_sem=25), run_id=run.id)
        assert result.report.overall_score < 60  # 低分
        assert len(result.report.findings) > 0  # 有 findings
        for c in cases:
            assert repo.get_test_case(c.id).status == TestCaseStatus.REVIEWED  # 仍转 REVIEWED

    def test_only_validated_reviewed(self, v2_db):
        """非 VALIDATED（如 GENERATED）的用例不纳入评审"""
        run, ver, items, tps, cases = _setup(v2_db, n_cases=1)
        # 额外建一个 TP + GENERATED 用例（不同 TP → 不同 fingerprint，避免与 VALIDATED 用例幂等碰撞）
        tp2 = TestPoint(
            run_id=run.id,
            version_id=ver.id,
            item_ids=[items[0].id],
            module="登录",
            subcategory="功能",
            title="TP-extra",
            description="d",
            dimension=TestDimension.FUNCTIONAL,
            priority=Priority.P1,
            provenance=Provenance.LLM,
            generation_scope=GenerationScope.ITEM,
        )
        v2_db.save_test_point(tp2)
        gc = TestCase(
            run_id=run.id,
            display_id="TC_099",
            test_point_ids=[tp2.id],
            module="登录",
            title="未校验",
            precondition="",
            steps=[TestStep(seq=1, action="a", expected="b")],
            expected="e",
            priority=Priority.P1,
            type=TestCaseType.FUNCTIONAL,
            status=TestCaseStatus.GENERATED,
        )
        v2_db.save_test_case(gc)
        result = review_test_cases(ReviewClient(), run_id=run.id)
        assert result.reviewed_count == 1  # 只评审 VALIDATED 的那条
        assert repo.get_test_case(gc.id).status == TestCaseStatus.GENERATED  # 未被转


# ============================================================
# coverage 双指标 + executability 加权（门槛 2/3）
# ============================================================


class TestScoresComposition:
    def test_coverage_detail_dual_metrics(self, v2_db):
        """门槛 2：coverage_detail 保留双指标 + uncovered_item_ids"""
        run, ver, items, tps, cases = _setup(v2_db, extra_uncovered_item=True)
        report = review_test_cases(ReviewClient(), run_id=run.id).report
        cd = report.coverage_detail
        assert cd is not None
        assert cd.requirement_item_coverage == 0.5  # 2 items，1 未覆盖
        assert cd.strategy_obligation_coverage == 1.0  # 无 obligation → ratio=1.0
        assert len(cd.uncovered_item_ids) == 1

    def test_executability_weighted(self, v2_db):
        """门槛 3：executability = structural×0.4 + semantic×0.6（非简单平均）"""
        run, *_ = _setup(v2_db)
        report = review_test_cases(ReviewClient(exec_sem=75), run_id=run.id).report
        ed = report.executability_detail
        assert ed.structural_score == 100.0  # 用例结构完整
        assert ed.semantic_score == 75.0
        expected = round(100.0 * EXECUTABILITY_STRUCTURAL_WEIGHT + 75.0 * EXECUTABILITY_SEMANTIC_WEIGHT, 2)
        assert report.scores.executability == expected == 85.0
        # 反证：非简单平均 (100+75)/2=87.5
        assert report.scores.executability != 87.5

    def test_dimension_reasons_complete(self, v2_db):
        """门槛 6：dimension_reasons 含硬维度 + 软维度理由"""
        run, *_ = _setup(v2_db)
        report = review_test_cases(ReviewClient(), run_id=run.id).report
        for dim in ("coverage", "duplication", "consistency", "executability_structural", "accuracy", "missing_risk"):
            assert dim in report.dimension_reasons


# ============================================================
# findings 持久化 + provenance（门槛 5）+ review_target（门槛 10）
# ============================================================


class TestFindingsAndTarget:
    def test_validator_and_llm_findings_persisted(self, v2_db):
        """门槛 5：validator 与 llm findings 都持久化，provenance 可区分"""
        run, ver, items, tps, cases = _setup(v2_db, extra_uncovered_item=True)
        # LLM 给一条 accuracy finding（引用 display_id）
        client = ReviewClient(
            acc_findings=[{"target_type": "testcase", "target_ref": "TC_001", "severity": "major", "issue": "预期笼统"}]
        )
        report = review_test_cases(client, run_id=run.id).report
        got = repo.get_review_report(report.id)
        provs = {f.provenance for f in got.findings}
        assert Provenance.VALIDATOR in provs  # coverage 缺口（未覆盖 item）
        assert Provenance.LLM in provs  # accuracy finding

    def test_coverage_finding_targets_item(self, v2_db):
        run, ver, items, tps, cases = _setup(v2_db, extra_uncovered_item=True)
        report = review_test_cases(ReviewClient(), run_id=run.id).report
        cov_findings = [f for f in report.findings if f.dimension.value == "coverage"]
        assert cov_findings
        assert cov_findings[0].target_type == TargetType.REQUIREMENT_ITEM
        assert cov_findings[0].target_id == items[1].id  # 未覆盖的 item

    def test_review_target_filled(self, v2_db):
        """门槛 10：review_target_type=TESTCASE + review_target_ids=被评审 TestCase[]"""
        run, ver, items, tps, cases = _setup(v2_db)
        report = review_test_cases(ReviewClient(), run_id=run.id).report
        assert report.review_target_type == TargetType.TESTCASE
        assert set(report.review_target_ids) == {c.id for c in cases}

    def test_llm_finding_detail_roundtrip(self, v2_db):
        run, *_ = _setup(v2_db)
        client = ReviewClient(
            acc_findings=[
                {
                    "target_type": "testcase",
                    "target_ref": "TC_001",
                    "severity": "minor",
                    "issue": "x",
                    "detail": {"rule_name": "r1"},
                }
            ]
        )
        report = review_test_cases(client, run_id=run.id).report
        got = repo.get_review_report(report.id)
        llm_f = [f for f in got.findings if f.provenance == Provenance.LLM][0]
        assert llm_f.detail == {"rule_name": "r1"}


# ============================================================
# revision 递增 + 边界 + 只读（门槛 11）
# ============================================================


class TestRevisionAndEdge:
    def test_revision_increments(self, v2_db):
        run, *_ = _setup(v2_db)
        r1 = review_test_cases(ReviewClient(), run_id=run.id)
        assert r1.report.revision == 1
        # 用例已转 REVIEWED，第二轮无 VALIDATED → 需重新置为 VALIDATED 才能再评
        for c in repo.list_test_cases(run.id):
            repo.update_test_case_status(c.id, TestCaseStatus.VALIDATED.value)
        r2 = review_test_cases(ReviewClient(), run_id=run.id, trigger_type=ReviewTriggerType.AFTER_OPTIMIZER)
        assert r2.report.revision == 2
        reports = repo.list_review_reports(run.id)
        assert [r.revision for r in reports] == [1, 2]

    def test_get_latest_review_report(self, v2_db):
        run, *_ = _setup(v2_db)
        review_test_cases(ReviewClient(), run_id=run.id)
        latest = repo.get_latest_review_report(run.id)
        assert latest is not None and latest.revision == 1

    def test_no_validated_cases(self, v2_db):
        run, *_ = _setup(v2_db, n_cases=0)
        result = review_test_cases(ReviewClient(), run_id=run.id)
        assert result.report is None
        assert result.reviewed_count == 0
        assert any("VALIDATED" in i for i in result.issues)

    def test_run_not_found(self, v2_db):
        result = review_test_cases(ReviewClient(), run_id="01ARZ3NDEKTSV4RRFFQ69G5ZZZ")
        assert result.report is None
        assert any("Run 不存在" in i for i in result.issues)

    def test_readonly_case_content_unchanged(self, v2_db):
        """门槛 11：评审只改 status，不改用例内容（title/steps/expected 不动）"""
        run, ver, items, tps, cases = _setup(v2_db)
        before = {c.id: (c.title, c.render_steps_text(), c.expected, c.content_hash) for c in cases}
        review_test_cases(ReviewClient(), run_id=run.id)
        for c in cases:
            got = repo.get_test_case(c.id)
            assert (got.title, got.render_steps_text(), got.expected, got.content_hash) == before[c.id]
            assert got.status == TestCaseStatus.REVIEWED  # 只有 status 变
