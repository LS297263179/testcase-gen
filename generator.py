"""测试用例生成模块 - Prompt 模板 + 用例解析，支持分段并行生成"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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

维度包括：功能测试、边界测试、异常测试、兼容性测试、性能测试。根据模块特点选择合适的维度。

如果提供了图片，请先仔细分析图片中的界面元素（表单、按钮、导航、列表、弹窗等），再基于分析结果拆解模块。"""

ANALYSIS_PROMPT_WITH_IMAGE = """你是一名资深软件测试架构师。请结合需求描述和图片，拆解出独立的功能模块和每个模块需要覆盖的测试维度。

## 图片分析要求
请仔细分析图片中的以下内容，将其作为拆解模块的重要依据：
1. **页面布局**：有哪些独立页面或页面区域
2. **表单元素**：输入框、下拉框、单选/多选、日期选择等，及其校验规则
3. **交互控件**：按钮（提交、取消、删除等）、链接、弹窗、抽屉
4. **数据展示**：表格/列表、详情页、分页、搜索筛选
5. **状态变化**：空状态、加载中、错误提示、成功反馈

输出格式（严格 JSON）：
```json
{
  "modules": [
    {
      "name": "模块名称",
      "description": "模块简述（结合图片中的具体界面元素）",
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
6. 每个维度 3-5 条用例，注重质量而非数量
7. **严格避免重复**：不同用例的标题和测试场景必须有实质性差异，不要用不同的措辞描述同一个测试场景

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

MODULE_PROMPT_IMAGE_SUFFIX = """
## 图片参考
如果提供了图片，请结合图片中的实际界面来编写测试用例：
- 测试步骤中引用图片中的具体元素名称（按钮文字、输入框标签等）
- 预期结果基于图片中的实际界面布局和交互逻辑
- 发现图片中需求文档未提及的界面元素时，也应为其编写用例"""

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
8. **严格避免重复用例**：每条用例的标题和测试场景必须有实质性差异，不要用不同措辞描述同一场景

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
                       on_progress: Callable[[str], None] | None = None,
                       max_testcases: int = 100) -> list[dict]:
    """分段生成测试用例：先分析模块，再按模块逐一生成，最后合并去重。
    on_progress: 进度回调，用于通知前端当前步骤
    max_testcases: 单次生成最大用例数，默认 100
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
        return _generate_all_in_one(active_client, requirement, default_priority, case_types, images, max_testcases)

    # Step 2: 按模块并行生成
    all_testcases = []
    total_modules = len(modules)
    max_workers = min(total_modules, 5)  # 最多 5 路并发，避免 API 过载

    if on_progress:
        on_progress(f"正在并行生成 {total_modules} 个模块的测试用例（{max_workers} 路并发）...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_module = {
            executor.submit(_generate_for_module, active_client, requirement, mod, default_priority, images): mod
            for mod in modules
        }
        completed = 0
        for future in as_completed(future_to_module):
            mod = future_to_module[future]
            completed += 1
            try:
                cases = future.result()
                all_testcases.extend(cases)
                if on_progress:
                    on_progress(f"「{mod['name']}」模块完成，生成 {len(cases)} 条用例 ({completed}/{total_modules})")
            except Exception as e:
                if on_progress:
                    on_progress(f"「{mod['name']}」模块生成失败: {e} ({completed}/{total_modules})")

    if not all_testcases:
        raise ValueError("分段生成未产出任何用例")

    # Step 3: 去重、限制数量、统一编号
    raw_count = len(all_testcases)
    all_testcases = _deduplicate(all_testcases)
    dedup_count = raw_count - len(all_testcases)
    if on_progress and dedup_count > 0:
        on_progress(f"去重完成，移除 {dedup_count} 条重复用例")

    if len(all_testcases) > max_testcases:
        if on_progress:
            on_progress(f"用例数 ({len(all_testcases)}) 超过上限 {max_testcases}，按优先级保留")
        all_testcases = _limit_testcases(all_testcases, max_testcases)

    for i, tc in enumerate(all_testcases):
        tc["id"] = f"TC_{i + 1:03d}"

    return all_testcases


def _analyze_modules(client: LLMClient, requirement: str,
                     case_types: list[str], images: list[dict] | None = None) -> list[dict]:
    """Step 1: 分析需求，拆解功能模块和测试维度"""
    # 有图片时用图片版 prompt，引导 LLM 关注界面元素
    sys_prompt = ANALYSIS_PROMPT_WITH_IMAGE if images else ANALYSIS_PROMPT

    prompt = f"""请分析以下需求，拆解出独立的功能模块：

---需求开始---
{requirement}
---需求结束---

需要覆盖的测试维度：{"、".join(case_types)}"""

    for attempt in range(2):
        try:
            raw = client.chat(sys_prompt, prompt, images=images)
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
                         module: dict, default_priority: str,
                         images: list[dict] | None = None) -> list[dict]:
    """Step 2: 为单个模块生成测试用例"""
    prompt = MODULE_PROMPT.format(
        module_name=module["name"],
        module_desc=module.get("description", ""),
        dimensions="、".join(module.get("dimensions", ["功能测试", "边界测试", "异常测试"])),
        requirement=requirement,
    )
    # 有图片时追加图片分析指引
    if images:
        prompt += MODULE_PROMPT_IMAGE_SUFFIX

    for attempt in range(2):
        try:
            raw = client.chat("你是一名资深软件测试工程师。", prompt, images=images, max_tokens=8192)
            return _parse_response(raw)
        except (ValueError, json.JSONDecodeError):
            continue
    return []


def _generate_all_in_one(client: LLMClient, requirement: str,
                         default_priority: str, case_types: list[str],
                         images: list[dict] | None = None,
                         max_testcases: int = 100) -> list[dict]:
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

    user_prompt += f"\n- 用例总数不超过 {max_testcases} 条，避免生成重复或高度相似的用例"

    for attempt in range(3):
        try:
            raw = client.chat(SYSTEM_PROMPT, user_prompt, images=images)
            return _parse_response(raw)
        except (ValueError, json.JSONDecodeError):
            continue

    raise ValueError("JSON 解析失败")


def _normalize_text(text: str) -> str:
    """标准化文本用于去重比较：去除空白、标点差异"""
    if not text:
        return ""
    # 统一空白字符，去除首尾空格
    text = re.sub(r'\s+', ' ', text).strip()
    # 去除常见标点差异
    text = text.replace("，", ",").replace("。", ".").replace("：", ":")
    text = text.replace("（", "(").replace("）", ")").replace("、", ",")
    return text


def _deduplicate(testcases: list[dict]) -> list[dict]:
    """去除重复用例（模块 + 标题 + 预期结果 相同视为重复）"""
    seen = set()
    unique = []
    for tc in testcases:
        module = _normalize_text(tc.get("module", ""))
        title = _normalize_text(tc.get("title", ""))
        expected = _normalize_text(tc.get("expected", ""))[:80]
        # 用 模块+标题+预期结果 作为去重键，比 steps 更能识别语义重复
        key = (module, title, expected)
        if key not in seen:
            seen.add(key)
            unique.append(tc)
    return unique


PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _limit_testcases(testcases: list[dict], max_count: int) -> list[dict]:
    """限制用例数量，按优先级保留高优先级用例"""
    if len(testcases) <= max_count:
        return testcases
    # 按优先级排序（P0 优先），同优先级保持原顺序
    sorted_cases = sorted(testcases, key=lambda tc: PRIORITY_ORDER.get(tc.get("priority", "P1"), 1))
    return sorted_cases[:max_count]


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


def _strip_trailing_punctuation(text: str) -> str:
    """去除文本结尾的标点符号"""
    if not text:
        return text
    text = text.rstrip()
    while text and text[-1] in ("。", ".", "，", ",", "；", ";", "：", ":"):
        text = text[:-1].rstrip()
    return text


def _normalize_steps(steps: str) -> str:
    """统一测试步骤格式：去除每步结尾的标点"""
    if not steps:
        return steps
    lines = steps.split("\n")
    normalized = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^(\d+[.、]\s*)', line)
        if m:
            prefix = m.group(1)
            content = line[len(prefix):].strip()
            while content and content[-1] in ("。", ".", "，", ",", "；", ";", "：", ":"):
                content = content[:-1].rstrip()
            normalized.append(prefix + content)
        else:
            normalized.append(line)
    return "\n".join(normalized)


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
        # 统一标点符号
        tc["expected"] = _strip_trailing_punctuation(tc.get("expected", ""))
        tc["precondition"] = _strip_trailing_punctuation(tc.get("precondition", ""))
        tc["steps"] = _normalize_steps(tc.get("steps", ""))
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
