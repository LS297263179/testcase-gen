"""V2 Step 7 LLM Soft Review Engine 测试 - 解析 / reason / 证据引用 / 非法 target 兜底 / score clamp。

用 mock client（不调真实 API）；v2_db fixture 供 TargetResolver 校验非法 target 时查询。
"""

import json

from core.schemas import (
    Priority,
    Provenance,
    RequirementItem,
    RequirementItemType,
    ReviewDimension,
    Severity,
    TargetType,
    TestCase,
    TestCaseType,
    TestStep,
)
from core.v2.review_soft import DEFAULT_SOFT_SCORE_ON_FAILURE, run_soft_review

RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
FAKE_ULID = "01ARZ3NDEKTSV4RRFFQ69G5ZZZ"


class SoftClient:
    def __init__(self, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = 0
        self.prompts = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls += 1
        self.prompts.append((system_prompt, user_prompt))
        if self.raise_exc:
            raise self.raise_exc
        return self.response if self.response is not None else "{}"


def _tc(display_id="TC_001", title="登录") -> TestCase:
    return TestCase(
        run_id=RUN_ID,
        display_id=display_id,
        module="登录",
        title=title,
        precondition="",
        steps=[TestStep(seq=1, action="输入账号", expected="ok")],
        expected="登录成功",
        priority=Priority.P1,
        type=TestCaseType.FUNCTIONAL,
    )


def _item(statement="验证码 5 分钟有效") -> RequirementItem:
    return RequirementItem(
        version_id=VERSION_ID,
        seq=1,
        type=RequirementItemType.DATA_FIELD,
        module="登录",
        statement=statement,
    )


def _resp(accuracy=80, missing=70, exec_sem=75, acc_findings=None, miss_findings=None, exec_findings=None):
    return json.dumps(
        {
            "soft_reviews": [
                {"dimension": "accuracy", "score": accuracy, "reason": "准确性理由", "findings": acc_findings or []},
                {"dimension": "missing_risk", "score": missing, "reason": "遗漏理由", "findings": miss_findings or []},
                {
                    "dimension": "executability_semantic",
                    "score": exec_sem,
                    "reason": "可执行理由",
                    "findings": exec_findings or [],
                },
            ]
        },
        ensure_ascii=False,
    )


# ============================================================
# 分数解析 + reason（门槛 6）
# ============================================================


class TestSoftScores:
    def test_parse_three_dimensions(self, v2_db):
        r = run_soft_review(SoftClient(_resp(80, 70, 75)), [_tc()], [_item()])
        assert r.accuracy_score == 80
        assert r.missing_risk_score == 70
        assert r.executability_semantic_score == 75
        assert r.calls == 1

    def test_reasons_recorded(self, v2_db):
        """门槛 6：soft score 必带 reason（可解释性）"""
        r = run_soft_review(SoftClient(_resp()), [_tc()], [_item()])
        assert r.dimension_reasons["accuracy"] == "准确性理由"
        assert r.dimension_reasons["missing_risk"] == "遗漏理由"
        assert r.dimension_reasons["executability_semantic"] == "可执行理由"

    def test_score_clamp_high(self, v2_db):
        r = run_soft_review(SoftClient(_resp(accuracy=150)), [_tc()], [_item()])
        assert r.accuracy_score == 100.0

    def test_score_clamp_low(self, v2_db):
        r = run_soft_review(SoftClient(_resp(accuracy=-20)), [_tc()], [_item()])
        assert r.accuracy_score == 0.0

    def test_missing_reason_flagged(self, v2_db):
        resp = json.dumps(
            {
                "soft_reviews": [
                    {"dimension": "accuracy", "score": 80, "reason": "", "findings": []},
                    {"dimension": "missing_risk", "score": 70, "reason": "x", "findings": []},
                    {"dimension": "executability_semantic", "score": 75, "reason": "y", "findings": []},
                ]
            },
            ensure_ascii=False,
        )
        r = run_soft_review(SoftClient(resp), [_tc()], [_item()])
        assert any("缺少 reason" in i for i in r.issues)


# ============================================================
# 证据引用（门槛 7）+ 非法 target 兜底（门槛 8）
# ============================================================


class TestEvidenceAnchoring:
    def test_missing_risk_targets_real_item(self, v2_db):
        """门槛 7：missing_risk finding 引用真实 RequirementItem + rule_name"""
        item = _item()
        resp = _resp(
            miss_findings=[
                {
                    "target_type": "requirement_item",
                    "target_ref": item.id,
                    "severity": "minor",
                    "issue": "未覆盖验证码过期",
                    "detail": {"rule_name": "验证码有效期"},
                }
            ]
        )
        r = run_soft_review(SoftClient(resp), [_tc()], [item])
        assert len(r.findings) == 1
        f = r.findings[0]
        assert f.dimension == ReviewDimension.MISSING_RISK
        assert f.target_type == TargetType.REQUIREMENT_ITEM
        assert f.target_id == item.id
        assert f.detail["rule_name"] == "验证码有效期"
        assert f.provenance == Provenance.LLM

    def test_accuracy_targets_testcase_by_id(self, v2_db):
        tc = _tc()
        resp = _resp(
            acc_findings=[
                {
                    "target_type": "testcase",
                    "target_ref": tc.id,
                    "severity": "major",
                    "issue": "预期笼统",
                    "suggestion": "明确断言",
                }
            ]
        )
        r = run_soft_review(SoftClient(resp), [tc], [_item()])
        assert r.findings[0].target_id == tc.id
        assert r.findings[0].severity == Severity.MAJOR

    def test_target_ref_by_display_id_resolved(self, v2_db):
        """LLM 用 display_id 引用也能解析回真实 id"""
        tc = _tc(display_id="TC_007")
        resp = _resp(
            acc_findings=[{"target_type": "testcase", "target_ref": "TC_007", "severity": "minor", "issue": "问题"}]
        )
        r = run_soft_review(SoftClient(resp), [tc], [_item()])
        assert len(r.findings) == 1
        assert r.findings[0].target_id == tc.id

    def test_invalid_target_dropped(self, v2_db):
        """门槛 8：编造的 target_ref（不存在）→ 丢弃 + 记 issue"""
        resp = _resp(
            acc_findings=[
                {"target_type": "testcase", "target_ref": FAKE_ULID, "severity": "major", "issue": "幻觉问题"}
            ]
        )
        r = run_soft_review(SoftClient(resp), [_tc()], [_item()])
        assert r.findings == []
        assert any("非法 target" in i or "证据锚定失败" in i for i in r.issues)

    def test_invalid_target_type_dropped(self, v2_db):
        resp = _resp(
            acc_findings=[{"target_type": "obligation", "target_ref": FAKE_ULID, "severity": "major", "issue": "x"}]
        )
        r = run_soft_review(SoftClient(resp), [_tc()], [_item()])
        assert r.findings == []

    def test_finding_empty_issue_dropped(self, v2_db):
        tc = _tc()
        resp = _resp(acc_findings=[{"target_type": "testcase", "target_ref": tc.id, "severity": "minor", "issue": ""}])
        r = run_soft_review(SoftClient(resp), [tc], [_item()])
        assert r.findings == []

    def test_invalid_severity_defaults_minor(self, v2_db):
        tc = _tc()
        resp = _resp(acc_findings=[{"target_type": "testcase", "target_ref": tc.id, "severity": "乱写", "issue": "x"}])
        r = run_soft_review(SoftClient(resp), [tc], [_item()])
        assert r.findings[0].severity == Severity.MINOR

    def test_soft_findings_not_auto_fixable(self, v2_db):
        """软维度问题多需语义修复，auto_fixable=False（Step 8/人工处理）"""
        tc = _tc()
        resp = _resp(acc_findings=[{"target_type": "testcase", "target_ref": tc.id, "severity": "major", "issue": "x"}])
        r = run_soft_review(SoftClient(resp), [tc], [_item()])
        assert r.findings[0].auto_fixable is False
        assert all(f.provenance == Provenance.LLM for f in r.findings)


# ============================================================
# 降级与鲁棒性
# ============================================================


class TestDegradation:
    def test_llm_call_failure_defaults(self, v2_db):
        r = run_soft_review(SoftClient(raise_exc=RuntimeError("API down")), [_tc()], [_item()])
        assert r.accuracy_score == DEFAULT_SOFT_SCORE_ON_FAILURE
        assert r.missing_risk_score == DEFAULT_SOFT_SCORE_ON_FAILURE
        assert any("调用失败" in i for i in r.issues)

    def test_bad_json_defaults(self, v2_db):
        r = run_soft_review(SoftClient("这不是 JSON"), [_tc()], [_item()])
        assert r.accuracy_score == DEFAULT_SOFT_SCORE_ON_FAILURE
        assert any("soft_reviews" in i for i in r.issues)

    def test_missing_dimension_defaults(self, v2_db):
        """缺失某软维度 → 中性默认分 + issue"""
        resp = json.dumps(
            {
                "soft_reviews": [
                    {"dimension": "accuracy", "score": 80, "reason": "r", "findings": []},
                ]
            },
            ensure_ascii=False,
        )
        r = run_soft_review(SoftClient(resp), [_tc()], [_item()])
        assert r.accuracy_score == 80
        assert r.missing_risk_score == DEFAULT_SOFT_SCORE_ON_FAILURE
        assert any("missing_risk" in i for i in r.issues)

    def test_hard_dimension_from_llm_ignored(self, v2_db):
        """LLM 越界评硬维度（coverage）→ 忽略 + issue（硬维度只由代码算）"""
        resp = json.dumps(
            {
                "soft_reviews": [
                    {"dimension": "coverage", "score": 99, "reason": "x", "findings": []},
                    {"dimension": "accuracy", "score": 80, "reason": "r", "findings": []},
                    {"dimension": "missing_risk", "score": 70, "reason": "r", "findings": []},
                    {"dimension": "executability_semantic", "score": 75, "reason": "r", "findings": []},
                ]
            },
            ensure_ascii=False,
        )
        r = run_soft_review(SoftClient(resp), [_tc()], [_item()])
        assert any("越界" in i or "未知" in i for i in r.issues)
        assert r.accuracy_score == 80

    def test_empty_cases_no_call(self, v2_db):
        r = run_soft_review(SoftClient(_resp()), [], [_item()])
        assert r.calls == 0
        assert any("无 TestCase" in i for i in r.issues)

    def test_prompt_contains_evidence_anchors(self, v2_db):
        """user prompt 含 TestCase/RequirementItem 的真实 id 供 LLM 引用"""
        tc = _tc()
        item = _item()
        client = SoftClient(_resp())
        run_soft_review(client, [tc], [item])
        user = client.prompts[0][1]
        assert tc.id in user
        assert item.id in user
