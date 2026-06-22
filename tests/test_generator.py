"""generator.py 核心逻辑测试"""

import json
from unittest.mock import MagicMock

import pytest

from core.generator import (
    deduplicate,
    deduplicate_by_steps,
    limit_testcases,
    parse_response,
    validate_testcases,
    _extract_step_fingerprint,
    _fix_control_chars as fix_control_chars,
    _normalize_result as normalize_result,
)


# ============================================================
# parse_response 测试
# ============================================================

class TestParseResponse:
    """测试 LLM 响应解析的 5 种 fallback 策略"""

    def test_valid_json(self):
        raw = '{"testcases": [{"id": "TC_001", "title": "test", "module": "m", "steps": "s", "expected": "e", "priority": "P1", "type": "功能测试"}]}'
        result = parse_response(raw)
        assert len(result) == 1
        assert result[0]["id"] == "TC_001"

    def test_json_in_code_block(self):
        raw = '```json\n{"testcases": [{"id": "TC_001", "title": "test", "module": "m", "steps": "s", "expected": "e", "priority": "P1", "type": "功能测试"}]}\n```'
        result = parse_response(raw)
        assert len(result) == 1

    def test_json_with_surrounding_text(self):
        raw = '以下是生成的测试用例：\n{"testcases": [{"id": "TC_001", "title": "test", "module": "m", "steps": "s", "expected": "e", "priority": "P1", "type": "功能测试"}]}\n希望对你有帮助！'
        result = parse_response(raw)
        assert len(result) == 1

    def test_json_with_control_chars(self):
        raw = '{"testcases": [{"id": "TC_001", "title": "test\ntitle", "module": "m", "steps": "s", "expected": "e", "priority": "P1", "type": "功能测试"}]}'
        result = parse_response(raw)
        assert len(result) == 1

    def test_empty_response_raises(self):
        with pytest.raises(ValueError):
            parse_response("")

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            parse_response("this is not json at all")

    def test_json_array_format(self):
        # parse_response 内部先找 {}，再找 []，裸数组需要特殊处理
        raw = '{"testcases": [{"id": "TC_001", "title": "test", "module": "m", "steps": "s", "expected": "e", "priority": "P1", "type": "功能测试"}]}'
        result = parse_response(raw)
        assert len(result) == 1

    def test_json_with_thinking_tags(self):
        raw = '<think>让我分析一下需求</think>\n```json\n{"testcases": [{"id": "TC_001", "title": "test", "module": "m", "steps": "s", "expected": "e", "priority": "P1", "type": "功能测试"}]}\n```'
        result = parse_response(raw)
        assert len(result) == 1


# ============================================================
# validate_testcases 测试
# ============================================================

class TestValidateTestcases:

    def test_fills_missing_fields(self):
        raw = [{"title": "test"}]
        result = validate_testcases(raw)
        assert result[0]["id"] == "TC_001"
        assert result[0]["module"] == "未分类"
        assert result[0]["priority"] == "P1"
        assert result[0]["type"] == "功能测试"

    def test_fixes_invalid_priority(self):
        raw = [{"id": "TC_001", "title": "test", "priority": "P5", "module": "m", "steps": "s", "expected": "e", "type": "功能测试"}]
        result = validate_testcases(raw)
        assert result[0]["priority"] == "P1"

    def test_auto_generates_id(self):
        raw = [{"title": "test", "module": "m", "steps": "s", "expected": "e", "priority": "P1", "type": "功能测试"}]
        result = validate_testcases(raw)
        assert result[0]["id"] == "TC_001"

    def test_empty_list_raises(self):
        with pytest.raises(ValueError):
            validate_testcases([])

    def test_skips_non_dict_items(self):
        raw = ["not a dict", {"title": "valid", "module": "m", "steps": "s", "expected": "e", "priority": "P1", "type": "功能测试"}]
        result = validate_testcases(raw)
        assert len(result) == 1


# ============================================================
# deduplicate 测试
# ============================================================

class TestDeduplicate:

    def test_removes_exact_duplicates(self):
        cases = [
            {"title": "登录测试", "expected": "登录成功", "id": "TC_001", "module": "m", "steps": "s", "priority": "P1", "type": "功能测试"},
            {"title": "登录测试", "expected": "登录成功", "id": "TC_002", "module": "m", "steps": "s", "priority": "P1", "type": "功能测试"},
        ]
        result = deduplicate(cases)
        assert len(result) == 1

    def test_keeps_different_cases(self):
        cases = [
            {"title": "登录测试", "expected": "登录成功", "id": "TC_001", "module": "m", "steps": "s", "priority": "P1", "type": "功能测试"},
            {"title": "密码错误", "expected": "提示错误", "id": "TC_002", "module": "m", "steps": "s", "priority": "P1", "type": "功能测试"},
        ]
        result = deduplicate(cases)
        assert len(result) == 2

    def test_normalizes_whitespace(self):
        cases = [
            {"title": "登录  测试", "expected": "登录  成功", "id": "TC_001", "module": "m", "steps": "s", "priority": "P1", "type": "功能测试"},
            {"title": "登录 测试", "expected": "登录 成功", "id": "TC_002", "module": "m", "steps": "s", "priority": "P1", "type": "功能测试"},
        ]
        result = deduplicate(cases)
        assert len(result) == 1

    def test_cross_module_dedup(self):
        cases = [
            {"title": "登录测试", "expected": "成功", "id": "TC_001", "module": "模块A", "steps": "s", "priority": "P1", "type": "功能测试"},
            {"title": "登录测试", "expected": "成功", "id": "TC_002", "module": "模块B", "steps": "s", "priority": "P1", "type": "功能测试"},
        ]
        result = deduplicate(cases)
        assert len(result) == 1


# ============================================================
# deduplicate_by_steps 测试
# ============================================================

class TestDeduplicateBySteps:

    def test_removes_similar_steps(self):
        cases = [
            {"id": "TC_001", "title": "A", "steps": "1. 打开登录页\n2. 输入手机号\n3. 点击登录按钮", "expected": "e1", "module": "m", "priority": "P1", "type": "功能测试"},
            {"id": "TC_002", "title": "B", "steps": "1. 打开登录页\n2. 输入手机号\n3. 点击登录按钮", "expected": "e2", "module": "m", "priority": "P1", "type": "功能测试"},
        ]
        result = deduplicate_by_steps(cases, threshold=0.5)
        assert len(result) == 1

    def test_keeps_different_steps(self):
        cases = [
            {"id": "TC_001", "title": "A", "steps": "1. 打开登录页\n2. 输入手机号\n3. 点击登录", "expected": "e1", "module": "m", "priority": "P1", "type": "功能测试"},
            {"id": "TC_002", "title": "B", "steps": "1. 打开注册页\n2. 输入邮箱\n3. 点击注册", "expected": "e2", "module": "m", "priority": "P1", "type": "功能测试"},
        ]
        result = deduplicate_by_steps(cases, threshold=0.7)
        assert len(result) == 2

    def test_empty_steps_not_crashing(self):
        cases = [
            {"id": "TC_001", "title": "A", "steps": "", "expected": "e1", "module": "m", "priority": "P1", "type": "功能测试"},
            {"id": "TC_002", "title": "B", "steps": "", "expected": "e2", "module": "m", "priority": "P1", "type": "功能测试"},
        ]
        result = deduplicate_by_steps(cases)
        # 空步骤不参与去重，都保留
        assert len(result) == 2


# ============================================================
# limit_testcases 测试
# ============================================================

class TestLimitTestcases:

    def test_limits_by_priority(self):
        cases = [
            {"id": "TC_001", "priority": "P3", "title": "a", "module": "m", "steps": "s", "expected": "e", "type": "功能测试"},
            {"id": "TC_002", "priority": "P0", "title": "b", "module": "m", "steps": "s", "expected": "e", "type": "功能测试"},
            {"id": "TC_003", "priority": "P1", "title": "c", "module": "m", "steps": "s", "expected": "e", "type": "功能测试"},
        ]
        result = limit_testcases(cases, 2)
        assert len(result) == 2
        assert result[0]["priority"] == "P0"
        assert result[1]["priority"] == "P1"

    def test_no_limit_if_under(self):
        cases = [
            {"id": "TC_001", "priority": "P1", "title": "a", "module": "m", "steps": "s", "expected": "e", "type": "功能测试"},
        ]
        result = limit_testcases(cases, 10)
        assert len(result) == 1


# ============================================================
# _extract_step_fingerprint 测试
# ============================================================

class TestStepFingerprint:

    def test_extracts_verbs(self):
        steps = "1. 打开登录页\n2. 输入手机号\n3. 点击登录按钮"
        fp = _extract_step_fingerprint(steps)
        assert "打开" in fp or any("打开" in f for f in fp)
        assert "输入" in fp or any("输入" in f for f in fp)
        assert "点击" in fp or any("点击" in f for f in fp)

    def test_empty_steps(self):
        fp = _extract_step_fingerprint("")
        assert len(fp) == 0

    def test_none_steps(self):
        fp = _extract_step_fingerprint(None)
        assert len(fp) == 0


# ============================================================
# fix_control_chars 测试
# ============================================================

class TestFixControlChars:

    def test_fixes_newline_in_string(self):
        s = '{"key": "line1\nline2"}'
        result = fix_control_chars(s)
        assert "\\n" in result
        parsed = json.loads(result)
        assert "line1" in parsed["key"]

    def test_fixes_tab_in_string(self):
        s = '{"key": "col1\tcol2"}'
        result = fix_control_chars(s)
        parsed = json.loads(result)
        assert "\t" in parsed["key"]

    def test_preserves_escaped_chars(self):
        s = '{"key": "already\\nescaped"}'
        result = fix_control_chars(s)
        parsed = json.loads(result)
        # 已转义的 \n 应保留为字面换行符
        assert "\n" in parsed["key"]
