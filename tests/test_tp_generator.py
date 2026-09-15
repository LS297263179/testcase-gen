"""V2 Step 3 测试点生成器测试 - Phase A/B 的 JSON 提取、分批、异常处理。

对应 Step 3；LLM 用 FakeClient mock，不依赖真实 API。
"""

from core.schemas import (
    DataType,
    FieldSpec,
    Priority,
    RequirementItem,
    RequirementItemType,
)
from core.v2.tp_generator import (
    GeneratorResult,
    complete_cross_item,
    generate_for_item,
)
from core.v2.tp_prompts import (
    TEST_POINT_COMPLETER_PROMPT,
    TEST_POINT_GENERATOR_PROMPT,
)

VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class FakeClient:
    """按调用次序返回预置响应；记录每次调用参数"""

    def __init__(self, responses):
        self.responses = responses if isinstance(responses, list) else [responses]
        self.calls: list[dict] = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls.append({"system": system_prompt, "user": user_prompt, "images": images})
        idx = len(self.calls) - 1
        resp = self.responses[idx] if idx < len(self.responses) else self.responses[-1]
        if isinstance(resp, Exception):
            raise resp
        return resp


def _make_item(item_id: str, module: str = "登录", statement: str = "手机号 11 位") -> RequirementItem:
    return RequirementItem(
        id=item_id,
        version_id=VERSION_ID,
        seq=1,
        type=RequirementItemType.DATA_FIELD,
        module=module,
        statement=statement,
        fields=[FieldSpec(name="phone", label="手机号", data_type=DataType.PHONE, required=True)],
        priority_hint=Priority.P1,
        confidence=0.9,
    )


# ============================================================
# Phase A：generate_for_item
# ============================================================


class TestPhaseAGenerator:
    def test_extracts_json_from_code_block(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        resp = """```json
{"test_points": [
  {"module": "登录", "subcategory": "输入校验", "title": "手机号长度", "description": "验证 11 位", "dimension": "boundary", "priority": "P1"}
]}
```"""
        client = FakeClient(resp)
        result = generate_for_item(client, item)
        assert isinstance(result, GeneratorResult)
        assert result.calls == 1
        assert len(result.raw_points) == 1
        assert result.raw_points[0]["title"] == "手机号长度"
        assert not result.issues

    def test_extracts_bare_json(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        resp = '{"test_points": [{"module":"登录","subcategory":"s","title":"t","description":"d","dimension":"functional","priority":"P1"}]}'
        client = FakeClient(resp)
        result = generate_for_item(client, item)
        assert len(result.raw_points) == 1

    def test_llm_exception_captured(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        client = FakeClient(RuntimeError("API 超时"))
        result = generate_for_item(client, item)
        assert result.raw_points == []
        assert any("LLM 调用失败" in i for i in result.issues)

    def test_invalid_json_returns_issue(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        client = FakeClient("完全不是 JSON")
        result = generate_for_item(client, item)
        assert result.raw_points == []
        assert any("无法解析 JSON" in i for i in result.issues)

    def test_missing_test_points_key(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        client = FakeClient('{"foo": "bar"}')
        result = generate_for_item(client, item)
        assert result.raw_points == []
        assert any("test_points" in i for i in result.issues)

    def test_non_dict_elements_dropped(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        resp = '{"test_points": [{"title":"t","description":"d","module":"m","subcategory":"s","dimension":"functional","priority":"P1"}, "字符串", 123]}'
        client = FakeClient(resp)
        result = generate_for_item(client, item)
        assert len(result.raw_points) == 1
        assert any("丢弃" in i for i in result.issues)

    def test_empty_test_points_ok(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        client = FakeClient('{"test_points": []}')
        result = generate_for_item(client, item)
        assert result.raw_points == []
        assert not result.issues

    def test_prompt_contains_item_id_hint(self):
        """Phase A 的 user prompt 必须显式包含 item.id，方便代码侧兜底覆写 item_ids"""
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        client = FakeClient('{"test_points": []}')
        generate_for_item(client, item)
        assert "01ARZ3NDEKTSV4RRFFQ69G5FAV" in client.calls[0]["user"]

    def test_system_prompt_is_generator_v1(self):
        item = _make_item("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        client = FakeClient('{"test_points": []}')
        generate_for_item(client, item)
        assert client.calls[0]["system"] == TEST_POINT_GENERATOR_PROMPT


# ============================================================
# Phase B：complete_cross_item
# ============================================================


class TestPhaseBGenerator:
    def _make_items(self, n: int, module: str = "订单") -> list[RequirementItem]:
        return [_make_item(f"01ARZ3NDEKTSV4RRFFQ69G5F{i:02d}", module=module, statement=f"item {i}") for i in range(n)]

    def test_single_call_when_below_threshold(self):
        items = self._make_items(3)
        resp = """```json
{"test_points": [
  {"item_ids": ["01ARZ3NDEKTSV4RRFFQ69G5F00","01ARZ3NDEKTSV4RRFFQ69G5F01"],
   "module": "订单", "subcategory": "字段联动", "title": "总价与库存联动",
   "description": "下单时联动扣减", "dimension": "linkage", "priority": "P0"}
]}
```"""
        client = FakeClient(resp)
        result = complete_cross_item(client, items, existing_points=[])
        assert result.calls == 1
        assert len(result.raw_points) == 1
        assert result.raw_points[0]["title"] == "总价与库存联动"

    def test_batches_by_module_when_above_threshold(self):
        # 构造 2 个 module，各 20 个 items → 总 40 > 30，触发分批
        items_a = self._make_items(20, module="订单")
        items_b = [
            _make_item(f"01ARZ3NDEKTSV4RRFFQ69G5B{i:02d}", module="支付", statement=f"pay {i}") for i in range(20)
        ]
        all_items = items_a + items_b
        # 每批返回一个候选
        resp = '{"test_points": [{"item_ids":["x","y"],"module":"m","subcategory":"s","title":"t","description":"d","dimension":"linkage","priority":"P1"}]}'
        client = FakeClient([resp, resp])
        result = complete_cross_item(client, all_items, existing_points=[], batch_threshold=30)
        assert result.calls == 2  # 2 个 module → 2 次调用
        assert len(result.raw_points) == 2  # 每批 1 个 → 合计 2

    def test_empty_items_returns_issue(self):
        client = FakeClient('{"test_points": []}')
        result = complete_cross_item(client, [], existing_points=[])
        assert result.calls == 0
        assert any("无 items" in i for i in result.issues)

    def test_llm_exception_captured(self):
        items = self._make_items(2)
        client = FakeClient(RuntimeError("API 挂了"))
        result = complete_cross_item(client, items, existing_points=[])
        assert result.raw_points == []
        assert any("LLM 调用失败" in i for i in result.issues)

    def test_invalid_json_returns_issue(self):
        items = self._make_items(2)
        client = FakeClient("不是 JSON")
        result = complete_cross_item(client, items, existing_points=[])
        assert result.raw_points == []
        assert any("无法解析 JSON" in i for i in result.issues)

    def test_prompt_contains_items_and_phase_a_summary(self):
        items = self._make_items(2)
        # 传 dict 形式的 Phase A 摘要（orchestrator 尚未转 TestPoint 时也能工作）
        existing = [
            {
                "item_ids": [items[0].id],
                "module": "订单",
                "subcategory": "输入校验",
                "title": "手机号长度",
                "description": "d",
                "dimension": "boundary",
            }
        ]
        client = FakeClient('{"test_points": []}')
        complete_cross_item(client, items, existing_points=existing)
        user_prompt = client.calls[0]["user"]
        assert items[0].id in user_prompt
        assert items[1].id in user_prompt
        assert "手机号长度" in user_prompt

    def test_system_prompt_is_completer_v1(self):
        items = self._make_items(2)
        client = FakeClient('{"test_points": []}')
        complete_cross_item(client, items, existing_points=[])
        assert client.calls[0]["system"] == TEST_POINT_COMPLETER_PROMPT

    def test_threshold_configurable(self):
        """batch_threshold 参数可覆盖默认值"""
        items = self._make_items(3)
        # 阈值设为 2 → 3 items 触发分批（按 module 分组，只有一个 module → 1 批）
        client = FakeClient('{"test_points": []}')
        result = complete_cross_item(client, items, existing_points=[], batch_threshold=2)
        # 单 module 分批只调 1 次
        assert result.calls == 1
