"""V2 Step 5 用例合成编排测试 - 端到端、Run 复用、幂等、关联表、GenerationConfig 审计、一站式入口。

用 apply_strategy_engine 预置 strategy TestPoint + obligation；手动插入 LLM TestPoint。
全 mock，不依赖真实 API。
"""

import json

from core.schemas import (
    DataType,
    FieldSpec,
    GenerationConfig,
    GenerationScope,
    PermissionRule,
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
    Technique,
    TestCaseStatus,
    TestDimension,
    TestPoint,
)
from core.v2 import repository as repo
from core.v2.strategy.orchestrator import apply_strategy_engine
from core.v2.tc_orchestrator import (
    GENERATOR_VERSION,
    FullSynthesisResult,
    SynthesisResult,
    generate_test_cases_full,
    synthesize_test_cases,
)
from core.v2.tc_prompts import PROMPT_VERSIONS_COMBINED

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class FakeClient:
    """按 system_prompt 区分：正则数据构造 vs 用例合成。"""

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
        self.synth_calls = 0
        self.pattern_calls = 0

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        if "正则表达式" in system_prompt or "测试数据构造专家" in system_prompt:
            self.pattern_calls += 1
            return self.pattern_response
        self.synth_calls += 1
        return self.case_response


def _setup(
    v2_db, *, with_permission=False, with_pattern=False
) -> tuple[Run, RequirementVersion, list[RequirementItem]]:
    """预置 Doc + Version + items + Run（status=DONE，模拟 Step 3/4 已结束）。"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="Step5 测试需求", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    v2_db.save_version(ver)

    fields = [FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True)]
    if with_pattern:
        fields.append(
            FieldSpec(name="phone", label="手机号", data_type=DataType.PHONE, pattern=r"^1[3-9]\d{9}$", nullable=True)
        )
    permissions = (
        [PermissionRule(role="admin", resource="order", action="refund", allowed=True)] if with_permission else []
    )

    items = [
        RequirementItem(
            version_id=ver.id,
            seq=1,
            type=RequirementItemType.DATA_FIELD,
            module="用户注册",
            statement="年龄 1-100",
            fields=fields,
            permissions=permissions,
            confidence=0.9,
        ),
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
        counts=RunCounts(items=len(items), points=0),
    )
    v2_db.save_run(run)
    return run, ver, items


def _insert_llm_testpoint(v2_db, run, ver, item, title="退出登录") -> TestPoint:
    """手动插入一个 LLM TestPoint（模拟 Step 3 产物）。"""
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
# 端到端：strategy TestPoint → CODE 用例
# ============================================================


class TestSynthesizeStrategy:
    def test_boundary_points_to_code_cases(self, v2_db):
        """age(min=1,max=100) → 6 boundary TestPoint → 6 CODE 用例"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)  # 派生 strategy TestPoint
        client = FakeClient()
        result = synthesize_test_cases(client, run_id=run.id, version_id=ver.id)
        assert isinstance(result, SynthesisResult)
        assert len(result.cases) == 6
        assert result.code_count == 6
        assert result.validated_count == 6
        assert result.failed_count == 0
        assert client.synth_calls == 0  # 模板合成不调 LLM

    def test_strategy_case_has_real_data(self, v2_db):
        """strategy 用例含具体测试数据（data_plan 非空 + steps 有数据）"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        result = synthesize_test_cases(FakeClient(), run_id=run.id)
        c = result.cases[0]
        assert c.data_plan  # 非空
        assert c.data_plan[0].generator == "code"
        assert c.status == TestCaseStatus.VALIDATED

    def test_permission_point_to_case(self, v2_db):
        """permission TestPoint → PERMISSION 类型用例"""
        run, ver, items = _setup(v2_db, with_permission=True)
        apply_strategy_engine(run.id, ver.id)
        result = synthesize_test_cases(FakeClient(), run_id=run.id)
        perm_cases = [c for c in result.cases if c.type.value == "permission"]
        assert len(perm_cases) == 1
        assert "admin" in perm_cases[0].precondition


# ============================================================
# LLM TestPoint → LLM 用例
# ============================================================


class TestSynthesizeLLM:
    def test_llm_point_to_llm_case(self, v2_db):
        run, ver, items = _setup(v2_db)
        _insert_llm_testpoint(v2_db, run, ver, items[0])
        client = FakeClient()
        result = synthesize_test_cases(client, run_id=run.id)
        assert len(result.cases) == 1
        assert result.cases[0].generation_mode.value == "llm"
        assert result.llm_count == 1
        assert client.synth_calls == 1
        assert result.cases[0].provenance == Provenance.LLM

    def test_mixed_strategy_and_llm(self, v2_db):
        """合流：strategy（CODE）+ LLM（LLM）两种 provenance 都能合成"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)  # 6 strategy
        _insert_llm_testpoint(v2_db, run, ver, items[0])  # 1 LLM
        result = synthesize_test_cases(FakeClient(), run_id=run.id)
        assert len(result.cases) == 7
        assert result.code_count == 6
        assert result.llm_count == 1

    def test_llm_synthesis_failure_skipped(self, v2_db):
        """LLM 合成失败（坏 JSON）→ 跳过该 TestPoint，不产空壳用例"""
        run, ver, items = _setup(v2_db)
        _insert_llm_testpoint(v2_db, run, ver, items[0])
        client = FakeClient(case_response="这不是 JSON")
        result = synthesize_test_cases(client, run_id=run.id)
        assert len(result.cases) == 0
        assert any("合成失败" in w for w in result.warnings)


# ============================================================
# Run 复用 + 状态机 + counts
# ============================================================


class TestRunReuse:
    def test_run_status_done_after(self, v2_db):
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        synthesize_test_cases(FakeClient(), run_id=run.id)
        got = repo.get_run(run.id)
        assert got.status == RunStatus.DONE

    def test_run_counts_cases_updated(self, v2_db):
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        synthesize_test_cases(FakeClient(), run_id=run.id)
        got = repo.get_run(run.id)
        assert got.counts.cases == 6
        assert got.counts.points == 6  # 保留 Step 4 的 points 计数

    def test_same_run_reused_not_new(self, v2_db):
        """Step 5 复用同一 run_id，不新建 Run"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        result = synthesize_test_cases(FakeClient(), run_id=run.id)
        assert result.run_id == run.id
        for c in result.cases:
            assert c.run_id == run.id

    def test_generation_config_augmented(self, v2_db):
        """GenerationConfig 追加 Step 5 prompt/generator 版本（审计诚实）"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        synthesize_test_cases(FakeClient(), run_id=run.id)
        cfg = repo.get_generation_config(run.generation_config_id)
        assert PROMPT_VERSIONS_COMBINED in cfg.prompt_version
        assert GENERATOR_VERSION in cfg.generator_version

    def test_generation_config_augment_idempotent(self, v2_db):
        """重复运行不重复追加 prompt 版本"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        synthesize_test_cases(FakeClient(), run_id=run.id)
        synthesize_test_cases(FakeClient(), run_id=run.id)
        cfg = repo.get_generation_config(run.generation_config_id)
        assert cfg.prompt_version.count("test-case-synthesizer-v1") == 1


# ============================================================
# 边界情况
# ============================================================


class TestEdgeCases:
    def test_run_not_found(self, v2_db):
        result = synthesize_test_cases(FakeClient(), run_id="01ARZ3NDEKTSV4RRFFQ69G5ZZZ")
        assert result.run_id == "01ARZ3NDEKTSV4RRFFQ69G5ZZZ"
        assert any("Run 不存在" in i for i in result.issues)

    def test_no_testpoints(self, v2_db):
        """Run 无 TestPoint（未跑 Step 3/4）→ 返回 issue，不崩溃"""
        run, ver, items = _setup(v2_db)
        result = synthesize_test_cases(FakeClient(), run_id=run.id)
        assert result.cases == []
        assert any("无 TestPoint" in i for i in result.issues)
        assert repo.get_run(run.id).status == RunStatus.DONE

    def test_version_id_defaults_from_run(self, v2_db):
        """version_id 缺省时从 Run 取"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        result = synthesize_test_cases(FakeClient(), run_id=run.id)  # 不传 version_id
        assert result.version_id == ver.id


# ============================================================
# 幂等 + 关联表 + display_id
# ============================================================


class TestIdempotentAndLinks:
    def test_idempotent_rerun_no_duplicates(self, v2_db):
        """门槛 6：同 run 重跑 → fingerprint 幂等复用旧 ULID，不产生重复"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        synthesize_test_cases(FakeClient(), run_id=run.id)
        ids1 = {c.id for c in repo.list_test_cases(run.id)}
        synthesize_test_cases(FakeClient(), run_id=run.id)
        cases2 = repo.list_test_cases(run.id)
        assert len(cases2) == 6  # 仍是 6，无重复
        ids2 = {c.id for c in cases2}
        assert ids1 == ids2  # ULID 复用

    def test_test_case_points_links(self, v2_db):
        """门槛 9：test_case_points M:N 关联写入正确"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        result = synthesize_test_cases(FakeClient(), run_id=run.id)
        for c in result.cases:
            got = repo.get_test_case(c.id)
            assert len(got.test_point_ids) == 1
            # 关联的 TestPoint 真实存在
            assert repo.get_test_point(got.test_point_ids[0]) is not None

    def test_display_id_stable_across_rerun(self, v2_db):
        """display_id 跨重跑稳定（按 TestPoint.id 排序分配）"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        synthesize_test_cases(FakeClient(), run_id=run.id)
        dids1 = sorted(c.display_id for c in repo.list_test_cases(run.id))
        synthesize_test_cases(FakeClient(), run_id=run.id)
        dids2 = sorted(c.display_id for c in repo.list_test_cases(run.id))
        assert dids1 == dids2
        assert dids1[0] == "TC_001"

    def test_reverse_lookup_obligation(self, v2_db):
        """门槛 17：TestCase → TestPoint → CoverageObligation 反查（间接继承，非双写）"""
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        result = synthesize_test_cases(FakeClient(), run_id=run.id)
        c = result.cases[0]
        tp = repo.get_test_point(c.test_point_ids[0])
        assert tp.obligation_id is not None
        ob = repo.get_obligation(tp.obligation_id)
        assert ob is not None
        assert ob.technique == Technique.BOUNDARY_VALUE

    def test_list_by_version(self, v2_db):
        run, ver, items = _setup(v2_db)
        apply_strategy_engine(run.id, ver.id)
        synthesize_test_cases(FakeClient(), run_id=run.id)
        cases = repo.list_test_cases_by_version(ver.id)
        assert len(cases) == 6


# ============================================================
# 一站式入口 Step 3+4+5
# ============================================================


class TestFullPipeline:
    def test_full_pipeline_produces_cases(self, v2_db):
        """generate_test_cases_full：Step 3（空）+ Step 4（strategy）+ Step 5（用例）"""
        run, ver, items = _setup(v2_db)
        # 直接用 version 走一站式（Step 3 Phase A 返回空 → 仅 Step 4 strategy 点）
        client = FakeClient()
        # Step 3 需要 Phase A 响应；FakeClient 对测试点 prompt 返回 case_response，需专门处理
        result = _run_full(client, ver.id)
        assert isinstance(result, FullSynthesisResult)
        assert result.run_id
        assert len(result.cases) == 6  # 6 boundary 用例
        assert result.strategy_obligation_coverage == 1.0

    def test_full_pipeline_run_state_done(self, v2_db):
        run, ver, items = _setup(v2_db)
        result = _run_full(FakeClient(), ver.id)
        assert repo.get_run(result.run_id).status == RunStatus.DONE


class _FullStep3Client(FakeClient):
    """一站式入口用：Step 3 测试点 prompt 返回空，Step 5 合成返回用例。"""

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        if "单个原子需求项" in system_prompt or "跨需求项" in system_prompt:
            return '{"test_points": []}'  # Step 3 Phase A/B 返回空
        return super().chat(system_prompt, user_prompt, images, max_tokens)


def _run_full(client, version_id):
    return generate_test_cases_full(_FullStep3Client(), version_id=version_id, user_id=USER_ID)
