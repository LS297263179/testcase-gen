"""V2 Step 5 验收测试 - 18 条门槛逐条对应（12 主 + 6 用户补充）。

对应 docs/v2/PROGRESS.md §9 与 plan §六。全 mock，不依赖真实 API。
"""

import json
import re

from core.schemas import (
    DataType,
    FieldSpec,
    GenerationConfig,
    GenerationScope,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    Run,
    RunCounts,
    RunStatus,
    SourceType,
    TestCaseStatus,
    TestCaseType,
    TestDimension,
    TestPoint,
)
from core.v2 import repository as repo
from core.v2.db import v2_conn, v2_read_conn
from core.v2.ddl import get_schema_version
from core.v2.strategy.orchestrator import apply_strategy_engine
from core.v2.tc_orchestrator import FullSynthesisResult, generate_test_cases_full, synthesize_test_cases

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class FakeClient:
    def __init__(self, case_response=None, pattern_response='{"sample": "13800138000"}'):
        self.case_response = case_response or json.dumps(
            {
                "title": "退出登录后回到登录页",
                "precondition": "用户已登录",
                "steps": [
                    {"action": "点击退出按钮", "expected": "会话清除"},
                    {"action": "观察跳转", "expected": "回到登录页"},
                ],
                "expected": "用户被登出并跳转登录页",
                "type": "functional",
                "priority": "P1",
                "remark": "",
            },
            ensure_ascii=False,
        )
        self.pattern_response = pattern_response
        self.pattern_calls = 0

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        if "正则表达式" in system_prompt or "测试数据构造专家" in system_prompt:
            self.pattern_calls += 1
            return self.pattern_response
        if "单个原子需求项" in system_prompt or "跨需求项" in system_prompt:
            return '{"test_points": []}'
        return self.case_response


def _setup(v2_db, fields=None, permissions=None) -> tuple[Run, RequirementVersion, list[RequirementItem]]:
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="Step5 验收需求", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    v2_db.save_version(ver)
    if fields is None:
        fields = [
            FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True)
        ]
    items = [
        RequirementItem(
            version_id=ver.id,
            seq=1,
            type=RequirementItemType.DATA_FIELD,
            module="用户注册",
            statement="字段约束",
            fields=fields,
            permissions=permissions or [],
            confidence=0.9,
        )
    ]
    for it in items:
        v2_db.save_item(it)
    doc.latest_version_id = ver.id
    v2_db.save_doc(doc)
    cfg = GenerationConfig(
        model_provider="openai",
        model_name="gpt-x",
        temperature=0.3,
        prompt_version="test-point-generator-v1",
        generator_version="tp-gen-v1",
    )
    v2_db.save_generation_config(cfg)
    run = Run(
        user_id=USER_ID,
        doc_id=doc.id,
        requirement_version_id=ver.id,
        generation_config_id=cfg.id,
        status=RunStatus.DONE,
        counts=RunCounts(items=1, points=0),
    )
    v2_db.save_run(run)
    return run, ver, items


def _insert_llm_tp(v2_db, run, ver, item, title="退出登录") -> TestPoint:
    tp = TestPoint(
        run_id=run.id,
        version_id=ver.id,
        item_ids=[item.id],
        module="登录",
        subcategory="功能",
        title=title,
        description="验证退出登录功能",
        dimension=TestDimension.FUNCTIONAL,
        priority=Priority.P1,
        provenance=Provenance.LLM,
        generation_scope=GenerationScope.ITEM,
    )
    v2_db.save_test_point(tp)
    return tp


# ============================================================
# 门槛 1：test_point_ids 全部指向真实 TestPoint
# ============================================================


def test_01_test_point_ids_reference_real_points(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    assert result.cases
    for c in result.cases:
        for tp_id in c.test_point_ids:
            assert repo.get_test_point(tp_id) is not None


# ============================================================
# 门槛 2：LLM TP → 1 TC；Strategy TP → 1 TC（含真实数据）
# ============================================================


def test_02_one_to_one_derivation(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)  # 6 strategy TP
    _insert_llm_tp(v2_db, run, ver, items[0])  # 1 LLM TP
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    # 7 TP → 7 TC（1:1）
    assert len(result.cases) == 7
    # 每个 case 恰好关联 1 个 TestPoint
    for c in result.cases:
        assert len(c.test_point_ids) == 1
    # strategy 用例含真实测试数据
    strategy_cases = [c for c in result.cases if c.generation_mode.value == "code"]
    assert strategy_cases
    for c in strategy_cases:
        assert c.data_plan and c.data_plan[0].value is not None


# ============================================================
# 门槛 3：steps 非空、seq 连续、action 非空
# ============================================================


def test_03_steps_wellformed(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    for c in result.cases:
        assert c.steps  # 非空
        seqs = [s.seq for s in c.steps]
        assert seqs == list(range(1, len(seqs) + 1))  # 连续 1..N
        for s in c.steps:
            assert s.action and s.action.strip()  # action 非空


# ============================================================
# 门槛 4：type 枚举合法（LLM 乱写被兜底）
# ============================================================


def test_04_type_enum_fallback(v2_db):
    run, ver, items = _setup(v2_db)
    _insert_llm_tp(v2_db, run, ver, items[0])
    bad = json.dumps(
        {
            "title": "t",
            "precondition": "",
            "steps": [{"action": "a"}],
            "expected": "e",
            "type": "乱写类型",
            "priority": "P1",
        },
        ensure_ascii=False,
    )
    result = synthesize_test_cases(FakeClient(case_response=bad), run_id=run.id)
    assert len(result.cases) == 1
    assert isinstance(result.cases[0].type, TestCaseType)  # 兜底为合法枚举


# ============================================================
# 门槛 5：fingerprint 非空、格式正确、UNIQUE 约束生效
# ============================================================


def test_05_fingerprint_format_and_unique(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    for c in result.cases:
        assert c.fingerprint and c.fingerprint.startswith("tc_")
        assert len(c.fingerprint) == 35
        int(c.fingerprint[3:], 16)
    # UNIQUE 索引存在
    with v2_read_conn() as conn:
        idxs = conn.execute("PRAGMA index_list(test_cases)").fetchall()
    fp_idx = [i for i in idxs if i["name"] == "ux_test_cases_fingerprint"]
    assert fp_idx and fp_idx[0]["unique"] == 1


# ============================================================
# 门槛 6：幂等重跑不产生重复
# ============================================================


def test_06_idempotent_rerun(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)
    synthesize_test_cases(FakeClient(), run_id=run.id)
    ids1 = {c.id for c in repo.list_test_cases(run.id)}
    synthesize_test_cases(FakeClient(), run_id=run.id)
    cases2 = repo.list_test_cases(run.id)
    assert len(cases2) == len(ids1)  # 无重复
    assert {c.id for c in cases2} == ids1  # ULID 复用


# ============================================================
# 门槛 7：Run 复用 + 状态机终态 DONE
# ============================================================


def test_07_run_reused_and_done(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    assert result.run_id == run.id  # 复用同一 Run，未新建
    got = repo.get_run(run.id)
    assert got.status == RunStatus.DONE
    assert got.counts.cases == 6


# ============================================================
# 门槛 8：TestCase 状态机 VALIDATED / VALIDATION_FAILED
# ============================================================


def test_08_status_validated(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    assert all(c.status == TestCaseStatus.VALIDATED for c in result.cases)


def test_08b_status_validation_failed(v2_db):
    """不可修复（steps 空）→ VALIDATION_FAILED"""
    run, ver, items = _setup(v2_db)
    _insert_llm_tp(v2_db, run, ver, items[0])
    bad = json.dumps(
        {"title": "t", "precondition": "", "steps": [], "expected": "e", "type": "functional", "priority": "P1"},
        ensure_ascii=False,
    )
    result = synthesize_test_cases(FakeClient(case_response=bad), run_id=run.id)
    assert len(result.cases) == 1
    assert result.cases[0].status == TestCaseStatus.VALIDATION_FAILED


# ============================================================
# 门槛 9：test_case_points M:N + 删 TestCase 不级联删 TestPoint
# ============================================================


def test_09_link_and_no_reverse_cascade(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    c = result.cases[0]
    tp_id = c.test_point_ids[0]
    # 删除 TestCase → test_case_points 链接级联删，但 TestPoint 保留
    with v2_conn() as conn:
        conn.execute("DELETE FROM test_cases WHERE id = ?", (c.id,))
    assert repo.get_test_point(tp_id) is not None  # TestPoint 未被级联删
    with v2_read_conn() as conn:
        links = conn.execute("SELECT * FROM test_case_points WHERE test_case_id = ?", (c.id,)).fetchall()
    assert links == []  # 链接已级联删


# ============================================================
# 门槛 10：数据来源优先级 + 复杂正则 LLM 兜底
# ============================================================


def test_10_data_source_priority_and_llm_fallback(v2_db):
    # phone 复杂正则 → valid_pattern 需 LLM 兜底
    fields = [
        FieldSpec(
            name="phone",
            label="手机号",
            data_type=DataType.PHONE,
            pattern=r"^1[3-9]\d{9}$",
            nullable=True,
            example=None,
        )
    ]
    run, ver, items = _setup(v2_db, fields=fields)
    apply_strategy_engine(run.id, ver.id)
    client = FakeClient(pattern_response='{"sample": "13800138000"}')
    result = synthesize_test_cases(client, run_id=run.id)
    assert client.pattern_calls >= 1  # 复杂正则落了 LLM 兜底
    # 至少一个用例的数据来自 LLM
    llm_data_cases = [c for c in result.cases if any(d.generator == "llm" for d in c.data_plan)]
    assert llm_data_cases


# ============================================================
# 门槛 11：V1 零回归 + schema_version=5
# ============================================================


def test_11_v1_untouched_and_schema_v5(v2_db):
    from core import db as v1_db

    assert v1_db._DB_PATH.endswith("data.db")
    from web.data import TEST_POINTS_PROMPT

    assert "资深测试工程师" in TEST_POINTS_PROMPT
    from core.generator import ANALYSIS_PROMPT, MODULE_PROMPT, SYSTEM_PROMPT

    assert ANALYSIS_PROMPT and MODULE_PROMPT and SYSTEM_PROMPT
    assert get_schema_version() == 5


# ============================================================
# 门槛 12：一站式入口 Step 3+4+5
# ============================================================


def test_12_full_pipeline(v2_db):
    # 建 IR（Doc/Version/Item）后走一站式入口（Step 3 空 + Step 4 strategy + Step 5 合成）
    run, ver, items = _setup(v2_db)
    result = generate_test_cases_full(FakeClient(), version_id=ver.id, user_id=USER_ID)
    assert isinstance(result, FullSynthesisResult)
    assert result.run_id
    assert len(result.cases) == 6
    assert result.strategy_obligation_coverage == 1.0


# ============================================================
# 门槛 13：TestDataPlanner 数据通过 FieldSpec 约束验证
# ============================================================


def test_13_data_satisfies_fieldspec(v2_db):
    fields = [
        FieldSpec(
            name="status", label="状态", data_type=DataType.ENUM, enum_values=["active", "disabled"], nullable=True
        ),
    ]
    run, ver, items = _setup(v2_db, fields=fields)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    for c in result.cases:
        for d in c.data_plan:
            if d.strategy == "equivalence" and d.expected_valid and d.source == "enum":
                assert d.value in ["active", "disabled"]  # 合法枚举值确实在集合内
            if d.strategy == "equivalence" and not d.expected_valid and d.value == "INVALID_VALUE":
                assert d.value not in ["active", "disabled"]


def test_13b_boundary_data_within_range(v2_db):
    run, ver, items = _setup(v2_db)  # age min=1 max=100
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    for c in result.cases:
        for d in c.data_plan:
            if d.strategy == "boundary" and d.expected_valid:
                assert 1 <= d.value <= 100  # 合法边界值在 [min,max] 内


# ============================================================
# 门槛 14：复杂正则 LLM 数据经 re.fullmatch 验证
# ============================================================


def test_14_llm_pattern_data_verified(v2_db):
    fields = [
        FieldSpec(name="phone", label="手机号", data_type=DataType.PHONE, pattern=r"^1[3-9]\d{9}$", nullable=True)
    ]
    run, ver, items = _setup(v2_db, fields=fields)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(pattern_response='{"sample": "13800138000"}'), run_id=run.id)
    for c in result.cases:
        for d in c.data_plan:
            if d.generator == "llm" and d.expected_valid:
                assert re.fullmatch(r"^1[3-9]\d{9}$", str(d.value)) is not None  # 经代码验证匹配


def test_14b_llm_bad_pattern_rejected(v2_db):
    """LLM 给不匹配样例 → 代码拒绝 → 重试仍失败 → value=None（不入库错误数据）"""
    fields = [
        FieldSpec(name="phone", label="手机号", data_type=DataType.PHONE, pattern=r"^1[3-9]\d{9}$", nullable=True)
    ]
    run, ver, items = _setup(v2_db, fields=fields)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(pattern_response='{"sample": "bad"}'), run_id=run.id)
    for c in result.cases:
        for d in c.data_plan:
            if d.generator == "llm" and d.expected_valid:
                # 要么是 None（全部重试失败），要么绝不入库未通过验证的值
                assert d.value is None or re.fullmatch(r"^1[3-9]\d{9}$", str(d.value)) is not None


# ============================================================
# 门槛 15：同 TestPoint 同 Version 只对应一个 TestCase
# ============================================================


def test_15_unique_case_per_testpoint(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)
    synthesize_test_cases(FakeClient(), run_id=run.id)
    synthesize_test_cases(FakeClient(), run_id=run.id)  # 重跑
    cases = repo.list_test_cases_by_version(ver.id)
    fps = [c.fingerprint for c in cases]
    assert len(fps) == len(set(fps))  # fingerprint 无重复 → 每 TP 唯一 TC


# ============================================================
# 门槛 16：改 title/steps 不改 identity fingerprint
# ============================================================


def test_16_edit_content_keeps_identity(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    c = result.cases[0]
    fp_before = c.fingerprint
    ch_before = c.content_hash
    # 模拟人工修改内容（Step 9 场景）
    c.title = "人工修改后的标题"
    c.steps[0].action = "人工修改后的步骤"
    from core.v2.fingerprint import compute_testcase_content_hash, compute_testcase_fingerprint

    fp_after = compute_testcase_fingerprint(
        version_id=ver.id, generation_mode=c.generation_mode.value, test_point_ids=c.test_point_ids
    )
    ch_after = compute_testcase_content_hash(
        title=c.title,
        precondition=c.precondition,
        steps_text=c.render_steps_text(),
        expected=c.expected,
        type=c.type.value,
        priority=c.priority.value,
    )
    assert fp_after == fp_before  # 身份不变
    assert ch_after != ch_before  # 内容变


# ============================================================
# 门槛 17：TestCase → TestPoint → CoverageObligation 反查
# ============================================================


def test_17_reverse_lookup_obligation(v2_db):
    run, ver, items = _setup(v2_db)
    apply_strategy_engine(run.id, ver.id)
    result = synthesize_test_cases(FakeClient(), run_id=run.id)
    c = result.cases[0]
    tp = repo.get_test_point(c.test_point_ids[0])
    assert tp.obligation_id
    ob = repo.get_obligation(tp.obligation_id)
    assert ob is not None
    # 未双写：obligation_coverage 不含 TestCase 目标（仅 TestPoint）
    from core.schemas import TargetType

    with v2_read_conn() as conn:
        rows = conn.execute("SELECT target_type FROM obligation_coverage WHERE obligation_id = ?", (ob.id,)).fetchall()
    assert all(r["target_type"] == TargetType.TESTPOINT.value for r in rows)


# ============================================================
# 门槛 18：VALIDATION_FAILED 保存明确 validation_errors
# ============================================================


def test_18_validation_errors_saved(v2_db):
    run, ver, items = _setup(v2_db)
    _insert_llm_tp(v2_db, run, ver, items[0])
    bad = json.dumps(
        {"title": "", "precondition": "", "steps": [], "expected": "", "type": "functional", "priority": "P1"},
        ensure_ascii=False,
    )
    result = synthesize_test_cases(FakeClient(case_response=bad), run_id=run.id)
    c = result.cases[0]
    assert c.status == TestCaseStatus.VALIDATION_FAILED
    assert c.validation_errors  # 非空
    # 持久化后仍可读出 validation_errors
    got = repo.get_test_case(c.id)
    assert got.validation_errors == c.validation_errors
    assert any("steps 为空" in e for e in got.validation_errors)
