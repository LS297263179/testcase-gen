"""Step 2 验收门槛测试 - 逐条对应用户定义的 14 项验收标准。

全部使用 mock LLM（FakeClient），不依赖真实 API。每个测试对应一条验收门槛，
测试名以序号开头，便于逐条核对。
"""

import pytest

from core.schemas import ConfidenceLevel, Provenance, RequirementItem, SourceType
from core.v2 import repository as repo
from core.v2.ingestion import ingest
from core.v2.ir import build_requirement_ir
from core.v2.parser import parse_requirement, split_segments

USER = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
VID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"  # 合法 ULID 形态，供不落库的 parse 测试用


class FakeClient:
    """mock LLM：记录调用、返回预置响应"""

    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls.append({"images": images, "user": user_prompt})
        return self.resp


@pytest.fixture
def env(v2_db):
    v2_db.save_user(USER, "alice", "hash")
    return v2_db


def _items(*stmts, module="m", conf=0.9):
    """快速构造 LLM 响应 JSON"""
    inner = ",".join(f'{{"statement":"{s}","module":"{module}","confidence":{conf}}}' for s in stmts)
    return f'{{"items":[{inner}]}}'


# ✅ 1. 文本需求可以解析
def test_01_text_requirement_parsed(env):
    resp = '{"items":[{"type":"function","module":"登录","statement":"用户可用手机号登录","source_ref":"用户可用手机号登录","confidence":0.9}]}'
    result = build_requirement_ir(
        FakeClient(resp), user_id=USER, title="登录", raw_text="用户可用手机号登录", source_type=SourceType.TEXT
    )
    assert len(result.items) == 1
    assert result.items[0].statement == "用户可用手机号登录"


# ✅ 2. Markdown 可以解析
def test_02_markdown_parsed(env, tmp_path):
    md = tmp_path / "req.md"
    md.write_text("# 登录模块\n- 支持手机号登录\n- 支持扫码登录", encoding="utf-8")
    text, _ = ingest(paths=[str(md)])
    assert "支持手机号登录" in text  # Ingestion 正确读取 md
    result = build_requirement_ir(
        FakeClient(_items("支持手机号登录", module="登录")),
        user_id=USER,
        title="md需求",
        raw_text=text,
        source_type=SourceType.MARKDOWN,
    )
    assert len(result.items) == 1


# ✅ 3. 图片可以进入多模态解析链
def test_03_image_enters_multimodal_chain(env, tmp_path):
    img = tmp_path / "ui.png"
    img.write_bytes(b"\x89PNG_fake_bytes")
    _, images = ingest(paths=[str(img)])
    assert len(images) == 1 and images[0]["media_type"] == "image/png"
    client = FakeClient(_items("图中登录按钮", module="UI", conf=0.7))
    parse_requirement(client, "看图理解", VID, images=images)
    assert client.calls[0]["images"] == images  # 图片确实传入 LLM 调用（多模态链）


# ✅ 4. 输出严格符合 Pydantic
def test_04_output_strictly_pydantic(env):
    resp = '{"items":[{"statement":"有效项","module":"m","confidence":0.9,"幻觉字段":"xxx"},{"no_statement":true}]}'
    result = build_requirement_ir(FakeClient(resp), user_id=USER, title="t", raw_text="x")
    assert all(isinstance(i, RequirementItem) for i in result.items)  # 严格 Pydantic 实例
    assert len(result.items) == 1  # 无 statement 的项被丢弃
    assert not hasattr(result.items[0], "幻觉字段")  # 噪声字段被白名单过滤


# ✅ 5. 过长需求能够自动分段
def test_05_long_requirement_auto_segments():
    long_text = "\n\n".join([f"需求段落{i}：" + "内容" * 40 for i in range(120)])
    assert len(split_segments(long_text)) > 1


# ✅ 6. 分段结果能合并
def test_06_segments_merged():
    long_text = "\n\n".join([f"段落{i}：" + "内容" * 40 for i in range(120)])
    client = FakeClient(_items("段内项"))
    result = parse_requirement(client, long_text, VID)
    assert result.segments > 1
    assert len(result.items) == result.segments  # 各段结果合并
    assert [i.seq for i in result.items] == list(range(1, len(result.items) + 1))  # seq 连续无断裂


# ✅ 7. 不产生明显重复 RequirementItem
def test_07_no_duplicate_items(env):
    resp = '{"items":[{"statement":"手机号11位","module":"登录","confidence":0.9},{"statement":"手机号 11 位","module":"登录","confidence":0.9}]}'
    result = build_requirement_ir(FakeClient(resp), user_id=USER, title="t", raw_text="x")
    assert len(result.items) == 1  # 归一化后视为重复
    assert result.validation.duplicates_removed == 1


# ✅ 8. 不凭空增加需求规则
def test_08_no_fabricated_rules_or_constraints(env):
    # LLM 只给 statement，未给 rules/fields/permissions
    r1 = build_requirement_ir(FakeClient(_items("简单功能")), user_id=USER, title="t", raw_text="x")
    it = r1.items[0]
    assert it.rules == [] and it.fields == [] and it.permissions == []  # 代码不臆造
    assert len(r1.items) == 1  # 不新增 item
    # LLM 给了字段但未给约束 → 不得编造 min/max/pattern
    resp = '{"items":[{"statement":"手机号","module":"m","type":"data_field","fields":[{"name":"phone","label":"手机号","data_type":"phone"}],"confidence":0.9}]}'
    r2 = build_requirement_ir(FakeClient(resp), user_id=USER, title="t", raw_text="x")
    f = r2.items[0].fields[0]
    assert f.min_length is None and f.max_length is None and f.pattern is None


# ✅ 9. 每个 RequirementItem 尽可能带 source_ref
def test_09_source_ref_populated(env):
    resp = '{"items":[{"statement":"手机号11位","module":"登录","source_ref":"手机号必须为11位","confidence":0.9},{"statement":"无溯源项","module":"登录","confidence":0.9}]}'
    result = build_requirement_ir(FakeClient(resp), user_id=USER, title="t", raw_text="x")
    with_ref = [i for i in result.items if i.source_ref is not None]
    assert len(with_ref) >= 1
    assert with_ref[0].source_ref.value == "手机号必须为11位"
    assert with_ref[0].source_ref.locator == "quote"


# ✅ 10. 低置信度能够识别
def test_10_low_confidence_identified(env):
    resp = '{"items":[{"statement":"含糊需求","module":"m","confidence":0.3},{"statement":"明确需求","module":"m","confidence":0.95}]}'
    result = build_requirement_ir(FakeClient(resp), user_id=USER, title="t", raw_text="x")
    assert len(result.validation.low_confidence_ids) == 1
    low = next(i for i in result.items if i.confidence == 0.3)
    assert low.confidence_level == ConfidenceLevel.LOW


# ✅ 11. 可以创建 Version 1
def test_11_create_version_1(env):
    result = build_requirement_ir(FakeClient('{"items":[]}'), user_id=USER, title="t", raw_text="x")
    assert result.version.version_no == 1
    assert repo.get_version(result.version.id).version_no == 1


# ✅ 12. 可以从已有 Doc 创建 Version 2
def test_12_create_version_2_from_existing_doc(env):
    client = FakeClient(_items("s"))
    r1 = build_requirement_ir(client, user_id=USER, title="t", raw_text="v1需求")
    r2 = build_requirement_ir(client, user_id=USER, title="t", raw_text="v2需求", doc=r1.doc)
    assert r2.version.version_no == 2
    assert r2.doc.id == r1.doc.id  # 同一需求身份
    assert [v.version_no for v in repo.list_versions(r1.doc.id)] == [1, 2]


# ✅ 13. Version 1 与 Version 2 数据互不覆盖
def test_13_versions_do_not_overwrite(env):
    r1 = build_requirement_ir(FakeClient(_items("v1项")), user_id=USER, title="t", raw_text="v1")
    r2 = build_requirement_ir(FakeClient(_items("v2项")), user_id=USER, title="t", raw_text="v2", doc=r1.doc)
    v1_items = repo.list_items(r1.version.id)
    v2_items = repo.list_items(r2.version.id)
    assert [i.statement for i in v1_items] == ["v1项"]  # v1 未被 v2 覆盖
    assert [i.statement for i in v2_items] == ["v2项"]
    assert r1.version.id != r2.version.id


# ✅ 14. Mock 全链端到端（解析→校验→持久化→回读）
def test_14_full_mock_pipeline(env):
    resp = (
        '{"items":[{"type":"data_field","module":"登录","statement":"手机号11位",'
        '"fields":[{"name":"phone","label":"手机号","data_type":"phone","min_length":11,"max_length":11}],'
        '"source_ref":"手机号必须11位","confidence":0.9}]}'
    )
    result = build_requirement_ir(
        FakeClient(resp), user_id=USER, title="登录", raw_text="手机号必须11位", source_type=SourceType.MARKDOWN
    )
    assert len(result.items) == 1
    persisted = repo.list_items(result.version.id)  # 回读
    assert len(persisted) == 1
    assert persisted[0].fields[0].max_length == 11  # 字段级 IR 落库
    assert persisted[0].source_ref.value == "手机号必须11位"
    assert persisted[0].provenance == Provenance.LLM
