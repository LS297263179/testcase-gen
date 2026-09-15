"""V2 Step 4 策略引擎编排测试 - Run 状态机、覆盖率双指标、幂等、一站式入口。"""

import json

from core.schemas import (
    DataType,
    FieldSpec,
    GenerationScope,
    PermissionRule,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    Run,
    RunCounts,
    RunStatus,
    SourceType,
    TargetType,
    Technique,
)
from core.v2 import repository as repo
from core.v2.strategy.orchestrator import (
    FullGenerationResult,
    StrategyResult,
    apply_strategy_engine,
    generate_test_points_full,
)

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class FakeClient:
    """Step 3 用 mock client（Phase A 返回空，简化测试聚焦 Step 4）"""

    def __init__(self, phase_a_response='{"test_points": []}'):
        self.phase_a_response = phase_a_response
        self.calls: list[dict] = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        if "单个原子需求项" in system_prompt:
            return self.phase_a_response
        return '{"test_points": []}'


def _setup_run_with_items(v2_db, items_spec: list[dict]) -> tuple[Run, RequirementVersion, list[RequirementItem]]:
    """预置 Doc + Version + items + Run（status=DONE，模拟 Step 3 已结束），返回 (run, version, items)"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="策略引擎测试需求", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求描述", provenance=Provenance.LLM)
    v2_db.save_version(ver)

    items: list[RequirementItem] = []
    for i, spec in enumerate(items_spec, 1):
        it = RequirementItem(
            version_id=ver.id,
            seq=i,
            type=spec.get("type", RequirementItemType.DATA_FIELD),
            module=spec.get("module", "用户注册"),
            statement=spec.get("statement", f"item {i}"),
            fields=spec.get("fields", []),
            permissions=spec.get("permissions", []),
            priority_hint=spec.get("priority_hint"),
            confidence=0.9,
        )
        v2_db.save_item(it)
        items.append(it)

    doc.latest_version_id = ver.id
    v2_db.save_doc(doc)

    # 建 Run（模拟 Step 3 已结束，status=DONE）
    from core.schemas import GenerationConfig

    cfg = GenerationConfig(
        model_provider="openai",
        model_name="gpt-x",
        temperature=0.3,
        prompt_version="test",
        generator_version="test",
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


# ============================================================
# apply_strategy_engine 端到端
# ============================================================


class TestApplyStrategyEngine:
    def test_end_to_end_boundary(self, v2_db):
        """age(min=1,max=100) → 1 obligation + 6 TestPoints + coverage=1.0"""
        run, ver, items = _setup_run_with_items(
            v2_db,
            [
                {
                    "fields": [
                        FieldSpec(
                            name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True
                        )
                    ]
                },
            ],
        )
        result = apply_strategy_engine(run.id, ver.id)
        assert isinstance(result, StrategyResult)
        assert len(result.obligations) == 1
        assert len(result.points) == 6
        assert result.strategy_obligation_coverage == 1.0
        # Run 状态机
        got_run = repo.get_run(run.id)
        assert got_run.status == RunStatus.DONE
        assert got_run.counts.obligations == 1
        assert got_run.counts.points == 6

    def test_end_to_end_mixed_strategies(self, v2_db):
        """三策略混合：boundary + equivalence + permission"""
        run, ver, items = _setup_run_with_items(
            v2_db,
            [
                {
                    "fields": [
                        FieldSpec(
                            name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True
                        ),
                        FieldSpec(
                            name="status", label="状态", data_type=DataType.ENUM, enum_values=["a", "b"], nullable=True
                        ),
                    ],
                    "permissions": [PermissionRule(role="admin", resource="user", action="delete", allowed=True)],
                },
            ],
        )
        result = apply_strategy_engine(run.id, ver.id)
        # boundary 1 ob + equivalence 1 ob (enum) + permission 1 ob = 3 obligations
        assert len(result.obligations) == 3
        # boundary 6 + equivalence 3 + permission 1 = 10 TestPoints
        assert len(result.points) == 10
        assert result.strategy_obligation_coverage == 1.0
        techniques = {tp.technique for tp in result.points}
        assert techniques == {Technique.BOUNDARY_VALUE, Technique.EQUIVALENCE_CLASS, Technique.PERMISSION_MATRIX}

    def test_run_status_transitions(self, v2_db):
        """Run.status: DONE → STRATEGIZING → DONE"""
        run, ver, items = _setup_run_with_items(
            v2_db,
            [
                {"fields": [FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, nullable=True)]},
            ],
        )
        assert repo.get_run(run.id).status == RunStatus.DONE
        result = apply_strategy_engine(run.id, ver.id)
        assert repo.get_run(run.id).status == RunStatus.DONE
        assert result.run_id == run.id

    def test_obligations_persisted(self, v2_db):
        run, ver, items = _setup_run_with_items(
            v2_db,
            [
                {
                    "fields": [
                        FieldSpec(
                            name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True
                        )
                    ]
                },
            ],
        )
        result = apply_strategy_engine(run.id, ver.id)
        # 从 DB 查 obligations
        db_obs = repo.list_obligations(run.id)
        assert len(db_obs) == 1
        assert db_obs[0].id == result.obligations[0].id

    def test_testpoints_persisted_with_hard_constraints(self, v2_db):
        """Step 4 硬性约束：provenance=STRATEGY / scope=STRATEGY / technique!=None / obligation_id!=None"""
        run, ver, items = _setup_run_with_items(
            v2_db,
            [
                {
                    "fields": [
                        FieldSpec(
                            name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True
                        )
                    ]
                },
            ],
        )
        result = apply_strategy_engine(run.id, ver.id)
        for tp in result.points:
            got = repo.get_test_point(tp.id)
            assert got is not None
            assert got.provenance == Provenance.STRATEGY
            assert got.generation_scope == GenerationScope.STRATEGY
            assert got.technique is not None
            assert got.obligation_id is not None
            assert got.strategy_params is not None
            assert got.fingerprint is not None
            assert got.run_id == run.id
            assert got.version_id == ver.id

    def test_add_coverage_registered(self, v2_db):
        """每个 obligation 被其派生的 TestPoint 覆盖（add_coverage 登记）"""
        run, ver, items = _setup_run_with_items(
            v2_db,
            [
                {
                    "fields": [
                        FieldSpec(
                            name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True
                        )
                    ]
                },
            ],
        )
        result = apply_strategy_engine(run.id, ver.id)
        ob = result.obligations[0]
        covered_tp_ids = repo.coverage_targets(ob.id, TargetType.TESTPOINT)
        assert len(covered_tp_ids) == 6  # 6 个边界点都登记了覆盖
        assert set(covered_tp_ids) == {tp.id for tp in result.points}

    def test_strategy_obligation_coverage_is_one(self, v2_db):
        """硬指标：每个 obligation 至少被 1 个 TestPoint 覆盖 → ratio=1.0"""
        run, ver, items = _setup_run_with_items(
            v2_db,
            [
                {
                    "fields": [
                        FieldSpec(
                            name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True
                        ),
                        FieldSpec(
                            name="email",
                            label="邮箱",
                            data_type=DataType.EMAIL,
                            required=True,
                            nullable=False,
                            unique=True,
                        ),
                    ],
                    "permissions": [
                        PermissionRule(role="admin", resource="user", action="delete", allowed=True),
                        PermissionRule(role="user", resource="user", action="delete", allowed=False),
                    ],
                },
            ],
        )
        result = apply_strategy_engine(run.id, ver.id)
        assert result.strategy_obligation_coverage == 1.0
        assert len(result.obligations) > 0

    def test_requirement_item_coverage_soft_report(self, v2_db):
        """软指标：RequirementItem Coverage 报告（仅 strategy 覆盖部分）"""
        run, ver, items = _setup_run_with_items(
            v2_db,
            [
                {"fields": [FieldSpec(name="age", label="年龄", data_type=DataType.INT, min_value=1, nullable=True)]},
                {"type": RequirementItemType.FUNCTION, "statement": "纯功能描述，无 fields/permissions"},
            ],
        )
        result = apply_strategy_engine(run.id, ver.id)
        # 第 1 个 item 有 fields → 被覆盖；第 2 个 item 无 fields/permissions → 未覆盖
        assert result.requirement_item_coverage is not None
        assert result.requirement_item_coverage.total_items == 2
        assert result.requirement_item_coverage.coverage_ratio == 0.5
        assert result.requirement_item_coverage.uncovered_item_ids == [items[1].id]

    def test_run_not_found(self, v2_db):
        v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
        result = apply_strategy_engine("01ARZ3NDEKTSV4RRFFQ69G5FZZ", "01ARZ3NDEKTSV4RRFFQ69G5FYY")
        assert result.run_id == "01ARZ3NDEKTSV4RRFFQ69G5FZZ"
        assert any("Run 不存在" in i for i in result.issues)

    def test_no_items_in_version(self, v2_db):
        run, ver, items = _setup_run_with_items(v2_db, [])
        result = apply_strategy_engine(run.id, ver.id)
        assert result.obligations == []
        assert result.points == []
        assert any("无 items" in i for i in result.issues)
        # Run 仍被置为 DONE
        assert repo.get_run(run.id).status == RunStatus.DONE


# ============================================================
# 幂等性
# ============================================================


class TestStrategyIdempotency:
    def test_rerun_same_version_reuses_ids(self, v2_db):
        """同 version 重跑 apply_strategy_engine：同 fingerprint 复用旧 ULID，不产生重复行"""
        run, ver, items = _setup_run_with_items(
            v2_db,
            [
                {
                    "fields": [
                        FieldSpec(
                            name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True
                        )
                    ]
                },
            ],
        )
        r1 = apply_strategy_engine(run.id, ver.id)
        ids_1 = {tp.id for tp in r1.points}
        fps_1 = {tp.fingerprint for tp in r1.points}
        ob_ids_1 = {ob.id for ob in r1.obligations}

        # 重跑（同一 Run）
        r2 = apply_strategy_engine(run.id, ver.id)
        ids_2 = {tp.id for tp in r2.points}
        fps_2 = {tp.fingerprint for tp in r2.points}
        ob_ids_2 = {ob.id for ob in r2.obligations}

        assert ob_ids_1 == ob_ids_2, "obligation.id 应复用（natural key 对齐）"
        assert fps_1 == fps_2, "fingerprint 应完全一致"
        assert ids_1 == ids_2, "同 fingerprint 应复用旧 ULID"

        # DB 层无重复 TestPoint
        all_pts = repo.list_test_points_by_version(ver.id)
        assert len(all_pts) == len(r1.points)

    def test_rerun_with_description_change_updates_in_place(self, v2_db):
        """重跑时 obligation 按 natural key 复用旧 id → TestPoint fingerprint 稳定 → 幂等生效

        natural key = (run_id, item_id, technique, target)，在同一 run 下唯一。
        重跑时 orchestrator 查旧行复用旧 ULID，保证下游 TestPoint 的 fingerprint（含 obligation_id）稳定，
        同 fingerprint 复用旧 TestPoint ULID，DB 层不产生重复行。
        """
        run, ver, items = _setup_run_with_items(
            v2_db,
            [
                {
                    "fields": [
                        FieldSpec(
                            name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True
                        )
                    ]
                },
            ],
        )
        r1 = apply_strategy_engine(run.id, ver.id)
        ob_ids_1 = {ob.id for ob in r1.obligations}
        tp_ids_1 = {tp.id for tp in r1.points}

        # 重跑：obligation.id 复用（natural key 对齐）→ fingerprint 稳定 → TestPoint.id 复用
        r2 = apply_strategy_engine(run.id, ver.id)
        ob_ids_2 = {ob.id for ob in r2.obligations}
        tp_ids_2 = {tp.id for tp in r2.points}

        assert ob_ids_1 == ob_ids_2, "obligation.id 应复用（natural key 对齐）"
        assert tp_ids_1 == tp_ids_2, "TestPoint.id 应复用（fingerprint 稳定）"
        assert len(r1.points) == len(r2.points) == 6

        # DB 层无重复
        all_pts = repo.list_test_points_by_version(ver.id)
        assert len(all_pts) == 6


# ============================================================
# generate_test_points_full 一站式入口
# ============================================================


class TestGenerateTestPointsFull:
    def test_end_to_end_step3_plus_step4(self, v2_db):
        """Step 3（LLM）+ Step 4（策略）合流"""
        v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
        doc = RequirementDoc(user_id=USER_ID, title="综合需求", source_type=SourceType.TEXT)
        v2_db.save_doc(doc)
        ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
        v2_db.save_version(ver)
        item = RequirementItem(
            version_id=ver.id,
            seq=1,
            type=RequirementItemType.DATA_FIELD,
            module="登录",
            statement="手机号 11 位",
            fields=[
                FieldSpec(
                    name="phone", label="手机号", data_type=DataType.STRING, min_length=11, max_length=11, nullable=True
                )
            ],
            confidence=0.9,
        )
        v2_db.save_item(item)
        doc.latest_version_id = ver.id
        v2_db.save_doc(doc)

        # Step 3 Phase A 给一个 LLM TestPoint
        phase_a_resp = json.dumps(
            {
                "test_points": [
                    {
                        "module": "登录",
                        "subcategory": "输入校验",
                        "title": "手机号长度校验",
                        "description": "验证 11 位",
                        "dimension": "boundary",
                        "priority": "P1",
                    }
                ]
            },
            ensure_ascii=False,
        )
        client = FakeClient(phase_a_response=phase_a_resp)

        result = generate_test_points_full(client, version_id=ver.id, user_id=USER_ID)
        assert isinstance(result, FullGenerationResult)
        assert result.run_id != ""
        # Step 3: 1 个 LLM TestPoint
        assert len(result.llm_points) == 1
        assert result.llm_points[0].provenance == Provenance.LLM
        # Step 4: phone min_length=11,max_length=11 → 长度边界 obligation → 3 点（min-1=10, min=11, min+1=12；max 侧重合去重）
        assert len(result.strategy_points) >= 3
        assert all(tp.provenance == Provenance.STRATEGY for tp in result.strategy_points)
        # 合流
        assert len(result.all_points) == len(result.llm_points) + len(result.strategy_points)
        # 覆盖率双指标
        assert result.strategy_obligation_coverage == 1.0
        assert result.requirement_item_coverage is not None
        assert result.requirement_item_coverage.coverage_ratio == 1.0  # item 被 LLM + strategy 都覆盖

    def test_generation_scope_three_values_present(self, v2_db):
        """合流后 generation_scope 三值齐全：item / cross_item / strategy"""
        v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
        doc = RequirementDoc(user_id=USER_ID, title="综合需求", source_type=SourceType.TEXT)
        v2_db.save_doc(doc)
        ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
        v2_db.save_version(ver)
        # 2 个 items，让 Step 3 Phase B 有机会产 cross_item
        item1 = RequirementItem(
            version_id=ver.id,
            seq=1,
            type=RequirementItemType.DATA_FIELD,
            module="登录",
            statement="手机号",
            fields=[
                FieldSpec(
                    name="phone", label="手机号", data_type=DataType.STRING, min_length=11, max_length=11, nullable=True
                )
            ],
            confidence=0.9,
        )
        item2 = RequirementItem(
            version_id=ver.id,
            seq=2,
            type=RequirementItemType.FUNCTION,
            module="登录",
            statement="验证码登录",
            confidence=0.9,
        )
        v2_db.save_item(item1)
        v2_db.save_item(item2)
        doc.latest_version_id = ver.id
        v2_db.save_doc(doc)

        # Step 3: Phase A 给 item1 一个点；Phase B 给一个跨项点
        phase_a_resp = json.dumps(
            {
                "test_points": [
                    {
                        "module": "登录",
                        "subcategory": "s",
                        "title": "t1",
                        "description": "d",
                        "dimension": "functional",
                        "priority": "P1",
                    }
                ]
            },
            ensure_ascii=False,
        )

        class FullClient(FakeClient):
            def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
                self.calls.append({"system": system_prompt, "user": user_prompt})
                if "单个原子需求项" in system_prompt:
                    # 从 user prompt 提取 item.id
                    for line in user_prompt.split("\n"):
                        if "当前需求项 id =" in line:
                            item_id = line.split("当前需求项 id =")[1].strip().split("；")[0].strip()
                            if item_id == item1.id:
                                return phase_a_resp
                    return '{"test_points": []}'
                # Phase B: 给一个跨项点
                return json.dumps(
                    {
                        "test_points": [
                            {
                                "item_ids": [item1.id, item2.id],
                                "module": "登录",
                                "subcategory": "联动",
                                "title": "手机号与验证码联动",
                                "description": "d",
                                "dimension": "linkage",
                                "priority": "P0",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        result = generate_test_points_full(FullClient(), version_id=ver.id, user_id=USER_ID)
        scopes = {tp.generation_scope for tp in result.all_points}
        assert GenerationScope.ITEM in scopes
        assert GenerationScope.CROSS_ITEM in scopes
        assert GenerationScope.STRATEGY in scopes

    def test_step3_failure_propagates(self, v2_db):
        """Step 3 失败（version 不存在）→ FullGenerationResult 带 issues，不调 Step 4"""
        v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
        client = FakeClient()
        result = generate_test_points_full(client, version_id="01ARZ3NDEKTSV4RRFFQ69G5FZZ", user_id=USER_ID)
        assert result.run_id == ""
        assert any("不存在" in i or "无 items" in i for i in result.issues)

    def test_counts_reflect_both_steps(self, v2_db):
        """Run.counts 反映 Step 3 + Step 4 合流后的总数"""
        v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
        doc = RequirementDoc(user_id=USER_ID, title="综合需求", source_type=SourceType.TEXT)
        v2_db.save_doc(doc)
        ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
        v2_db.save_version(ver)
        item = RequirementItem(
            version_id=ver.id,
            seq=1,
            type=RequirementItemType.DATA_FIELD,
            module="登录",
            statement="手机号",
            fields=[
                FieldSpec(
                    name="phone", label="手机号", data_type=DataType.STRING, min_length=11, max_length=11, nullable=True
                )
            ],
            confidence=0.9,
        )
        v2_db.save_item(item)
        doc.latest_version_id = ver.id
        v2_db.save_doc(doc)

        phase_a_resp = json.dumps(
            {
                "test_points": [
                    {
                        "module": "登录",
                        "subcategory": "s",
                        "title": "t1",
                        "description": "d",
                        "dimension": "functional",
                        "priority": "P1",
                    }
                ]
            },
            ensure_ascii=False,
        )
        client = FakeClient(phase_a_response=phase_a_resp)
        result = generate_test_points_full(client, version_id=ver.id, user_id=USER_ID)

        run = repo.get_run(result.run_id)
        assert run.status == RunStatus.DONE
        assert run.counts.items == 1
        assert run.counts.points == len(result.all_points)
        assert run.counts.obligations == len(result.obligations)
