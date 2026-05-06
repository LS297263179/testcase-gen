"""测试用例生成模块 - Prompt 模板 + 用例解析"""

import json
import re

from llm_client import LLMClient

SYSTEM_PROMPT = """你是一名资深软件测试工程师。你的任务是根据需求文档编写高质量的测试用例。

要求：
1. 用例覆盖全面，包含正常流程、边界条件、异常场景
2. 每条用例包含：用例编号、模块、标题、前置条件、测试步骤、预期结果、优先级、用例类型
3. 优先级分为：P0(阻塞)、P1(严重)、P2(一般)、P3(轻微)
4. 用例类型包括：功能测试、边界测试、异常测试、兼容性测试、性能测试
5. 测试步骤要具体可执行，预期结果要明确可验证
6. 步骤和预期结果中不要使用换行符，用分号或句号分隔

请严格按以下 JSON 格式输出，不要输出其他内容：
```json
{
  "testcases": [
    {
      "id": "TC_001",
      "module": "模块名称",
      "title": "用例标题",
      "precondition": "前置条件",
      "steps": "1. xxx; 2. xxx; 3. xxx",
      "expected": "预期结果",
      "priority": "P1",
      "type": "功能测试"
    }
  ]
}
```"""

USER_PROMPT_TEMPLATE = """请根据以下需求文档生成测试用例：

---需求文档开始---
{requirement}
---需求文档结束---

补充要求：
- 默认优先级：{default_priority}
- 重点覆盖的用例类型：{case_types}
- 请尽量细化测试步骤，确保可执行性"""

USER_PROMPT_WITH_IMAGE = """请根据以下需求描述和图片生成测试用例。

{requirement_text}

补充要求：
- 默认优先级：{default_priority}
- 重点覆盖的用例类型：{case_types}
- 请仔细分析图片中的界面元素、交互流程、业务规则
- 请尽量细化测试步骤，确保可执行性"""


def generate_testcases(client: LLMClient, requirement: str,
                       default_priority: str = "P1",
                       case_types: list[str] | None = None,
                       images: list[dict] | None = None) -> list[dict]:
    """调用 LLM 生成测试用例并解析，失败时自动重试生成
    images: [{"data": "base64...", "media_type": "image/png"}]
    """
    if case_types is None:
        case_types = ["功能测试", "边界测试", "异常测试"]

    if images:
        text_part = requirement if requirement else "请根据图片中的界面/需求生成测试用例。"
        user_prompt = USER_PROMPT_WITH_IMAGE.format(
            requirement_text=text_part,
            default_priority=default_priority,
            case_types="、".join(case_types),
        )
    else:
        user_prompt = USER_PROMPT_TEMPLATE.format(
            requirement=requirement,
            default_priority=default_priority,
            case_types="、".join(case_types),
        )

    max_attempts = 3
    last_error = None
    for attempt in range(max_attempts):
        raw = client.chat(SYSTEM_PROMPT, user_prompt, images=images)
        try:
            return _parse_response(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            if attempt < max_attempts - 1:
                continue

    raise ValueError(f"JSON 解析失败（已重试 {max_attempts} 次）: {last_error}")


def _parse_response(raw: str) -> list[dict]:
    """从 LLM 响应中提取 JSON 测试用例"""
    # 尝试提取 JSON 块
    match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw

    # 去掉首尾非 JSON 字符
    start = json_str.find("{")
    end = json_str.rfind("}") + 1
    if start >= 0 and end > start:
        json_str = json_str[start:end]

    # 依次尝试多种解析方式
    for parser in [_try_json_loads, _try_fix_control_chars, _try_json5_loads, _try_regex_extract]:
        result = parser(json_str)
        if result is not None:
            return _normalize_result(result)

    raise ValueError(f"无法解析 LLM 返回的 JSON")


def _try_json_loads(s: str):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _try_fix_control_chars(s: str):
    """修复 JSON 中的未转义控制字符后解析"""
    fixed = _fix_control_chars(s)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def _try_json5_loads(s: str):
    """尝试用 json5 解析（支持尾逗号、单引号等）"""
    try:
        import json5
        return json5.loads(s)
    except Exception:
        return None


def _try_regex_extract(s: str):
    """用正则直接提取每个测试用例对象"""
    pattern = r'\{[^{}]*"id"\s*:\s*"[^"]*"[^{}]*\}'
    matches = re.findall(pattern, s, re.DOTALL)
    if not matches:
        return None
    cases = []
    for m in matches:
        try:
            cases.append(json.loads(m))
        except json.JSONDecodeError:
            fixed = _fix_control_chars(m)
            try:
                cases.append(json.loads(fixed))
            except json.JSONDecodeError:
                continue
    return {"testcases": cases} if cases else None


def _normalize_result(data) -> list[dict]:
    if isinstance(data, dict) and "testcases" in data:
        return data["testcases"]
    if isinstance(data, list):
        return data
    return None


def _fix_control_chars(s: str) -> str:
    """修复 JSON 字符串中的未转义控制字符"""
    result = []
    in_string = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '"' and (i == 0 or s[i - 1] != '\\'):
            in_string = not in_string
            result.append(c)
        elif in_string and c in ('\n', '\r', '\t'):
            if c == '\n':
                result.append('\\n')
            elif c == '\r':
                result.append('\\r')
            elif c == '\t':
                result.append('\\t')
        else:
            result.append(c)
        i += 1
    return ''.join(result)
