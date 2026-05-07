"""测试用例生成模块 - Prompt 模板 + 用例解析，支持分段生成"""

import json
import re
from typing import Callable

from llm_client import LLMClient

# ============================================================
# Step 1: 需求分析 Prompt — 拆解模块和测试维度
# ============================================================
ANALYSIS_PROMPT = """你是一名资深软件测试架构师。请分析以下需求，拆解出独立的功能模块和每个模块需要覆盖的测试维度。

输出格式（严格 JSON）：
```json
{
  "modules": [
    {
      "name": "模块名称",
      "description": "模块简述",
      "dimensions": ["功能测试", "边界测试", "异常测试"]
    }
  ]
}
```

维度包括：功能测试、边界测试、异常测试、兼容性测试、性能测试。根据模块特点选择合适的维度。"""

# ============================================================
# Step 2: 分模块生成 Prompt
# ============================================================
MODULE_PROMPT = """你是一名资深软件测试工程师。请针对「{module_name}」模块生成测试用例。

模块描述：{module_desc}
需要覆盖的维度：{dimensions}

## 完整需求（供参考）
---需求开始---
{requirement}
---需求结束---

## 用例编写规范
1. 每条用例包含：用例编号、模块、标题、前置条件、测试步骤、预期结果、优先级、用例类型
2. 用例编号格式为 TC_XXX（后续会统一编号）
3. 优先级分为：P0(阻塞)、P1(严重)、P2(一般)、P3(轻微)
4. 测试步骤每步一行，格式为 "1. 操作描述"、"2. 操作描述"
5. 预期结果写最终的整体预期
6. 每个维度至少覆盖 3-5 条用例，确保覆盖充分

## 输出格式（严格 JSON）：
```json
{{
  "testcases": [
    {{
      "id": "TC_001",
      "module": "{module_name}",
      "title": "用例标题",
      "precondition": "前置条件",
      "steps": "1. 操作步骤一\\n2. 操作步骤二",
      "expected": "预期结果",
      "priority": "P1",
      "type": "功能测试"
    }}
  ]
}}
```"""

# ============================================================
# 原有的一次性生成 Prompt（保留作为备选）
# ============================================================
SYSTEM_PROMPT = """你是一名资深软件测试工程师。你的任务是根据需求文档编写高质量的测试用例。

## 用例编写规范
1. 用例覆盖全面，包含正常流程、边界条件、异常场景
2. 每条用例包含：用例编号、模块、标题、前置条件、测试步骤、预期结果、优先级、用例类型
3. 用例编号从 TC_001 开始，按顺序递增
4. 优先级分为：P0(阻塞)、P1(严重)、P2(一般)、P3(轻微)
5. 用例类型包括：功能测试、边界测试、异常测试、兼容性测试、性能测试
6. 测试步骤每步一行，格式为 "1. 操作描述"、"2. 操作描述"
7. 预期结果写最终的整体预期，不要写每步的预期

## 输出格式
请严格按以下 JSON 格式输出，不要输出其他内容：
```json
{
  "testcases": [
    {
      "id": "TC_001",
      "module": "模块名称",
      "title": "用例标题",
      "precondition": "前置条件",
      "steps": "1. 打开登录页面\\n2. 输入手机号 13800138000\\n3. 点击获取验证码\\n4. 输入验证码 123456\\n5. 点击登录按钮",
      "expected": "登录成功，跳转到首页",
      "priority": "P1",
      "type": "功能测试"
    }
  ]
}
```

## 示例
需求：用户通过手机号+验证码登录系统，手机号为11位，验证码为6位数字。

输出：
```json
{
  "testcases": [
    {
      "id": "TC_001",
      "module": "用户登录",
      "title": "手机号验证码正常登录",
      "precondition": "用户已注册，手机号为13800138000",
      "steps": "1. 打开登录页面\\n2. 输入手机号 13800138000\\n3. 点击获取验证码\\n4. 输入收到的6位验证码\\n5. 点击登录按钮",
      "expected": "登录成功，页面跳转到系统首页",
      "priority": "P1",
      "type": "功能测试"
    },
    {
      "id": "TC_002",
      "module": "用户登录",
      "title": "手机号位数不足11位",
      "precondition": "进入登录页面",
      "steps": "1. 打开登录页面\\n2. 输入手机号 13800138（10位）\\n3. 点击获取验证码",
      "expected": "提示手机号格式错误，无法获取验证码",
      "priority": "P1",
      "type": "边界测试"
    },
    {
      "id": "TC_003",
      "module": "用户登录",
      "title": "验证码过期后登录",
      "precondition": "用户已获取验证码且已超过5分钟",
      "steps": "1. 打开登录页面\\n2. 输入手机号 13800138000\\n3. 点击获取验证码\\n4. 等待5分钟以上\\n5. 输入验证码\\n6. 点击登录按钮",
      "expected": "提示验证码已过期，请重新获取",
      "priority": "P2",
      "type": "异常测试"
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
- 请仔细分析图片中的以下内容：
  1. 页面布局和导航结构
  2. 表单字段（输入框、下拉框、单选/多选等）及其校验规则
  3. 按钮和交互元素（提交、取消、弹窗等）
  4. 数据展示区域（列表、详情、分页等）
  5. 状态变化（空状态、加载中、错误状态等）
- 请尽量细化测试步骤，确保可执行性"""


def generate_testcases(client: LLMClient, requirement: str,
                       default_priority: str = "P1",
                       case_types: list[str] | None = None,
                       images: list[dict] | None = None,
                       image_client: LLMClient | None = None,
                       on_progress: Callable[[str], None] | None = None) -> list[dict]:
    """分段生成测试用例：先分析模块，再按模块逐一生成，最后合并去重。
    on_progress: 进度回调，用于通知前端当前步骤
    """
    if case_types is None:
        case_types = ["功能测试", "边界测试", "异常测试"]

    active_client = image_client if (images and image_client) else client

    # Step 1: 分析需求，拆解模块
    if on_progress:
        on_progress("正在分析需求，拆解功能模块...")

    modules = _analyze_modules(active_client, requirement, case_types, images)

    if not modules:
        # 分析失败，回退到一次性生成
        if on_progress:
            on_progress("模块分析失败，使用一次性生成模式...")
        return _generate_all_in_one(active_client, requirement, default_priority, case_types, images)

    # Step 2: 按模块分段生成
    all_testcases = []
    for i, mod in enumerate(modules):
        if on_progress:
            on_progress(f"正在生成「{mod['name']}」模块的测试用例 ({i + 1}/{len(modules)})...")

        cases = _generate_for_module(
            active_client, requirement, mod, default_priority
        )
        all_testcases.extend(cases)

    if not all_testcases:
        raise ValueError("分段生成未产出任何用例")

    # Step 3: 去重并统一编号
    all_testcases = _deduplicate(all_testcases)
    for i, tc in enumerate(all_testcases):
        tc["id"] = f"TC_{i + 1:03d}"

    return all_testcases


def _analyze_modules(client: LLMClient, requirement: str,
                     case_types: list[str], images: list[dict] | None = None) -> list[dict]:
    """Step 1: 分析需求，拆解功能模块和测试维度"""
    prompt = f"""请分析以下需求，拆解出独立的功能模块：

---需求开始---
{requirement}
---需求结束---

需要覆盖的测试维度：{"、".join(case_types)}"""

    for attempt in range(2):
        try:
            raw = client.chat(ANALYSIS_PROMPT, prompt, images=images)
            match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
            json_str = match.group(1) if match else raw
            start = json_str.find("{")
            end = json_str.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = json_str[start:end]
            data = json.loads(json_str)
            modules = data.get("modules", [])
            if modules and isinstance(modules, list):
                return modules
        except Exception:
            continue
    return []


def _generate_for_module(client: LLMClient, requirement: str,
                         module: dict, default_priority: str) -> list[dict]:
    """Step 2: 为单个模块生成测试用例"""
    prompt = MODULE_PROMPT.format(
        module_name=module["name"],
        module_desc=module.get("description", ""),
        dimensions="、".join(module.get("dimensions", ["功能测试", "边界测试", "异常测试"])),
        requirement=requirement,
    )

    for attempt in range(2):
        try:
            raw = client.chat("你是一名资深软件测试工程师。", prompt, max_tokens=8192)
            return _parse_response(raw)
        except (ValueError, json.JSONDecodeError):
            continue
    return []


def _generate_all_in_one(client: LLMClient, requirement: str,
                         default_priority: str, case_types: list[str],
                         images: list[dict] | None = None) -> list[dict]:
    """一次性生成（回退方案）"""
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

    for attempt in range(3):
        try:
            raw = client.chat(SYSTEM_PROMPT, user_prompt, images=images)
            return _parse_response(raw)
        except (ValueError, json.JSONDecodeError):
            continue

    raise ValueError("JSON 解析失败")


def _deduplicate(testcases: list[dict]) -> list[dict]:
    """去除重复用例（模块 + 标题 + 步骤相同视为重复）"""
    seen = set()
    unique = []
    for tc in testcases:
        key = (tc.get("module", ""), tc.get("title", ""), tc.get("steps", "")[:50])
        if key not in seen:
            seen.add(key)
            unique.append(tc)
    return unique


REQUIRED_FIELDS = {
    "id": "",
    "module": "未分类",
    "title": "未命名用例",
    "precondition": "",
    "steps": "",
    "expected": "",
    "priority": "P1",
    "type": "功能测试",
}


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
            testcases = _normalize_result(result)
            return _validate_testcases(testcases)

    raise ValueError(f"无法解析 LLM 返回的 JSON")


def _validate_testcases(testcases: list[dict]) -> list[dict]:
    """校验并补全测试用例字段"""
    if not testcases:
        raise ValueError("LLM 返回了空的用例列表")

    validated = []
    for i, tc in enumerate(testcases):
        if not isinstance(tc, dict):
            continue
        # 补全缺失字段
        for field, default in REQUIRED_FIELDS.items():
            if field not in tc or not tc[field]:
                tc[field] = default
        # 自动修正 id 格式
        if not tc["id"].startswith("TC_"):
            tc["id"] = f"TC_{i + 1:03d}"
        # 校验优先级
        if tc["priority"] not in ("P0", "P1", "P2", "P3"):
            tc["priority"] = "P1"
        validated.append(tc)

    if not validated:
        raise ValueError("没有有效的测试用例")

    return validated


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
    raise ValueError(f"无法识别的数据格式: {type(data)}")


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
