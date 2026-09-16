"""V2 Step 7 验收测试 - 14 条门槛逐条对应。

对应 docs/v2/step7-ai-reviewer.md 与 plan §八。全 mock，不调真实 API。
核心：混合分工（硬指标代码算稳定 + 软维度 LLM 带 reason/证据）、REVIEWED≠合格、只标记不修复。
"""

import json

from core.schemas import (
    DuplicateLevel,
    GenerationConfig,
    GenerationScope,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    ReviewDimension,
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
from core.v2.db import v2_read_conn
from core.v2.ddl import get_schema_version
from core.v2.review_orchestrator import review_test_cases
from core.v2.review_soft import run_soft_review

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
FAKE_ULID = "01ARZ3NDEKTSV4RRFFQ69G5ZZZ"


class ReviewClient:
    def __init__(self, accuracy=80, missing=70, exec_sem=75, findings=None):
        self.response = json.dumps(
            {
                "soft_reviews": [
                    {
                        "dimension": "accuracy",
                        "score": accuracy,
                        "reason": "准确性理由",
                        "findings": (findings or {}).get("accuracy", []),
                    },
                    {
                        "dimension": "missing_risk",
                        "score": missing,
                        "reason": "遗漏理由",
                        "findings": (findings or {}).get("missing_risk", []),
                    },
                    {"dimension": "executability_semantic", "score": exec_sem, "reason": "可执行理由", "findings": []},
                ]
            },
            ensure_ascii=False,
        )
        self.calls = 0

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls += 1
        return self.response


def _setup(v2_db, *, cases_spec=None, extra_uncovered_item=False):
    """cases_spec: list of (display_id, tp_title, tc_title, actions, expected, content_hash_override)。"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="Step7 验收", source_type=SourceType.TEXT)
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
        )
    ]
    if extra_uncovered_item:
        items.append(
            RequirementItem(
                version_id=ver.id,
                seq=2,
                type=RequirementItemType.FUNCTION,
                module="登录",
                statement="未覆盖需求",
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

    if cases_spec is None:
        cases_spec = [("TC_001", "TP0", "登录成功", ["输入账号", "点击登录"], "登录成功")]
    cases = []
    for display_id, tp_title, tc_title, actions, expected in cases_spec:
        tp = TestPoint(
            run_id=run.id,
            version_id=ver.id,
            item_ids=[items[0].id],
            module="登录",
            subcategory="功能",
            title=tp_title,
            description="d",
            dimension=TestDimension.FUNCTIONAL,
            priority=Priority.P1,
            provenance=Provenance.LLM,
            generation_scope=GenerationScope.ITEM,
        )
        v2_db.save_test_point(tp)
        tc = TestCase(
            run_id=run.id,
            display_id=display_id,
            test_point_ids=[tp.id],
            module="登录",
            title=tc_title,
            precondition="",
            steps=[TestStep(seq=i + 1, action=a, expected="ok") for i, a in enumerate(actions)],
            expected=expected,
            priority=Priority.P1,
            type=TestCaseType.FUNCTIONAL,
            status=TestCaseStatus.VALIDATED,
        )
        v2_db.save_test_case(tc)
        cases.append(tc)
    return run, ver, items, cases


# 门槛 1：ReviewReport 持久化 + 6 维齐全
def test_01_report_persisted_six_scores(v2_db):
    run, *_ = _setup(v2_db)
    result = review_test_cases(ReviewClient(), run_id=run.id)
    assert result.report.revision == 1
    assert result.report.trigger_type == ReviewTriggerType.INITIAL
    got = repo.get_review_report(result.report.id)
    s = got.scores
    for v in (s.coverage, s.accuracy, s.executability, s.consistency, s.missing_risk, s.duplication):
        assert 0 <= v <= 100
    assert 0 <= got.overall_score <= 100


# 门槛 2：coverage 双指标
def test_02_coverage_dual_metrics(v2_db):
    run, ver, items, cases = _setup(v2_db, extra_uncovered_item=True)
    report = review_test_cases(ReviewClient(), run_id=run.id).report
    cd = report.coverage_detail
    assert cd.strategy_obligation_coverage == 1.0
    assert cd.requirement_item_coverage == 0.5
    assert cd.uncovered_item_ids == [items[1].id]


# 门槛 3：executability 加权（非平均）
def test_03_executability_weighted(v2_db):
    run, *_ = _setup(v2_db)
    report = review_test_cases(ReviewClient(exec_sem=75), run_id=run.id).report
    assert report.scores.executability == round(100 * 0.4 + 75 * 0.6, 2) == 85.0
    assert report.scores.executability != 87.5  # 非简单平均


# 门槛 4：duplication 两级 + auto_fixable + 不删用例
def test_04_duplication_two_levels(v2_db):
    # 两条用例内容相同（同 content_hash）但关联不同 TP → EXACT 重复
    run, ver, items, cases = _setup(
        v2_db,
        cases_spec=[
            ("TC_001", "TPa", "登录成功", ["输入账号", "点击登录"], "登录成功"),
            ("TC_002", "TPb", "登录成功", ["输入账号", "点击登录"], "登录成功"),
        ],
    )
    n_before = len(repo.list_test_cases(run.id))
    report = review_test_cases(ReviewClient(), run_id=run.id).report
    dup = [f for f in report.findings if f.dimension == ReviewDimension.DUPLICATION]
    assert dup
    assert dup[0].detail["duplicate_level"] == DuplicateLevel.EXACT.value
    assert dup[0].auto_fixable is True
    # Step 7 不删用例（Step 8 才删）
    assert len(repo.list_test_cases(run.id)) == n_before


def test_04b_semantic_duplicate_level(v2_db):
    run, ver, items, cases = _setup(
        v2_db,
        cases_spec=[
            ("TC_001", "TPa", "验证手机号为空时无法登录", ["打开登录页", "手机号留空", "点击登录"], "阻止登录"),
            ("TC_002", "TPb", "验证手机号为空时不能登录", ["打开登录页", "手机号留空", "点击登录按钮"], "阻止登录"),
        ],
    )
    report = review_test_cases(ReviewClient(), run_id=run.id).report
    dup = [f for f in report.findings if f.dimension == ReviewDimension.DUPLICATION]
    assert dup
    assert dup[0].detail["duplicate_level"] == DuplicateLevel.SEMANTIC.value
    assert 0 < dup[0].detail["similarity"] < 1.0


# 门槛 5：provenance 可区分
def test_05_provenance_validator_vs_llm(v2_db):
    run, ver, items, cases = _setup(v2_db, extra_uncovered_item=True)
    report = review_test_cases(
        ReviewClient(
            findings={
                "accuracy": [
                    {"target_type": "testcase", "target_ref": "TC_001", "severity": "major", "issue": "预期笼统"}
                ]
            }
        ),
        run_id=run.id,
    ).report
    provs = {f.provenance for f in report.findings}
    assert Provenance.VALIDATOR in provs
    assert Provenance.LLM in provs


# 门槛 6：soft score 带 reason
def test_06_soft_score_has_reason(v2_db):
    run, *_ = _setup(v2_db)
    report = review_test_cases(ReviewClient(), run_id=run.id).report
    assert report.dimension_reasons.get("accuracy") == "准确性理由"
    assert report.dimension_reasons.get("missing_risk") == "遗漏理由"


# 门槛 7：LLM finding 引用真实 RequirementItem + rule_name
def test_07_missing_risk_evidence(v2_db):
    run, ver, items, cases = _setup(v2_db)
    report = review_test_cases(
        ReviewClient(
            findings={
                "missing_risk": [
                    {
                        "target_type": "requirement_item",
                        "target_ref": items[0].id,
                        "severity": "minor",
                        "issue": "遗漏场景",
                        "detail": {"rule_name": "验证码有效期"},
                    }
                ]
            }
        ),
        run_id=run.id,
    ).report
    mr = [f for f in report.findings if f.dimension == ReviewDimension.MISSING_RISK]
    assert mr
    assert mr[0].target_type == TargetType.REQUIREMENT_ITEM
    assert mr[0].target_id == items[0].id
    assert mr[0].detail["rule_name"] == "验证码有效期"


# 门槛 8：非法 target 丢弃
def test_08_invalid_target_dropped(v2_db):
    run, ver, items, cases = _setup(v2_db)
    r = run_soft_review(
        ReviewClient(
            findings={
                "accuracy": [{"target_type": "testcase", "target_ref": FAKE_ULID, "severity": "major", "issue": "幻觉"}]
            }
        ),
        cases,
        items,
    )
    assert r.findings == []
    assert any("证据锚定失败" in i or "非法 target" in i for i in r.issues)


# 门槛 9：VALIDATED→REVIEWED 全部转，低分也转（REVIEWED≠合格）
def test_09_reviewed_not_equal_passed(v2_db):
    run, ver, items, cases = _setup(v2_db, extra_uncovered_item=True)
    result = review_test_cases(ReviewClient(accuracy=20, missing=15, exec_sem=20), run_id=run.id)
    assert result.report.overall_score < 60  # 低分
    assert len(result.report.findings) > 0
    for c in cases:
        assert repo.get_test_case(c.id).status == TestCaseStatus.REVIEWED  # 低分仍转 REVIEWED


# 门槛 10：review_target 填充
def test_10_review_target(v2_db):
    run, ver, items, cases = _setup(v2_db)
    report = review_test_cases(ReviewClient(), run_id=run.id).report
    assert report.review_target_type == TargetType.TESTCASE
    assert set(report.review_target_ids) == {c.id for c in cases}


# 门槛 11：只标记不修复（用例内容不变）
def test_11_only_mark_not_fix(v2_db):
    run, ver, items, cases = _setup(
        v2_db,
        cases_spec=[
            ("TC_001", "TPa", "登录成功", ["输入账号", "点击登录"], "登录成功"),
            ("TC_002", "TPb", "登录成功", ["输入账号", "点击登录"], "登录成功"),  # 与 TC_001 重复
        ],
    )
    before = {c.id: (c.title, c.render_steps_text(), c.content_hash) for c in repo.list_test_cases(run.id)}
    report = review_test_cases(ReviewClient(), run_id=run.id).report
    # 有 auto_fixable 的重复 finding
    assert any(f.auto_fixable for f in report.findings)
    # 但用例内容/数量不变（Step 7 不修复/不去重）
    after = repo.list_test_cases(run.id)
    assert len(after) == len(before)
    for c in after:
        assert (c.title, c.render_steps_text(), c.content_hash) == before[c.id]


# 门槛 12：稳定性——硬指标多次一致，LLM 软分可波动
def test_12_hard_stable_soft_varies(v2_db):
    run, ver, items, cases = _setup(v2_db, extra_uncovered_item=True)
    hard_triples = []
    soft_acc = []
    for acc in (80, 55, 90):  # LLM 软分波动
        for c in repo.list_test_cases(run.id):
            repo.update_test_case_status(c.id, TestCaseStatus.VALIDATED.value)  # 重置以便再评
        rep = review_test_cases(ReviewClient(accuracy=acc), run_id=run.id).report
        hard_triples.append(
            (
                rep.scores.coverage,
                rep.scores.duplication,
                rep.scores.consistency,
                rep.executability_detail.structural_score,
            )
        )
        soft_acc.append(rep.scores.accuracy)
    # 硬指标三次完全一致
    assert hard_triples[0] == hard_triples[1] == hard_triples[2]
    # LLM 软分随输入波动
    assert soft_acc == [80, 55, 90]


# 门槛 13：schema_version=7 + 新列
def test_13_schema_v7_and_new_columns(v2_db):
    assert get_schema_version() >= 7
    with v2_read_conn() as conn:
        rr = {r["name"] for r in conn.execute("PRAGMA table_info(review_reports)").fetchall()}
        rf = {r["name"] for r in conn.execute("PRAGMA table_info(review_findings)").fetchall()}
    assert {
        "review_target_type",
        "review_target_ids_json",
        "coverage_detail_json",
        "executability_detail_json",
        "dimension_reasons_json",
    } <= rr
    assert "detail_json" in rf


# 门槛 14：V1 零回归
def test_14_v1_untouched(v2_db):
    from core import db as v1_db

    assert v1_db._DB_PATH.endswith("data.db")
    # V1 reviewer.py 的自由文本评审 Prompt 仍在（未被 V2 覆盖）
    from core.reviewer import REVIEW_SYSTEM_PROMPT

    assert "资深测试架构师" in REVIEW_SYSTEM_PROMPT
    from web.data import TEST_POINTS_PROMPT

    assert "资深测试工程师" in TEST_POINTS_PROMPT
