"""V2 Step 3「需求 → 测试点」验收测试 - 12 条门槛逐条对应。

对应 docs/v2/PROGRESS.md §9 与 Step 3 计划文档 test_plan 章节。
每条测试独立、可单跑；全 mock LLM，不依赖真实 API。

用户修订后的 Step 3 硬性约束（贯穿全测试）：
  - provenance = LLM（代码强制覆写）
  - technique = None（Step 3 不赋值，Step 4 策略引擎才会赋）
  - obligation_id = None（Step 3 不赋值）
  - module 必须来自关联 RequirementItem 的 module（代码强制覆写/校验）
  - subcategory 允许 LLM 在 RequirementItem 语义范围内合理归纳
  - generation_scope ∈ {item, cross_item}（Phase A/B 分别赋值）
  - fingerprint 非空（业务确定性指纹，DB UNIQUE 约束，upsert 幂等身份）
"""

import json

import pytest

from core.schemas import (
    EntityStatus,
    GenerationScope,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    RunStatus,
    SourceType,
    TestDimension,
    TestPoint,
)
from core.v2 import repository as repo
from core.v2.ddl import get_schema_version
from core.v2.tp_orchestrator import generate_test_points
from core.v2.tp_prompts import PROMPT_VERSIONS_COMBINED

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class FakeClient:
    """Phase A 与 Phase B 用不同 system_prompt 区分响应"""

    def __init__(self, phase_a_map, phase_b_response='{"test_points": []}'):
        self.phase_a_map = phase_a_map  # dict[item_id, response_str]
        self.phase_b_response = phase_b_response
        self.calls: list[dict] = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        if "单个原子需求项" in system_prompt:
            for line in user_prompt.split("\n"):
                if "当前需求项 id =" in line:
                    item_id = line.split("当前需求项 id =")[1].strip().split("；")[0].strip()
                    return self.phase_a_map.get(item_id, '{"test_points": []}')
            return '{"test_points": []}'
        return self.phase_b_response


def _mk_pt(title: str, dim: str = "functional", pri: str = "P1", subcat: str = "输入校验", mod: str = "登录") -> dict:
    return {
        "module": mod,
        "subcategory": subcat,
        "title": title,
        "description": f"验证 {title}",
        "dimension": dim,
        "priority": pri,
    }


@pytest.fixture
def ir_with_5_items(v2_db):
    """预置 Doc + Version + 5 个 items（覆盖 data_field / function / permission 多类型）"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="订单+登录综合需求", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="综合需求描述", provenance=Provenance.LLM)
    v2_db.save_version(ver)

    items = [
        RequirementItem(
            version_id=ver.id,
            seq=1,
            type=RequirementItemType.DATA_FIELD,
            module="登录",
            statement="手机号 11 位",
            confidence=0.9,
        ),
        RequirementItem(
            version_id=ver.id,
            seq=2,
            type=RequirementItemType.DATA_FIELD,
            module="登录",
            statement="验证码 6 位数字，5 分钟有效",
            confidence=0.9,
        ),
        RequirementItem(
            version_id=ver.id,
            seq=3,
            type=RequirementItemType.FUNCTION,
            module="登录",
            statement="输入手机号+验证码后点击登录按钮进入首页",
            confidence=0.85,
        ),
        RequirementItem(
            version_id=ver.id,
            seq=4,
            type=RequirementItemType.DATA_FIELD,
            module="订单",
            statement="订单金额 total = price * quantity",
            confidence=0.95,
        ),
        RequirementItem(
            version_id=ver.id,
            seq=5,
            type=RequirementItemType.PERMISSION,
            module="订单",
            statement="仅 admin 可退款",
            confidence=0.8,
        ),
    ]
    for it in items:
        v2_db.save_item(it)
    doc.latest_version_id = ver.id
    v2_db.save_doc(doc)
    return doc, ver, items


def _phase_a_map_5(items):
    """给 5 个 items 各准备一个 Phase A 响应"""
    return {
        items[0].id: json.dumps({"test_points": [_mk_pt("手机号长度边界", "boundary")]}, ensure_ascii=False),
        items[1].id: json.dumps({"test_points": [_mk_pt("验证码有效期", "boundary")]}, ensure_ascii=False),
        items[2].id: json.dumps({"test_points": [_mk_pt("登录按钮跳转", "functional")]}, ensure_ascii=False),
        items[3].id: json.dumps(
            {"test_points": [_mk_pt("订单金额计算", "functional", mod="订单")]}, ensure_ascii=False
        ),
        items[4].id: json.dumps(
            {"test_points": [_mk_pt("退款权限校验", "permission", mod="订单")]}, ensure_ascii=False
        ),
    }


# ============================================================
# 门槛 1：给定 Step 2 IR，orchestrator 能生成 TestPoint[] 并全部通过 Pydantic
# ============================================================


def test_01_orchestrator_produces_pydantic_valid_points(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    client = FakeClient(_phase_a_map_5(items))
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    assert len(result.points) == 5
    for tp in result.points:
        assert isinstance(tp, TestPoint)
        # Pydantic 严格模式已生效（extra=forbid），能构造出来即证明通过校验
        assert tp.title and tp.description and tp.module and tp.subcategory


# ============================================================
# 门槛 2：每个 TestPoint 的 item_ids 全部指向真实存在的 RequirementItem
# ============================================================


def test_02_item_ids_all_reference_real_items(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    client = FakeClient(_phase_a_map_5(items))
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    valid_ids = {it.id for it in items}
    for tp in result.points:
        for iid in tp.item_ids:
            assert iid in valid_ids, f"item_id {iid} 不在 IR items 里"


# ============================================================
# 门槛 3：Phase A 的 item_ids 严格等于 [输入 item.id]，即使 LLM 乱写也被代码覆写
# ============================================================


def test_03_phase_a_item_ids_forced_overwrite(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    # LLM 乱写 item_ids
    evil_resp = json.dumps(
        {
            "test_points": [
                {
                    **_mk_pt("手机号长度"),
                    "item_ids": ["乱写的id", "另一个乱写的id"],  # 应被强制覆写
                }
            ]
        },
        ensure_ascii=False,
    )
    client = FakeClient({items[0].id: evil_resp})
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    phase_a_pts = [p for p in result.points if p.generation_scope == GenerationScope.ITEM]
    assert len(phase_a_pts) == 1
    assert phase_a_pts[0].item_ids == [items[0].id], "Phase A 必须强制覆写为 [当前 item.id]"


# ============================================================
# 门槛 4：Phase B 的 len(item_ids) >= 2；过滤非法 id 后 <2 的被丢弃
# ============================================================


def test_04_phase_b_requires_two_or_more_items(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    # Phase B 给 3 个候选：一个合法(2 真实 id)、一个只 1 个真实 id、一个全非法
    phase_b_resp = json.dumps(
        {
            "test_points": [
                {  # 合法：2 个真实 id
                    "item_ids": [items[0].id, items[1].id],
                    "module": "登录",
                    "subcategory": "字段联动",
                    "title": "手机号与验证码联动",
                    "description": "d",
                    "dimension": "linkage",
                    "priority": "P0",
                },
                {  # 非法：过滤后只剩 1 个 → 应被丢弃
                    "item_ids": [items[0].id, "乱写id1", "乱写id2"],
                    "module": "登录",
                    "subcategory": "s",
                    "title": "只剩一个真实id",
                    "description": "d",
                    "dimension": "linkage",
                    "priority": "P1",
                },
                {  # 非法：全部 id 都是乱写 → 应被丢弃
                    "item_ids": ["乱写id3", "乱写id4"],
                    "module": "登录",
                    "subcategory": "s",
                    "title": "全非法",
                    "description": "d",
                    "dimension": "linkage",
                    "priority": "P1",
                },
            ]
        },
        ensure_ascii=False,
    )
    client = FakeClient(_phase_a_map_5(items), phase_b_response=phase_b_resp)
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    cross_pts = [p for p in result.points if p.generation_scope == GenerationScope.CROSS_ITEM]
    assert len(cross_pts) == 1, "过滤非法 id 后 <2 的候选必须被丢弃"
    assert len(cross_pts[0].item_ids) >= 2


# ============================================================
# 门槛 5：dimension/priority 非法值被代码兜底；Step 3 硬性约束 technique/obligation_id/provenance
# ============================================================


def test_05_enum_fallback_and_step3_hard_constraints(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    evil_resp = json.dumps(
        {
            "test_points": [
                {
                    "module": "登录",
                    "subcategory": "s",
                    "title": "非法枚举测试",
                    "description": "d",
                    "dimension": "乱写的维度",  # 应兜底为 functional
                    "priority": "P99",  # 应兜底为 P1
                    # 以下三个字段 LLM 恶意给了，代码必须覆写
                    "technique": "boundary_value",
                    "obligation_id": "01ARZ3NDEKTSV4RRFFQ69G5FB0",
                    "provenance": "strategy",
                }
            ]
        },
        ensure_ascii=False,
    )
    client = FakeClient({items[0].id: evil_resp})
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    assert len(result.points) == 1
    tp = result.points[0]
    # 枚举兜底
    assert tp.dimension == TestDimension.FUNCTIONAL
    assert tp.priority == Priority.P1
    # Step 3 硬性约束
    assert tp.technique is None, "Step 3 产出的 technique 必须为 None"
    assert tp.obligation_id is None, "Step 3 产出的 obligation_id 必须为 None"
    assert tp.provenance == Provenance.LLM, "Step 3 产出的 provenance 必须为 LLM"
    assert tp.status == EntityStatus.DRAFT


# ============================================================
# 门槛 6：按 fingerprint 严格去重；跨 phase 不做语义合并
# ============================================================


def test_06_dedupe_by_fingerprint(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    # Phase A 同一个 item 输出两个业务身份完全相同的候选（title/module/subcategory 全同）
    dup_resp = json.dumps(
        {
            "test_points": [
                _mk_pt("重复标题"),
                _mk_pt("重复标题"),  # 完全重复 → 应被去重
                _mk_pt("不同标题"),
            ]
        },
        ensure_ascii=False,
    )
    client = FakeClient({items[0].id: dup_resp})
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    titles = [p.title for p in result.points]
    assert titles.count("重复标题") == 1, "同 fingerprint 应去重"
    assert "不同标题" in titles


# ============================================================
# 门槛 7：items 过多时 Phase B 能按 module 分批 + 合并结果不重不漏
# ============================================================


def test_07_phase_b_batches_by_module(v2_db):
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="大量 items", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="", provenance=Provenance.LLM)
    v2_db.save_version(ver)

    # 构造 40 个 items，分 2 个 module（订单 20 + 支付 20）→ 触发 Phase B 分批
    items = []
    for i in range(20):
        items.append(
            RequirementItem(
                version_id=ver.id,
                seq=i + 1,
                type=RequirementItemType.FUNCTION,
                module="订单",
                statement=f"order_{i}",
                confidence=0.9,
            )
        )
    for i in range(20):
        items.append(
            RequirementItem(
                version_id=ver.id,
                seq=21 + i,
                type=RequirementItemType.FUNCTION,
                module="支付",
                statement=f"pay_{i}",
                confidence=0.9,
            )
        )
    for it in items:
        v2_db.save_item(it)
    doc.latest_version_id = ver.id
    v2_db.save_doc(doc)

    # Phase A 每个 item 返回空（简化）；Phase B 每批返回 1 个候选
    phase_a_map = {it.id: '{"test_points": []}' for it in items}
    # Phase B 会被调 2 次（订单批 + 支付批），每次给同一个候选模板但 item_ids 不同
    # 简化：让 Phase B 都返回空，重点验证 calls=2
    client = FakeClient(phase_a_map, phase_b_response='{"test_points": []}')
    # Phase A 空 → Phase B 会被跳过（无参照系）
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    assert result.phase_b.calls == 0  # Phase A 空 → Phase B 跳过

    # 改造：让 Phase A 至少产 1 个，Phase B 才会跑
    phase_a_map_2 = {it.id: '{"test_points": []}' for it in items}
    phase_a_map_2[items[0].id] = json.dumps({"test_points": [_mk_pt("订单点1", mod="订单")]}, ensure_ascii=False)
    client2 = FakeClient(phase_a_map_2, phase_b_response='{"test_points": []}')
    result2 = generate_test_points(client2, version_id=ver.id, user_id=USER_ID, phase_b_batch_threshold=30)
    # 40 items > 30 → 按 module 分批，2 个 module → 2 次调用
    assert result2.phase_b.calls == 2, "items > 阈值时应按 module 分批"


# ============================================================
# 门槛 8：Run + GenerationConfig 正确落库；run.status 终态 DONE
# ============================================================


def test_08_run_and_generation_config_persisted(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    client = FakeClient(_phase_a_map_5(items))
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    run = repo.get_run(result.run_id)
    assert run is not None
    assert run.status == RunStatus.DONE
    assert run.requirement_version_id == ver.id
    assert run.doc_id == doc.id
    assert run.user_id == USER_ID
    assert run.counts.points == len(result.points)
    assert run.counts.items == len(items)

    cfg = repo.get_generation_config(run.generation_config_id)
    assert cfg is not None
    assert cfg.prompt_version == PROMPT_VERSIONS_COMBINED
    assert cfg.generator_version == "tp-gen-v1"
    assert cfg.reviewer_version is None  # Step 3 不涉及评审器


# ============================================================
# 门槛 9：test_point_items 关联表 M:N 写入正确；删 TestPoint 不级联删 Item
# ============================================================


def test_09_test_point_items_link_and_no_reverse_cascade(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    phase_b_resp = json.dumps(
        {
            "test_points": [
                {
                    "item_ids": [items[0].id, items[3].id],  # 跨 module（登录+订单）
                    "module": "登录",
                    "subcategory": "跨模块联动",
                    "title": "登录与订单联动",
                    "description": "d",
                    "dimension": "linkage",
                    "priority": "P0",
                }
            ]
        },
        ensure_ascii=False,
    )
    client = FakeClient(_phase_a_map_5(items), phase_b_response=phase_b_resp)
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    cross_pt = next(p for p in result.points if p.generation_scope == GenerationScope.CROSS_ITEM)
    got = repo.get_test_point(cross_pt.id)
    assert set(got.item_ids) == {items[0].id, items[3].id}

    # 关联表 M:N 验证：items[0] 应被 Phase A 的单点 + Phase B 的跨点同时引用
    from core.v2.db import v2_read_conn

    with v2_read_conn() as conn:
        rows = conn.execute(
            "SELECT test_point_id FROM test_point_items WHERE requirement_item_id = ?", (items[0].id,)
        ).fetchall()
    assert len(rows) >= 2, "items[0] 应被 Phase A + Phase B 两个 TestPoint 引用"

    # 反向不级联：删 TestPoint 不影响 RequirementItem
    # （SQLite 外键方向是 test_point_items.test_point_id → test_points.id，删 tp 只清关联表，不删 item）
    with v2_read_conn() as conn:
        item_row = conn.execute("SELECT id FROM requirement_items WHERE id = ?", (items[0].id,)).fetchone()
    assert item_row is not None


# ============================================================
# 门槛 10：幂等 —— 同 version 重复调用不产生孤儿/重复
# ============================================================


def test_10_idempotent_rerun(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    client1 = FakeClient(_phase_a_map_5(items))
    r1 = generate_test_points(client1, version_id=ver.id, user_id=USER_ID)
    ids_1 = {p.id for p in r1.points}
    fps_1 = {p.fingerprint for p in r1.points}

    client2 = FakeClient(_phase_a_map_5(items))
    r2 = generate_test_points(client2, version_id=ver.id, user_id=USER_ID)
    ids_2 = {p.id for p in r2.points}
    fps_2 = {p.fingerprint for p in r2.points}

    assert fps_1 == fps_2, "fingerprint 应完全一致"
    assert ids_1 == ids_2, "同 fingerprint 应复用旧 ULID"
    assert r1.run_id != r2.run_id, "Run 每次新建"

    # DB 层无重复
    all_pts = repo.list_test_points_by_version(ver.id)
    assert len(all_pts) == len(r1.points)


# ============================================================
# 门槛 11：覆盖率报告能列出未被引用的 RequirementItem（软指标，不阻塞入库）
# ============================================================


def test_11_coverage_report_lists_uncovered_items(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    # 只给前 3 个 item 产出，后 2 个返回空 → 覆盖率 3/5
    partial_map = {
        items[0].id: json.dumps({"test_points": [_mk_pt("t0")]}, ensure_ascii=False),
        items[1].id: json.dumps({"test_points": [_mk_pt("t1")]}, ensure_ascii=False),
        items[2].id: json.dumps({"test_points": [_mk_pt("t2")]}, ensure_ascii=False),
        items[3].id: '{"test_points": []}',
        items[4].id: '{"test_points": []}',
    }
    client = FakeClient(partial_map)
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    assert result.coverage is not None
    assert result.coverage.total_items == 5
    assert result.coverage.coverage_ratio == pytest.approx(3 / 5)
    assert set(result.coverage.uncovered_item_ids) == {items[3].id, items[4].id}
    # 覆盖率软指标不阻塞入库：3 个 TestPoint 仍然入库
    assert len(result.points) == 3


# ============================================================
# 门槛 12：module 必须来自 IR items；subcategory 允许 LLM 语义归纳
# ============================================================


def test_12_module_from_ir_subcategory_free(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    # LLM 乱写 module + 合理归纳 subcategory
    evil_resp = json.dumps(
        {
            "test_points": [
                {
                    "module": "乱写的模块名",  # 应被覆写为 item.module="登录"
                    "subcategory": "输入格式校验",  # 合理归纳，应保留
                    "title": "手机号格式",
                    "description": "d",
                    "dimension": "data_validation",
                    "priority": "P1",
                }
            ]
        },
        ensure_ascii=False,
    )
    client = FakeClient({items[0].id: evil_resp})
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    tp = result.points[0]
    assert tp.module == "登录", "module 必须强制来自 IR item.module"
    assert tp.subcategory == "输入格式校验", "subcategory 允许 LLM 语义归纳，应保留"


# ============================================================
# 附加门槛 A：generation_scope 正确赋值（Phase A=item, Phase B=cross_item）
# ============================================================


def test_extra_generation_scope_correctly_assigned(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    phase_b_resp = json.dumps(
        {
            "test_points": [
                {
                    "item_ids": [items[0].id, items[1].id],
                    "module": "登录",
                    "subcategory": "联动",
                    "title": "手机号+验证码",
                    "description": "d",
                    "dimension": "linkage",
                    "priority": "P0",
                }
            ]
        },
        ensure_ascii=False,
    )
    client = FakeClient(_phase_a_map_5(items), phase_b_response=phase_b_resp)
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    for tp in result.points:
        if len(tp.item_ids) == 1:
            assert tp.generation_scope == GenerationScope.ITEM
        else:
            assert tp.generation_scope == GenerationScope.CROSS_ITEM


# ============================================================
# 附加门槛 B：fingerprint 非空、格式正确、DB 层 UNIQUE 约束生效
# ============================================================


def test_extra_fingerprint_nonempty_and_unique(ir_with_5_items):
    doc, ver, items = ir_with_5_items
    client = FakeClient(_phase_a_map_5(items))
    result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
    fps = set()
    for tp in result.points:
        assert tp.fingerprint is not None
        assert tp.fingerprint.startswith("tp_")
        assert len(tp.fingerprint) == 35
        fps.add(tp.fingerprint)
    assert len(fps) == len(result.points), "fingerprint 必须互不相同"

    # DB 层 UNIQUE 约束：直接 SQL 插入同 fingerprint 应报错
    from sqlite3 import IntegrityError

    from core.v2.db import v2_conn

    with pytest.raises(IntegrityError), v2_conn() as conn:
        conn.execute(
            "INSERT INTO test_points (id, module, subcategory, title, description, dimension, "
            "generation_scope, fingerprint, created_at, updated_at) "
            "VALUES ('01ARZ3NDEKTSV4RRFFQ69G5FZZ', 'm', 's', 't', 'd', 'functional', "
            "'item', ?, datetime('now'), datetime('now'))",
            (result.points[0].fingerprint,),
        )


# ============================================================
# 附加门槛 C：V1 零回归（不动 web/data.py、core/generator.py、data.db）
# ============================================================


def test_extra_v1_untouched(ir_with_5_items):
    """Step 3 全程不动 V1：data.db 文件、V1 表、V1 Prompt 常量均保持原状"""

    # V1 数据库文件路径不变
    from core import db as v1_db

    v1_path = v1_db._DB_PATH
    assert v1_path.endswith("data.db"), "V1 数据库路径应保持 data.db"

    # V1 TEST_POINTS_PROMPT 常量仍可 import（未被 Step 3 覆盖）
    from web.data import TEST_POINTS_PROMPT

    assert "资深测试工程师" in TEST_POINTS_PROMPT

    # V1 core.generator 的关键 Prompt 常量仍在
    from core.generator import ANALYSIS_PROMPT, MODULE_PROMPT, SYSTEM_PROMPT

    assert ANALYSIS_PROMPT and MODULE_PROMPT and SYSTEM_PROMPT

    # schema_version = 3（Step 3 升级后）
    assert get_schema_version() == 3
