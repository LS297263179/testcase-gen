"""V2 Step 3 测试点编排测试 - 端到端 mock、Run/GenerationConfig 落库、幂等、关联表。"""

import json

import pytest

from core.schemas import (
    EntityStatus,
    GenerationScope,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    RunStatus,
    SourceType,
)
from core.v2 import repository as repo
from core.v2.tp_orchestrator import (
    GENERATOR_VERSION,
    GenerationResult,
    generate_test_points,
)
from core.v2.tp_prompts import PROMPT_VERSIONS_COMBINED

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class FakeClient:
    """Phase A 与 Phase B 用不同的 system_prompt 区分响应。"""

    def __init__(self, phase_a_responses=None, phase_b_response=None):
        # phase_a_responses: dict[item_id, response_str] 或统一 response_str
        self.phase_a_responses = phase_a_responses or {}
        self.phase_b_response = phase_b_response or '{"test_points": []}'
        self.calls: list[dict] = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        # Phase A 的 system prompt 里含 "单个原子需求项"
        if "单个原子需求项" in system_prompt:
            # 从 user prompt 里提取 item.id（Phase A 显式注入了 "当前需求项 id = XXX"）
            for line in user_prompt.split("\n"):
                if "当前需求项 id =" in line:
                    item_id = line.split("当前需求项 id =")[1].strip().split("；")[0].strip()
                    resp = self.phase_a_responses.get(item_id)
                    if resp is not None:
                        return resp
                    break
            # 兜底：若 phase_a_responses 是字符串则统一用
            if isinstance(self.phase_a_responses, str):
                return self.phase_a_responses
            return '{"test_points": []}'
        # Phase B
        return self.phase_b_response


@pytest.fixture
def setup_ir(v2_db):
    """预置 Doc + Version + 3 个 items，返回 (doc, version, items)"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="登录需求", source_type=SourceType.TEXT)
    v2_db.save_doc(doc)
    ver = RequirementVersion(
        doc_id=doc.id, version_no=1, raw_text="用户通过手机号+验证码登录", provenance=Provenance.LLM
    )
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
    ]
    for it in items:
        v2_db.save_item(it)
    doc.latest_version_id = ver.id
    v2_db.save_doc(doc)
    return doc, ver, items


def _phase_a_resp(item_id: str, title: str, dim: str = "functional") -> str:
    return json.dumps(
        {
            "test_points": [
                {
                    "module": "登录",
                    "subcategory": "输入校验",
                    "title": title,
                    "description": f"验证 {title}",
                    "dimension": dim,
                    "priority": "P1",
                }
            ]
        },
        ensure_ascii=False,
    )


# ============================================================
# 端到端流程
# ============================================================


class TestOrchestratorEndToEnd:
    def test_generates_points_and_persists(self, setup_ir):
        doc, ver, items = setup_ir
        client = FakeClient(
            phase_a_responses={
                items[0].id: _phase_a_resp(items[0].id, "手机号长度边界", "boundary"),
                items[1].id: _phase_a_resp(items[1].id, "验证码有效期", "boundary"),
                items[2].id: _phase_a_resp(items[2].id, "登录按钮跳转", "functional"),
            },
            phase_b_response=json.dumps(
                {
                    "test_points": [
                        {
                            "item_ids": [items[0].id, items[1].id, items[2].id],
                            "module": "登录",
                            "subcategory": "字段联动",
                            "title": "手机号+验证码+登录联动",
                            "description": "三者协同工作",
                            "dimension": "linkage",
                            "priority": "P0",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
        result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
        assert isinstance(result, GenerationResult)
        assert len(result.points) == 4  # 3 个 Phase A + 1 个 Phase B
        assert result.phase_a.valid_count == 3
        assert result.phase_b.valid_count == 1
        assert result.phase_a.calls == 3  # 每个 item 调 1 次
        assert result.phase_b.calls == 1  # items 数量 <= 阈值 → 单次

        # 每个 TestPoint 都持久化了
        for tp in result.points:
            got = repo.get_test_point(tp.id)
            assert got is not None
            assert got.run_id == result.run_id
            assert got.version_id == ver.id
            assert got.provenance == Provenance.LLM
            assert got.technique is None
            assert got.obligation_id is None
            assert got.status == EntityStatus.DRAFT
            assert got.fingerprint is not None

        # Phase A 的 scope=item，item_ids 长度=1
        phase_a_pts = [p for p in result.points if p.generation_scope == GenerationScope.ITEM]
        assert len(phase_a_pts) == 3
        for p in phase_a_pts:
            assert len(p.item_ids) == 1
        # Phase B 的 scope=cross_item，item_ids 长度>=2
        phase_b_pts = [p for p in result.points if p.generation_scope == GenerationScope.CROSS_ITEM]
        assert len(phase_b_pts) == 1
        assert len(phase_b_pts[0].item_ids) >= 2

    def test_run_and_config_persisted(self, setup_ir):
        doc, ver, items = setup_ir
        client = FakeClient(
            phase_a_responses={it.id: _phase_a_resp(it.id, f"t{i}") for i, it in enumerate(items)},
        )
        result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
        # Run 落库
        run = repo.get_run(result.run_id)
        assert run is not None
        assert run.status == RunStatus.DONE
        assert run.requirement_version_id == ver.id
        assert run.user_id == USER_ID
        assert run.counts.points == len(result.points)
        assert run.counts.items == len(items)
        # GenerationConfig 落库
        cfg = repo.get_generation_config(run.generation_config_id)
        assert cfg is not None
        assert cfg.prompt_version == PROMPT_VERSIONS_COMBINED
        assert cfg.generator_version == GENERATOR_VERSION
        assert cfg.reviewer_version is None  # Step 3 不涉及评审器

    def test_coverage_report_full(self, setup_ir):
        doc, ver, items = setup_ir
        # 每个 item 都有 Phase A 产出 → 覆盖率 100%
        client = FakeClient(
            phase_a_responses={it.id: _phase_a_resp(it.id, f"t{i}") for i, it in enumerate(items)},
        )
        result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
        assert result.coverage is not None
        assert result.coverage.total_items == 3
        assert result.coverage.coverage_ratio == 1.0
        assert result.coverage.uncovered_item_ids == []

    def test_coverage_report_partial(self, setup_ir):
        doc, ver, items = setup_ir
        # 只给第一个 item 产出，后两个 LLM 返回空
        client = FakeClient(
            phase_a_responses={
                items[0].id: _phase_a_resp(items[0].id, "t0"),
                items[1].id: '{"test_points": []}',
                items[2].id: '{"test_points": []}',
            },
        )
        result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
        assert result.coverage.coverage_ratio == pytest.approx(1 / 3)
        assert set(result.coverage.uncovered_item_ids) == {items[1].id, items[2].id}

    def test_test_point_items_link_table(self, setup_ir):
        """关联表 M:N 写入正确"""
        doc, ver, items = setup_ir
        client = FakeClient(
            phase_a_responses={it.id: _phase_a_resp(it.id, f"t{i}") for i, it in enumerate(items)},
            phase_b_response=json.dumps(
                {
                    "test_points": [
                        {
                            "item_ids": [items[0].id, items[2].id],
                            "module": "登录",
                            "subcategory": "联动",
                            "title": "手机号与登录按钮联动",
                            "description": "d",
                            "dimension": "linkage",
                            "priority": "P1",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
        result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
        # 找 Phase B 的那个 TestPoint
        cross_pt = next(p for p in result.points if p.generation_scope == GenerationScope.CROSS_ITEM)
        got = repo.get_test_point(cross_pt.id)
        assert set(got.item_ids) == {items[0].id, items[2].id}


# ============================================================
# 幂等性
# ============================================================


class TestOrchestratorIdempotency:
    def test_rerun_same_version_reuses_ids(self, setup_ir):
        """同 version 重跑 orchestrator：同 fingerprint 复用旧 ULID，不产生重复行"""
        doc, ver, items = setup_ir
        client = FakeClient(
            phase_a_responses={it.id: _phase_a_resp(it.id, f"t{i}") for i, it in enumerate(items)},
        )
        result1 = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
        ids_1 = {p.id for p in result1.points}
        fps_1 = {p.fingerprint for p in result1.points}

        # 重跑（新 Run，但 fingerprint 相同）
        client2 = FakeClient(
            phase_a_responses={it.id: _phase_a_resp(it.id, f"t{i}") for i, it in enumerate(items)},
        )
        result2 = generate_test_points(client2, version_id=ver.id, user_id=USER_ID)
        ids_2 = {p.id for p in result2.points}
        fps_2 = {p.fingerprint for p in result2.points}

        assert fps_1 == fps_2, "fingerprint 应完全相同"
        assert ids_1 == ids_2, "同 fingerprint 应复用旧 ULID"
        assert result1.run_id != result2.run_id, "Run 每次都是新的"

        # DB 层无重复
        all_points = repo.list_test_points_by_version(ver.id)
        assert len(all_points) == len(result1.points)

    def test_rerun_with_description_change_updates_in_place(self, setup_ir):
        """重跑时非身份字段（description）变化 → upsert 更新，id 不变"""
        doc, ver, items = setup_ir
        resp1 = json.dumps(
            {
                "test_points": [
                    {
                        "module": "登录",
                        "subcategory": "输入校验",
                        "title": "手机号长度",
                        "description": "第一版描述",
                        "dimension": "boundary",
                        "priority": "P1",
                    }
                ]
            },
            ensure_ascii=False,
        )
        resp2 = json.dumps(
            {
                "test_points": [
                    {
                        "module": "登录",
                        "subcategory": "输入校验",
                        "title": "手机号长度",
                        "description": "第二版更新后的描述",
                        "dimension": "boundary",
                        "priority": "P1",
                    }
                ]
            },
            ensure_ascii=False,
        )
        client1 = FakeClient(
            phase_a_responses={items[0].id: resp1, items[1].id: '{"test_points":[]}', items[2].id: '{"test_points":[]}'}
        )
        r1 = generate_test_points(client1, version_id=ver.id, user_id=USER_ID)
        # 找到手机号长度那个
        tp1 = next(p for p in r1.points if p.title == "手机号长度")

        client2 = FakeClient(
            phase_a_responses={items[0].id: resp2, items[1].id: '{"test_points":[]}', items[2].id: '{"test_points":[]}'}
        )
        r2 = generate_test_points(client2, version_id=ver.id, user_id=USER_ID)
        tp2 = next(p for p in r2.points if p.title == "手机号长度")

        assert tp1.id == tp2.id, "同 fingerprint 复用 id"
        got = repo.get_test_point(tp1.id)
        assert got.description == "第二版更新后的描述"


# ============================================================
# 边界情况
# ============================================================


class TestOrchestratorEdgeCases:
    def test_version_not_found(self, v2_db):
        v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
        client = FakeClient()
        result = generate_test_points(client, version_id="01ARZ3NDEKTSV4RRFFQ69G5FZZ", user_id=USER_ID)
        assert result.run_id == ""
        assert any("不存在" in i for i in result.issues)

    def test_no_items_in_version(self, v2_db):
        v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
        doc = RequirementDoc(user_id=USER_ID, title="空需求", source_type=SourceType.TEXT)
        v2_db.save_doc(doc)
        ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="", provenance=Provenance.LLM)
        v2_db.save_version(ver)
        client = FakeClient()
        result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
        assert result.run_id == ""
        assert any("无 items" in i for i in result.issues)

    def test_user_id_inferred_from_doc(self, setup_ir):
        """未传 user_id 时从 Version → Doc → user_id 反查"""
        doc, ver, items = setup_ir
        client = FakeClient(
            phase_a_responses={it.id: _phase_a_resp(it.id, f"t{i}") for i, it in enumerate(items)},
        )
        result = generate_test_points(client, version_id=ver.id)  # 不传 user_id
        run = repo.get_run(result.run_id)
        assert run.user_id == USER_ID

    def test_all_llm_fail_gracefully(self, setup_ir):
        """所有 LLM 调用都失败时，Run 仍然落库到 DONE，points 为空"""
        doc, ver, items = setup_ir

        class FailClient:
            def chat(self, *args, **kwargs):
                raise RuntimeError("全挂")

        result = generate_test_points(FailClient(), version_id=ver.id, user_id=USER_ID)
        assert result.points == []
        assert result.phase_a.calls == len(items)
        assert all("LLM 调用失败" in i for i in result.phase_a.issues)
        run = repo.get_run(result.run_id)
        assert run.status == RunStatus.DONE
        assert run.counts.points == 0

    def test_phase_b_skipped_when_phase_a_empty(self, setup_ir):
        """Phase A 无产出时跳过 Phase B（无参照系）"""
        doc, ver, items = setup_ir
        client = FakeClient(
            phase_a_responses={it.id: '{"test_points": []}' for it in items},
        )
        result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
        assert result.phase_a.valid_count == 0
        assert result.phase_b.calls == 0
        assert any("Phase A 无产出" in i for i in result.phase_b.issues)

    def test_list_test_points_by_run(self, setup_ir):
        doc, ver, items = setup_ir
        client = FakeClient(
            phase_a_responses={it.id: _phase_a_resp(it.id, f"t{i}") for i, it in enumerate(items)},
        )
        result = generate_test_points(client, version_id=ver.id, user_id=USER_ID)
        by_run = repo.list_test_points_by_run(result.run_id)
        assert len(by_run) == len(result.points)
        by_ver = repo.list_test_points_by_version(ver.id)
        assert len(by_ver) == len(result.points)
