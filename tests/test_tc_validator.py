"""V2 Step 5 用例校验器测试 - 可修复/不可修复、身份/内容指纹分离、状态机、去重。

对应验收门槛 3/4/5/8/16/18 与 plan D1/D5。全代码，不调 LLM。
"""

from core.schemas import (
    ConfidenceLevel,
    DataPlanItem,
    GenerationMode,
    GenerationScope,
    Priority,
    Provenance,
    TestCaseStatus,
    TestCaseType,
    TestDimension,
    TestPoint,
)
from core.v2.fingerprint import compute_testcase_content_hash, compute_testcase_fingerprint
from core.v2.tc_generator import TestCaseCandidate
from core.v2.tc_validator import BuildResult, dedupe_cases, validate_and_build

VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
ITEM_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD2"
TP_ID = "01ARZ3NDEKTSV4RRFFQ69G5FE3"


def _tp(
    provenance=Provenance.LLM, technique=None, dimension=TestDimension.FUNCTIONAL, priority=Priority.P1
) -> TestPoint:
    return TestPoint(
        id=TP_ID,
        run_id=RUN_ID,
        version_id=VERSION_ID,
        item_ids=[ITEM_ID],
        module="登录",
        subcategory="功能",
        title="退出登录",
        description="验证退出登录",
        dimension=dimension,
        technique=technique,
        priority=priority,
        provenance=provenance,
        generation_scope=GenerationScope.ITEM if provenance == Provenance.LLM else GenerationScope.STRATEGY,
    )


def _candidate(raw=None, mode=GenerationMode.LLM, data_plan=None, issues=None) -> TestCaseCandidate:
    default_raw = {
        "module": "登录",
        "title": "退出登录后回到登录页",
        "precondition": "用户已登录",
        "steps": [{"action": "点击退出", "data": None, "expected": "清除会话"}],
        "expected": "跳转到登录页",
        "type": "functional",
        "priority": "P1",
        "remark": "",
    }
    return TestCaseCandidate(
        test_point_id=TP_ID,
        raw=raw if raw is not None else default_raw,
        generation_mode=mode,
        data_plan=data_plan or [],
        issues=issues or [],
    )


def _build(cand, tp=None, display_id="TC_001"):
    return validate_and_build(cand, tp=tp or _tp(), run_id=RUN_ID, version_id=VERSION_ID, display_id=display_id)


# ============================================================
# 可修复项（GENERATED → VALIDATED）
# ============================================================


class TestRepairable:
    def test_valid_case_status_validated(self):
        r = _build(_candidate())
        assert isinstance(r, BuildResult)
        assert r.status == TestCaseStatus.VALIDATED
        assert r.test_case is not None
        assert r.test_case.status == TestCaseStatus.VALIDATED
        assert r.validation_errors == []

    def test_seq_reordered_consecutive(self):
        """门槛 3：LLM 给的 seq 不连续（1,3,4）→ 代码重排 1,2,3"""
        raw = {
            "module": "登录",
            "title": "t",
            "precondition": "",
            "expected": "e",
            "type": "functional",
            "priority": "P1",
            "steps": [
                {"seq": 1, "action": "a1"},
                {"seq": 3, "action": "a2"},
                {"seq": 4, "action": "a3"},
            ],
        }
        r = _build(_candidate(raw=raw))
        seqs = [s.seq for s in r.test_case.steps]
        assert seqs == [1, 2, 3]

    def test_empty_action_step_dropped(self):
        """门槛 3：action 为空的 step 被丢弃"""
        raw = {
            "module": "登录",
            "title": "t",
            "precondition": "",
            "expected": "e",
            "type": "functional",
            "priority": "P1",
            "steps": [{"action": "有效步骤"}, {"action": ""}, {"action": "   "}, {"not_action": "x"}],
        }
        r = _build(_candidate(raw=raw))
        assert len(r.test_case.steps) == 1
        assert r.test_case.steps[0].action == "有效步骤"
        assert any("丢弃" in i for i in r.issues)

    def test_type_illegal_fallback_by_dimension(self):
        """门槛 4：type 非法 → 按 dimension 兜底推断"""
        raw = {
            "module": "登录",
            "title": "t",
            "precondition": "",
            "expected": "e",
            "type": "乱写的类型",
            "priority": "P1",
            "steps": [{"action": "a"}],
        }
        tp = _tp(dimension=TestDimension.BOUNDARY)
        r = _build(_candidate(raw=raw), tp=tp)
        assert r.test_case.type == TestCaseType.BOUNDARY
        assert any("type" in i for i in r.issues)

    def test_type_missing_fallback_functional(self):
        raw = {
            "module": "登录",
            "title": "t",
            "precondition": "",
            "expected": "e",
            "priority": "P1",
            "steps": [{"action": "a"}],
        }
        r = _build(_candidate(raw=raw), tp=_tp(dimension=TestDimension.FUNCTIONAL))
        assert r.test_case.type == TestCaseType.FUNCTIONAL

    def test_priority_illegal_fallback_from_tp(self):
        raw = {
            "module": "登录",
            "title": "t",
            "precondition": "",
            "expected": "e",
            "type": "functional",
            "priority": "P99",
            "steps": [{"action": "a"}],
        }
        r = _build(_candidate(raw=raw), tp=_tp(priority=Priority.P0))
        assert r.test_case.priority == Priority.P0

    def test_module_overwritten_from_tp(self):
        """module 硬性覆写为 TestPoint.module，不接受 LLM 给的值"""
        raw = {
            "module": "LLM乱写模块",
            "title": "t",
            "precondition": "",
            "expected": "e",
            "type": "functional",
            "priority": "P1",
            "steps": [{"action": "a"}],
        }
        r = _build(_candidate(raw=raw), tp=_tp())
        assert r.test_case.module == "登录"

    def test_test_point_ids_injected(self):
        """test_point_ids 硬性覆写为 [tp.id]（1:1 派生）"""
        r = _build(_candidate())
        assert r.test_case.test_point_ids == [TP_ID]

    def test_steps_data_expected_coerced_to_str(self):
        raw = {
            "module": "登录",
            "title": "t",
            "precondition": "",
            "expected": "e",
            "type": "functional",
            "priority": "P1",
            "steps": [{"action": "输入", "data": 123, "expected": 456}],
        }
        r = _build(_candidate(raw=raw))
        assert r.test_case.steps[0].data == "123"
        assert r.test_case.steps[0].expected == "456"


# ============================================================
# 不可修复项（GENERATED → VALIDATION_FAILED + validation_errors）
# ============================================================


class TestUnrepairable:
    def test_empty_steps_failed(self):
        """门槛 8/18：steps 全空 → VALIDATION_FAILED + validation_errors"""
        raw = {
            "module": "登录",
            "title": "t",
            "precondition": "",
            "expected": "e",
            "type": "functional",
            "priority": "P1",
            "steps": [],
        }
        r = _build(_candidate(raw=raw))
        assert r.status == TestCaseStatus.VALIDATION_FAILED
        assert r.test_case.status == TestCaseStatus.VALIDATION_FAILED
        assert any("steps 为空" in e for e in r.validation_errors)
        assert r.test_case.validation_errors == r.validation_errors

    def test_all_action_empty_steps_failed(self):
        raw = {
            "module": "登录",
            "title": "t",
            "precondition": "",
            "expected": "e",
            "type": "functional",
            "priority": "P1",
            "steps": [{"action": ""}],
        }
        r = _build(_candidate(raw=raw))
        assert r.status == TestCaseStatus.VALIDATION_FAILED

    def test_empty_title_failed(self):
        raw = {
            "module": "登录",
            "title": "",
            "precondition": "",
            "expected": "e",
            "type": "functional",
            "priority": "P1",
            "steps": [{"action": "a"}],
        }
        r = _build(_candidate(raw=raw))
        assert r.status == TestCaseStatus.VALIDATION_FAILED
        assert any("title 为空" in e for e in r.validation_errors)

    def test_empty_expected_failed(self):
        raw = {
            "module": "登录",
            "title": "t",
            "precondition": "",
            "expected": "  ",
            "type": "functional",
            "priority": "P1",
            "steps": [{"action": "a"}],
        }
        r = _build(_candidate(raw=raw))
        assert r.status == TestCaseStatus.VALIDATION_FAILED
        assert any("expected 为空" in e for e in r.validation_errors)

    def test_failed_case_still_has_fingerprint(self):
        """VALIDATION_FAILED 的用例仍计算 fingerprint（身份不依赖内容）"""
        raw = {
            "module": "登录",
            "title": "",
            "precondition": "",
            "expected": "",
            "type": "functional",
            "priority": "P1",
            "steps": [],
        }
        r = _build(_candidate(raw=raw))
        assert r.test_case.fingerprint and r.test_case.fingerprint.startswith("tc_")


# ============================================================
# fingerprint（身份）/ content_hash（内容）分离 — 门槛 5/16
# ============================================================


class TestFingerprintIdentity:
    def test_fingerprint_format(self):
        """门槛 5：fingerprint = tc_ + 32 hex"""
        r = _build(_candidate())
        fp = r.test_case.fingerprint
        assert fp.startswith("tc_")
        assert len(fp) == 35
        int(fp[3:], 16)  # 后 32 位是十六进制

    def test_content_hash_format(self):
        r = _build(_candidate())
        ch = r.test_case.content_hash
        assert ch.startswith("tch_")
        assert len(ch) == 36

    def test_modify_title_keeps_fingerprint_changes_content_hash(self):
        """门槛 16（核心）：改 title/steps → fingerprint 不变，content_hash 变"""
        raw1 = {
            "module": "登录",
            "title": "验证手机号为空",
            "precondition": "",
            "expected": "e",
            "type": "functional",
            "priority": "P1",
            "steps": [{"action": "a"}],
        }
        raw2 = {
            "module": "登录",
            "title": "验证未输入手机号时无法登录",
            "precondition": "",
            "expected": "e2",
            "type": "functional",
            "priority": "P1",
            "steps": [{"action": "b"}],
        }
        r1 = _build(_candidate(raw=raw1))
        r2 = _build(_candidate(raw=raw2))
        assert r1.test_case.fingerprint == r2.test_case.fingerprint  # 身份不变
        assert r1.test_case.content_hash != r2.test_case.content_hash  # 内容变

    def test_fingerprint_matches_formula(self):
        """fingerprint 与公式一致（version_id | generation_mode | sorted(test_point_ids)）"""
        r = _build(_candidate(mode=GenerationMode.LLM))
        expected = compute_testcase_fingerprint(version_id=VERSION_ID, generation_mode="llm", test_point_ids=[TP_ID])
        assert r.test_case.fingerprint == expected

    def test_content_hash_matches_formula(self):
        r = _build(_candidate())
        tc = r.test_case
        expected = compute_testcase_content_hash(
            title=tc.title,
            precondition=tc.precondition,
            steps_text=tc.render_steps_text(),
            expected=tc.expected,
            type=tc.type.value,
            priority=tc.priority.value,
        )
        assert tc.content_hash == expected

    def test_different_mode_different_fingerprint(self):
        """generation_mode 参与身份：同 TestPoint 不同 mode → 不同 fingerprint"""
        r_code = _build(_candidate(mode=GenerationMode.CODE))
        r_llm = _build(_candidate(mode=GenerationMode.LLM))
        assert r_code.test_case.fingerprint != r_llm.test_case.fingerprint

    def test_fingerprint_independent_of_run_id(self):
        """fingerprint 不含 run_id：不同 run 同 version+mode+tp → 同 fingerprint（跨 run 幂等）"""
        r1 = _build(_candidate(), display_id="TC_001")
        c2 = _candidate()
        r2 = validate_and_build(
            c2, tp=_tp(), run_id="01ARZ3NDEKTSV4RRFFQ69G5F99", version_id=VERSION_ID, display_id="TC_001"
        )
        assert r1.test_case.fingerprint == r2.test_case.fingerprint


# ============================================================
# provenance / confidence_level 派生
# ============================================================


class TestProvenanceConfidence:
    def test_code_mode_provenance_strategy(self):
        r = _build(_candidate(mode=GenerationMode.CODE))
        assert r.test_case.provenance == Provenance.STRATEGY

    def test_hybrid_mode_provenance_strategy(self):
        r = _build(_candidate(mode=GenerationMode.HYBRID))
        assert r.test_case.provenance == Provenance.STRATEGY

    def test_llm_mode_provenance_llm(self):
        r = _build(_candidate(mode=GenerationMode.LLM))
        assert r.test_case.provenance == Provenance.LLM

    def test_code_mode_confidence_high(self):
        r = _build(_candidate(mode=GenerationMode.CODE))
        assert r.test_case.confidence_level == ConfidenceLevel.HIGH

    def test_llm_mode_confidence_medium(self):
        r = _build(_candidate(mode=GenerationMode.LLM))
        assert r.test_case.confidence_level == ConfidenceLevel.MEDIUM


# ============================================================
# data_plan 承载 + display_id
# ============================================================


class TestDataPlanAndDisplay:
    def test_data_plan_carried(self):
        dp = [
            DataPlanItem(
                field="age",
                strategy="boundary",
                value=1,
                source="strategy_params",
                generator="code",
                expected_valid=True,
            )
        ]
        r = _build(_candidate(mode=GenerationMode.CODE, data_plan=dp))
        assert r.test_case.data_plan == dp

    def test_display_id_assigned(self):
        r = _build(_candidate(), display_id="TC_042")
        assert r.test_case.display_id == "TC_042"

    def test_run_id_assigned(self):
        r = _build(_candidate())
        assert r.test_case.run_id == RUN_ID


# ============================================================
# 去重
# ============================================================


class TestDedupe:
    def test_dedupe_by_fingerprint(self):
        """同 fingerprint 只保留首个"""
        c = _candidate()
        tc1 = _build(c, display_id="TC_001").test_case
        tc2 = _build(_candidate(), display_id="TC_002").test_case
        # 两者 fingerprint 相同（同 version+mode+tp）
        assert tc1.fingerprint == tc2.fingerprint
        deduped, issues = dedupe_cases([tc1, tc2])
        assert len(deduped) == 1
        assert any("重复 fingerprint" in i for i in issues)

    def test_dedupe_keeps_distinct(self):
        tc1 = _build(_candidate(mode=GenerationMode.CODE), display_id="TC_001").test_case
        tc2 = _build(_candidate(mode=GenerationMode.LLM), display_id="TC_002").test_case
        deduped, issues = dedupe_cases([tc1, tc2])
        assert len(deduped) == 2
        assert issues == []
