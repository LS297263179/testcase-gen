"""V2 Step 4「测试策略引擎」验收测试 - 12 条主门槛 + 2 条附加，逐条对应。

对应 docs/v2/PROGRESS.md §9 与 Step 4 计划文档 Test Plan 章节。
每条测试独立、可单跑；纯代码派生，不调 LLM。

用户修订后的 Step 4 硬性约束（贯穿全测试）：
  - Strategy TestPoint 必须：provenance=STRATEGY / generation_scope=STRATEGY / technique!=None / obligation_id!=None
  - Strategy Engine 不生成真实测试数据，只产出抽象类标识（具体数据由 Step 5 TestDataGenerator 生成）
  - fingerprint 公式：sha256(version_id | "strategy" | obligation_id | technique | canonical(strategy_params))
  - 覆盖率双指标严格分离：
      Strategy Obligation Coverage（硬指标，应=1.0）≠ RequirementItem Coverage（整体软指标）
"""

import json

from core.schemas import (
    DataType,
    EntityStatus,
    FieldSpec,
    GenerationConfig,
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
from core.v2.ddl import get_schema_version
from core.v2.fingerprint import canonical_strategy_params, compute_strategy_testpoint_fingerprint
from core.v2.strategy.orchestrator import (
    FullGenerationResult,
    apply_strategy_engine,
    generate_test_points_full,
)

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class FakeClient:
    """Step 3 用 mock client（Phase A 返回空或单个点，聚焦 Step 4 验收）"""

    def __init__(self, phase_a_response='{"test_points": []}'):
        self.phase_a_response = phase_a_response
        self.calls: list[dict] = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        if "单个原子需求项" in system_prompt:
            return self.phase_a_response
        return '{"test_points": []}'


def _setup_run(v2_db, items_spec: list[dict]) -> tuple[Run, RequirementVersion, list[RequirementItem]]:
    """预置 Doc + Version + items + Run（status=DONE，模拟 Step 3 已结束）"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="Step4 验收需求", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
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
# 门槛 1：min_value+max_value → BOUNDARY_VALUE obligation + 6 TestPoint
# ============================================================


def test_01_boundary_value_six_points(v2_db):
    run, ver, items = _setup_run(
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
    assert len(result.obligations) == 1
    assert result.obligations[0].technique == Technique.BOUNDARY_VALUE
    assert len(result.points) == 6
    values = sorted(tp.strategy_params["value"] for tp in result.points)
    assert values == [0, 1, 2, 99, 100, 101]


# ============================================================
# 门槛 2：min_length+max_length → BOUNDARY_VALUE obligation + 6 TestPoint
# ============================================================


def test_02_boundary_length_six_points(v2_db):
    run, ver, items = _setup_run(
        v2_db,
        [
            {
                "fields": [
                    FieldSpec(name="username", label="用户名", data_type=DataType.STRING, min_length=3, max_length=20)
                ]
            },
        ],
    )
    result = apply_strategy_engine(run.id, ver.id)
    length_obs = [ob for ob in result.obligations if ob.params.get("kind") == "length"]
    assert len(length_obs) == 1
    length_tps = [tp for tp in result.points if tp.strategy_params.get("kind") == "length"]
    assert len(length_tps) == 6
    values = sorted(tp.strategy_params["value"] for tp in length_tps)
    assert values == [2, 3, 4, 19, 20, 21]


# ============================================================
# 门槛 3：enum_values → EQUIVALENCE_CLASS obligation + N+1 TestPoint
# ============================================================


def test_03_equivalence_enum_n_plus_1(v2_db):
    run, ver, items = _setup_run(
        v2_db,
        [
            {
                "fields": [
                    FieldSpec(
                        name="status",
                        label="状态",
                        data_type=DataType.ENUM,
                        enum_values=["active", "disabled", "pending"],
                        nullable=True,
                    )
                ]
            },
        ],
    )
    result = apply_strategy_engine(run.id, ver.id)
    enum_obs = [ob for ob in result.obligations if ob.params.get("kind") == "enum"]
    assert len(enum_obs) == 1
    enum_tps = [tp for tp in result.points if tp.strategy_params.get("class", "").endswith("enum_value")]
    assert len(enum_tps) == 4  # 3 合法 + 1 非法
    valid = [tp for tp in enum_tps if tp.strategy_params["class"] == "valid_enum_value"]
    invalid = [tp for tp in enum_tps if tp.strategy_params["class"] == "invalid_enum_value"]
    assert len(valid) == 3
    assert len(invalid) == 1


# ============================================================
# 门槛 4：required=True → EQUIVALENCE_CLASS obligation（必填 + 空值非法）
# ============================================================


def test_04_equivalence_required(v2_db):
    run, ver, items = _setup_run(
        v2_db,
        [
            {"fields": [FieldSpec(name="age", label="年龄", data_type=DataType.INT, required=True, nullable=True)]},
        ],
    )
    result = apply_strategy_engine(run.id, ver.id)
    req_obs = [ob for ob in result.obligations if ob.params.get("kind") == "required"]
    assert len(req_obs) == 1
    req_tps = [tp for tp in result.points if tp.strategy_params.get("class", "").startswith("required_")]
    assert len(req_tps) == 2
    classes = {tp.strategy_params["class"] for tp in req_tps}
    assert classes == {"required_provided", "required_missing"}


# ============================================================
# 门槛 5：pattern 非空 → EQUIVALENCE_CLASS obligation（合法匹配 + 非法不匹配）
# ============================================================


def test_05_equivalence_pattern(v2_db):
    run, ver, items = _setup_run(
        v2_db,
        [
            {"fields": [FieldSpec(name="phone", label="手机号", data_type=DataType.STRING, pattern=r"^\d{11}$")]},
        ],
    )
    result = apply_strategy_engine(run.id, ver.id)
    pat_obs = [ob for ob in result.obligations if ob.params.get("kind") == "pattern"]
    assert len(pat_obs) == 1
    pat_tps = [tp for tp in result.points if tp.strategy_params.get("class", "").endswith("pattern")]
    assert len(pat_tps) == 2
    classes = {tp.strategy_params["class"] for tp in pat_tps}
    assert classes == {"valid_pattern", "invalid_pattern"}
    # ★ 修订后 description 不含具体数据样例
    for tp in pat_tps:
        assert "11111111111" not in tp.description
        assert "Step 5" in tp.description or "TestDataGenerator" in tp.description


# ============================================================
# 门槛 6：PermissionRule → PERMISSION_MATRIX obligation + 1:1 TestPoint
# ============================================================


def test_06_permission_matrix_one_to_one(v2_db):
    run, ver, items = _setup_run(
        v2_db,
        [
            {
                "type": RequirementItemType.PERMISSION,
                "permissions": [
                    PermissionRule(role="admin", resource="order", action="refund", allowed=True),
                    PermissionRule(role="user", resource="order", action="refund", allowed=False),
                ],
            },
        ],
    )
    result = apply_strategy_engine(run.id, ver.id)
    perm_obs = [ob for ob in result.obligations if ob.technique == Technique.PERMISSION_MATRIX]
    assert len(perm_obs) == 2
    perm_tps = [tp for tp in result.points if tp.technique == Technique.PERMISSION_MATRIX]
    assert len(perm_tps) == 2  # 1:1
    titles = {tp.title for tp in perm_tps}
    assert "admin refund order 允许" in titles
    assert "user refund order 拒绝" in titles


# ============================================================
# 门槛 7：Strategy TestPoint 硬性约束
# ============================================================


def test_07_strategy_testpoint_hard_constraints(v2_db):
    run, ver, items = _setup_run(
        v2_db,
        [
            {
                "fields": [
                    FieldSpec(
                        name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True
                    ),
                    FieldSpec(
                        name="email", label="邮箱", data_type=DataType.EMAIL, required=True, nullable=False, unique=True
                    ),
                ],
                "permissions": [PermissionRule(role="admin", resource="user", action="delete", allowed=True)],
            },
        ],
    )
    result = apply_strategy_engine(run.id, ver.id)
    assert len(result.points) > 0
    for tp in result.points:
        # 用户补充 1：Strategy TestPoint 必须满足以下 4 条
        assert tp.provenance == Provenance.STRATEGY
        assert tp.generation_scope == GenerationScope.STRATEGY
        assert tp.technique is not None
        assert tp.obligation_id is not None
        # 附加约束
        assert tp.strategy_params is not None
        assert tp.fingerprint is not None
        assert tp.status == EntityStatus.DRAFT
        assert tp.item_ids == [items[0].id]
        # DB 层验证
        got = repo.get_test_point(tp.id)
        assert got.provenance == Provenance.STRATEGY
        assert got.strategy_params == tp.strategy_params


# ============================================================
# 门槛 8：Strategy Obligation Coverage == 1.0（硬指标）
# ============================================================


def test_08_strategy_obligation_coverage_is_one(v2_db):
    """用户补充 2：Step 4 生成的所有 CoverageObligation 均至少有一个 Strategy TestPoint 覆盖"""
    run, ver, items = _setup_run(
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
                    FieldSpec(name="email", label="邮箱", data_type=DataType.EMAIL, required=True, nullable=False),
                ],
                "permissions": [
                    PermissionRule(role="admin", resource="user", action="delete", allowed=True),
                    PermissionRule(role="user", resource="user", action="delete", allowed=False),
                ],
            },
        ],
    )
    result = apply_strategy_engine(run.id, ver.id)
    assert len(result.obligations) > 0
    assert result.strategy_obligation_coverage == 1.0
    # 交叉验证：每个 obligation 都有 add_coverage 登记
    for ob in result.obligations:
        covered = repo.coverage_targets(ob.id, TargetType.TESTPOINT)
        assert len(covered) >= 1, f"obligation {ob.id} 未被任何 TestPoint 覆盖"
    # repo.obligation_coverage_ratio 也应为 1.0
    assert repo.obligation_coverage_ratio(run.id) == 1.0


# ============================================================
# 门槛 9：fingerprint 幂等（重跑不产生孤儿/重复）
# ============================================================


def test_09_fingerprint_idempotent_rerun(v2_db):
    run, ver, items = _setup_run(
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

    r2 = apply_strategy_engine(run.id, ver.id)
    ids_2 = {tp.id for tp in r2.points}
    fps_2 = {tp.fingerprint for tp in r2.points}
    ob_ids_2 = {ob.id for ob in r2.obligations}

    assert ob_ids_1 == ob_ids_2, "obligation.id 应复用（natural key 对齐）"
    assert fps_1 == fps_2, "fingerprint 应完全一致"
    assert ids_1 == ids_2, "同 fingerprint 应复用旧 ULID"
    # DB 层无重复
    all_pts = repo.list_test_points_by_version(ver.id)
    assert len(all_pts) == len(r1.points)


# ============================================================
# 门槛 10：Step 3 + Step 4 合流后 generation_scope 三值齐全
# ============================================================


def test_10_merged_three_scopes_present(v2_db):
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="合流需求", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    v2_db.save_version(ver)
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
                for line in user_prompt.split("\n"):
                    if "当前需求项 id =" in line:
                        item_id = line.split("当前需求项 id =")[1].strip().split("；")[0].strip()
                        if item_id == item1.id:
                            return phase_a_resp
                return '{"test_points": []}'
            # Phase B: 跨项点
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
    assert isinstance(result, FullGenerationResult)
    scopes = {tp.generation_scope for tp in result.all_points}
    assert GenerationScope.ITEM in scopes
    assert GenerationScope.CROSS_ITEM in scopes
    assert GenerationScope.STRATEGY in scopes
    # provenance 两种都有
    provs = {tp.provenance for tp in result.all_points}
    assert Provenance.LLM in provs
    assert Provenance.STRATEGY in provs


# ============================================================
# 门槛 11：Run 状态机 + counts 累加
# ============================================================


def test_11_run_state_machine_and_counts(v2_db):
    run, ver, items = _setup_run(
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
    assert repo.get_run(run.id).status == RunStatus.DONE
    result = apply_strategy_engine(run.id, ver.id)
    got_run = repo.get_run(run.id)
    assert got_run.status == RunStatus.DONE
    assert got_run.counts.obligations == len(result.obligations)
    assert got_run.counts.points == len(result.points)
    assert got_run.counts.items == len(items)


# ============================================================
# 门槛 12：V1 零回归 + schema_version=4
# ============================================================


def test_12_v1_untouched_and_schema_v4(v2_db):
    from core import db as v1_db

    assert v1_db._DB_PATH.endswith("data.db")
    from web.data import TEST_POINTS_PROMPT

    assert "资深测试工程师" in TEST_POINTS_PROMPT
    from core.generator import ANALYSIS_PROMPT, MODULE_PROMPT, SYSTEM_PROMPT

    assert ANALYSIS_PROMPT and MODULE_PROMPT and SYSTEM_PROMPT
    # Step 4 要求 schema 至少升级到 4；Step 5+ 会继续递增，故用 >= 保证里程碑测试对未来鲁棒
    assert get_schema_version() >= 4


# ============================================================
# 附加 A：fingerprint 公式验证（用户补充 3）
# ============================================================


def test_extra_a_fingerprint_formula(v2_db):
    """Strategy TestPoint fingerprint = sha256(version_id | "strategy" | obligation_id | technique | canonical(strategy_params))"""
    run, ver, items = _setup_run(
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
    for tp in result.points:
        expected_fp = compute_strategy_testpoint_fingerprint(
            version_id=ver.id,
            obligation_id=ob.id,
            technique=Technique.BOUNDARY_VALUE.value,
            strategy_params=tp.strategy_params,
        )
        assert tp.fingerprint == expected_fp
        assert tp.fingerprint.startswith("tp_")
        assert len(tp.fingerprint) == 35


def test_extra_a_canonical_params_deterministic():
    """canonical_strategy_params 对相同 dict 不同插入顺序产出相同字符串"""
    p1 = {"boundary_type": "min", "value": 1, "kind": "value"}
    p2 = {"kind": "value", "value": 1, "boundary_type": "min"}
    assert canonical_strategy_params(p1) == canonical_strategy_params(p2)
    # 不同值 → 不同 canonical
    p3 = {"boundary_type": "max", "value": 100, "kind": "value"}
    assert canonical_strategy_params(p1) != canonical_strategy_params(p3)


# ============================================================
# 附加 B：覆盖率双指标严格分离（用户修订）
# ============================================================


def test_extra_b_dual_coverage_metrics_separated(v2_db):
    """Strategy Obligation Coverage（硬）≠ RequirementItem Coverage（软），两者独立计算"""
    run, ver, items = _setup_run(
        v2_db,
        [
            # item1: 有 fields → Step 4 能派生 obligation + TestPoint
            {
                "fields": [
                    FieldSpec(
                        name="age", label="年龄", data_type=DataType.INT, min_value=1, max_value=100, nullable=True
                    )
                ]
            },
            # item2: 纯功能类，无 fields/permissions → Step 4 无法派生 obligation
            {"type": RequirementItemType.FUNCTION, "statement": "用户点击退出登录，系统返回登录页"},
        ],
    )
    result = apply_strategy_engine(run.id, ver.id)

    # 硬指标：Strategy Obligation Coverage = 1.0（所有派生的 obligation 都被覆盖）
    assert result.strategy_obligation_coverage == 1.0
    assert len(result.obligations) == 1  # 只有 item1 派生了 obligation

    # 软指标：RequirementItem Coverage = 0.5（item1 被覆盖，item2 未被 Step 4 覆盖）
    assert result.requirement_item_coverage is not None
    assert result.requirement_item_coverage.total_items == 2
    assert result.requirement_item_coverage.coverage_ratio == 0.5
    assert result.requirement_item_coverage.uncovered_item_ids == [items[1].id]

    # 两个指标数值不同，证明严格分离
    assert result.strategy_obligation_coverage != result.requirement_item_coverage.coverage_ratio
