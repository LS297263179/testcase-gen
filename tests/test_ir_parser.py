"""V2 需求解析器测试 - JSON 提取鲁棒性、代码修复、超长分段、mock LLM 全链。

对应 Step 2；LLM 用 FakeClient mock，不依赖真实 API。
"""

from core.schemas import DataType, RequirementItemType
from core.v2.parser import (
    SEGMENT_MAX_CHARS,
    coerce_item,
    extract_json,
    parse_requirement,
    split_segments,
)

VERSION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class FakeClient:
    """按调用次序返回预置响应；记录每次调用参数"""

    def __init__(self, responses):
        self.responses = responses if isinstance(responses, list) else [responses]
        self.calls = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        self.calls.append({"system": system_prompt, "user": user_prompt, "images": images})
        idx = len(self.calls) - 1
        resp = self.responses[idx] if idx < len(self.responses) else self.responses[-1]
        if isinstance(resp, Exception):
            raise resp
        return resp


# ============================================================
# extract_json 鲁棒性
# ============================================================


class TestExtractJson:
    def test_code_block(self):
        assert extract_json('```json\n{"items": []}\n```') == {"items": []}

    def test_bare_json(self):
        assert extract_json('{"items": []}') == {"items": []}

    def test_json_with_surrounding_prose(self):
        assert extract_json('好的，结果如下：{"items": [{"a": 1}]} 希望有帮助') == {"items": [{"a": 1}]}

    def test_bare_array(self):
        assert extract_json('[{"a": 1}]') == [{"a": 1}]

    def test_json5_trailing_comma(self):
        # json5 容忍尾逗号
        assert extract_json('{"items": [{"a": 1},]}') == {"items": [{"a": 1}]}

    def test_control_chars_stripped(self):
        assert extract_json('{"items": [\x01{"a": 1}\x02]}') == {"items": [{"a": 1}]}

    def test_invalid_returns_none(self):
        assert extract_json("完全不是 JSON") is None

    def test_empty_returns_none(self):
        assert extract_json("") is None

    # --- F1：多围栏 / 回显围栏容错（离线复现 bc_02 / bc_03 的 IR 静默 0 条；零 LLM） ---

    _ITEMS = {"items": [{"module": "订单", "type": "function", "statement": "用户可发起售后"}]}
    _ITEMS_JSON = '{"items":[{"module":"订单","type":"function","statement":"用户可发起售后"}]}'

    def test_f1_form1_normal_single_json_fence(self):
        """形态①：正常单 JSON 围栏 → 行为必须不变（本轮通过 IR 的 8 个 case 皆为此形态）"""
        assert extract_json(f"结果：\n```json\n{self._ITEMS_JSON}\n```") == self._ITEMS

    def test_f1_form2_echoed_fence_before_json_fence(self):
        """形态②：模型回显需求正文里的围栏，其后才是 JSON 围栏 → 旧实现取首块导致丢负载"""
        resp = f"状态流转：\n```\n待支付 → 已支付\n```\n\n据此解析：\n```json\n{self._ITEMS_JSON}\n```"
        assert extract_json(resp) == self._ITEMS

    def test_f1_form3_echoed_fence_then_bare_json(self):
        """形态③：回显围栏 + 裸 JSON（未用围栏包裹）→ 应解析成功"""
        resp = f"状态：\n```\nNOT_APPLIED → PENDING\n```\n\n{self._ITEMS_JSON}"
        assert extract_json(resp) == self._ITEMS

    def test_f1_form4_no_json_anywhere_stays_none(self):
        """形态④：响应里确实没有 JSON → 仍返回 None，禁止把围栏正文当作解析结果"""
        assert extract_json("状态：\n```\n待支付 → 已支付\n```\n\n以上即全部") is None

    def test_f1_form5_multiple_non_json_fences_then_json(self):
        """形态⑤：多个非 JSON 围栏 + 尾部 JSON → 应解析成功"""
        resp = f"```\nA → B\n```\n说明\n```\nC → D\n```\n{self._ITEMS_JSON}"
        assert extract_json(resp) == self._ITEMS


# ============================================================
# coerce_item 代码修复
# ============================================================


class TestCoerceItem:
    def test_infers_type_from_fields(self):
        item, err = coerce_item(
            {"statement": "手机号校验", "fields": [{"name": "phone", "label": "手机", "data_type": "phone"}]},
            VERSION_ID,
            1,
        )
        assert err is None
        assert item.type == RequirementItemType.DATA_FIELD
        assert item.fields[0].data_type == DataType.PHONE

    def test_infers_permission_type(self):
        item, _ = coerce_item(
            {"statement": "权限", "permissions": [{"role": "admin", "resource": "x", "action": "y", "allowed": True}]},
            VERSION_ID,
            1,
        )
        assert item.type == RequirementItemType.PERMISSION

    def test_drops_missing_statement(self):
        item, err = coerce_item({"module": "m"}, VERSION_ID, 1)
        assert item is None and "statement" in err

    def test_drops_non_dict(self):
        item, err = coerce_item("不是对象", VERSION_ID, 1)
        assert item is None and err == "非对象"

    def test_clamps_confidence_high(self):
        item, _ = coerce_item({"statement": "s", "confidence": 5}, VERSION_ID, 1)
        assert item.confidence == 1.0

    def test_clamps_confidence_invalid(self):
        item, _ = coerce_item({"statement": "s", "confidence": "abc"}, VERSION_ID, 1)
        assert item.confidence == 0.5

    def test_filters_extra_keys(self):
        # LLM 多给的噪声键被白名单过滤，不触发 extra=forbid
        item, err = coerce_item({"statement": "s", "foo": "bar", "hallucinated": 123}, VERSION_ID, 1)
        assert err is None and item.statement == "s"

    def test_invalid_priority_dropped(self):
        item, _ = coerce_item({"statement": "s", "priority_hint": "P9"}, VERSION_ID, 1)
        assert item.priority_hint is None

    def test_invalid_type_reinferred(self):
        item, _ = coerce_item(
            {"statement": "s", "type": "瞎写的类型", "fields": [{"name": "a", "label": "A", "data_type": "string"}]},
            VERSION_ID,
            1,
        )
        assert item.type == RequirementItemType.DATA_FIELD

    def test_seq_and_version_assigned(self):
        item, _ = coerce_item({"statement": "s"}, VERSION_ID, 7)
        assert item.seq == 7 and item.version_id == VERSION_ID


# ============================================================
# 超长自动分段
# ============================================================


class TestSegmentation:
    def test_short_text_single_segment(self):
        assert split_segments("短需求") == ["短需求"]

    def test_long_text_split(self):
        long_text = "\n\n".join([f"需求段落{i}：" + "内容" * 40 for i in range(120)])
        assert len(long_text) > SEGMENT_MAX_CHARS
        segs = split_segments(long_text)
        assert len(segs) > 1
        assert all(len(s) <= SEGMENT_MAX_CHARS for s in segs)

    def test_single_huge_paragraph_hard_split(self):
        huge = "字" * (SEGMENT_MAX_CHARS * 2 + 100)  # 无空行，单段超长
        segs = split_segments(huge)
        assert len(segs) > 1
        assert all(len(s) <= SEGMENT_MAX_CHARS for s in segs)


# ============================================================
# parse_requirement（mock LLM）
# ============================================================


class TestParseRequirement:
    def test_single_segment_parse(self):
        resp = '{"items": [{"type":"data_field","module":"登录","statement":"手机号11位","fields":[{"name":"phone","label":"手机号","data_type":"phone","min_length":11,"max_length":11}],"confidence":0.9}]}'
        result = parse_requirement(FakeClient(resp), "手机号11位", VERSION_ID)
        assert result.segments == 1
        assert len(result.items) == 1
        assert result.items[0].fields[0].max_length == 11
        assert result.items[0].seq == 1

    def test_malformed_json_records_issue(self):
        result = parse_requirement(FakeClient("这不是JSON"), "x", VERSION_ID)
        assert result.items == []
        assert any("JSON" in i for i in result.issues)

    def test_llm_exception_handled(self):
        result = parse_requirement(FakeClient(RuntimeError("API 超时")), "x", VERSION_ID)
        assert result.items == []
        assert any("LLM 调用失败" in i for i in result.issues)

    def test_invalid_items_dropped_valid_kept(self):
        resp = '{"items": [{"statement":"有效项"},{"module":"无statement项"}]}'
        result = parse_requirement(FakeClient(resp), "x", VERSION_ID)
        assert len(result.items) == 1
        assert any("丢弃" in i for i in result.issues)

    def test_segmented_parse_merges_with_continuous_seq(self):
        long_text = "\n\n".join([f"需求段落{i}：" + "内容" * 40 for i in range(120)])
        resp = '{"items": [{"statement":"段落项","module":"m"}]}'
        client = FakeClient(resp)  # 每段都返回同一条
        result = parse_requirement(client, long_text, VERSION_ID)
        assert result.segments > 1
        assert len(client.calls) == result.segments  # 每段一次 LLM 调用
        assert len(result.items) == result.segments
        assert [it.seq for it in result.items] == list(range(1, result.segments + 1))  # seq 全局连续

    def test_images_only_first_segment(self):
        long_text = "\n\n".join([f"段落{i}：" + "内容" * 40 for i in range(120)])
        imgs = [{"data": "xxx", "media_type": "image/png"}]
        client = FakeClient('{"items": [{"statement":"s"}]}')
        result = parse_requirement(client, long_text, VERSION_ID, images=imgs)
        assert result.segments > 1
        assert client.calls[0]["images"] == imgs  # 首段带图
        assert all(c["images"] is None for c in client.calls[1:])  # 后续段不带图
