"""V2 IR 构建编排测试 - build_requirement_ir（新建 v1 / 追加 v2 / 持久化可读 / 去重）。

对应 Step 2 + Q4：支持 Version 1 与追加 Version 2，但不含变更影响分析。
"""

import pytest

from core.schemas import Provenance, SourceType
from core.v2 import repository as repo
from core.v2.ir import build_requirement_ir, ingest_and_build_ir

USER = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

RESP = (
    '{"items":['
    '{"type":"data_field","module":"登录","statement":"手机号11位",'
    '"fields":[{"name":"phone","label":"手机号","data_type":"phone","min_length":11,"max_length":11}],"confidence":0.9},'
    '{"type":"permission","module":"登录","statement":"管理员可解锁",'
    '"permissions":[{"role":"admin","resource":"account","action":"unlock","allowed":true}],"confidence":0.85}'
    "]}"
)


class FakeClient:
    def __init__(self, resp):
        self.resp = resp
        self.calls = 0

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls += 1
        return self.resp


@pytest.fixture
def env(v2_db):
    v2_db.save_user(USER, "alice", "hash")
    return v2_db


class TestBuildIR:
    def test_new_doc_creates_v1_and_persists(self, env):
        result = build_requirement_ir(
            FakeClient(RESP), user_id=USER, title="登录需求", raw_text="手机号11位…", source_type=SourceType.MARKDOWN
        )
        assert result.version.version_no == 1
        assert len(result.items) == 2
        # doc.latest_version_id 指向新版本
        assert repo.get_doc(result.doc.id).latest_version_id == result.version.id
        # items 已持久化且字段级 IR 完整
        items = repo.list_items(result.version.id)
        assert len(items) == 2
        phone = next(i for i in items if i.statement == "手机号11位")
        assert phone.fields[0].max_length == 11
        assert phone.fields[0].data_type.value == "phone"

    def test_append_version_v2_keeps_v1(self, env):
        client = FakeClient(RESP)
        r1 = build_requirement_ir(client, user_id=USER, title="登录", raw_text="验证码5分钟")
        r2 = build_requirement_ir(client, user_id=USER, title="登录", raw_text="验证码10分钟", doc=r1.doc)
        assert (r1.version.version_no, r2.version.version_no) == (1, 2)
        assert r2.doc.id == r1.doc.id  # 同一需求身份
        assert [v.version_no for v in repo.list_versions(r1.doc.id)] == [1, 2]
        # v1 的 items 未被覆盖（历史资产不丢）
        assert len(repo.list_items(r1.version.id)) == 2
        assert len(repo.list_items(r2.version.id)) == 2

    def test_latest_version_pointer_advances(self, env):
        client = FakeClient(RESP)
        r1 = build_requirement_ir(client, user_id=USER, title="t", raw_text="a")
        r2 = build_requirement_ir(client, user_id=USER, title="t", raw_text="b", doc=r1.doc)
        assert repo.get_doc(r1.doc.id).latest_version_id == r2.version.id

    def test_dedupe_within_pipeline(self, env):
        dup = '{"items":[{"statement":"重复项","module":"m"},{"statement":"重复项","module":"m"}]}'
        result = build_requirement_ir(FakeClient(dup), user_id=USER, title="t", raw_text="x")
        assert len(result.items) == 1
        assert result.validation.duplicates_removed == 1

    def test_parse_failure_keeps_version_shell(self, env):
        result = build_requirement_ir(FakeClient("垃圾输出非JSON"), user_id=USER, title="t", raw_text="x")
        assert result.items == []
        assert result.parse.issues  # 记录了解析问题
        assert repo.get_version(result.version.id) is not None  # IR 骨架仍在

    def test_items_provenance_llm(self, env):
        result = build_requirement_ir(FakeClient(RESP), user_id=USER, title="t", raw_text="x")
        assert all(i.provenance == Provenance.LLM for i in result.items)

    def test_version_raw_text_persisted(self, env):
        result = build_requirement_ir(FakeClient(RESP), user_id=USER, title="t", raw_text="原始需求全文XYZ")
        assert repo.get_version(result.version.id).raw_text == "原始需求全文XYZ"


class TestIngestAndBuild:
    """链式入口 ingest_and_build_ir：文件/文本 → Ingestion → IR 持久化（一步到位）。"""

    def test_from_files_persists_ir(self, env, tmp_path):
        md = tmp_path / "req.md"
        md.write_text("# 登录\n手机号11位", encoding="utf-8")
        img = tmp_path / "ui.png"
        img.write_bytes(b"\x89PNG_x")
        client = FakeClient(RESP)
        result = ingest_and_build_ir(client, user_id=USER, title="登录", paths=[str(md), str(img)])
        assert result.version.version_no == 1
        assert len(result.items) == 2  # 文件文本经 ingestion 后解析出 RESP 的 2 个 item
        assert len(repo.list_items(result.version.id)) == 2  # 已持久化可读
        assert client.calls == 1  # 图片随首段进入 LLM（单段）

    def test_from_inline_text(self, env):
        result = ingest_and_build_ir(FakeClient(RESP), user_id=USER, title="t", text="手机号11位")
        assert len(result.items) == 2


class TestIRIssueObservability:
    """F2：解析失败原因必须落日志。

    此前 items=0 时 `parsed.issues` 从不进入任何日志或持久化，导致线上无法归因
    （bc_02 / bc_03 只能靠"哪些 case 含代码围栏"的相关性反推）。F2 只加日志，
    不改返回值、失败分类与降级语义。
    """

    _NO_JSON = "状态：\n```\n待支付 → 已支付\n```\n\n抱歉，我无法输出 JSON"

    def test_parse_issues_are_logged_as_warning(self, env, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="core.v2.ir"):
            result = build_requirement_ir(FakeClient(self._NO_JSON), user_id=USER, title="t", raw_text="x")
        msgs = [r.getMessage() for r in caplog.records if "IR 解析问题" in r.getMessage()]
        assert msgs, "解析失败原因仍未落日志"
        assert any("无法从 LLM 响应解析出 JSON" in m for m in msgs)
        assert result.items == []  # 返回值未被 F2 改变

    def test_clean_parse_emits_no_issue_warning(self, env, caplog):
        """正常路径不加噪，否则 warning 会失去信号价值"""
        import logging

        with caplog.at_level(logging.WARNING, logger="core.v2.ir"):
            result = build_requirement_ir(FakeClient(RESP), user_id=USER, title="t", raw_text="x")
        assert len(result.items) == 2
        assert not [r for r in caplog.records if "IR 解析问题" in r.getMessage()]
