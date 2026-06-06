"""偏好学习模块 - 从用户编辑中提取偏好规则"""

import json
import re

from llm_client import LLMClient

# 比较字段列表
_DIFF_FIELDS = ["title", "precondition", "steps", "expected", "priority", "type"]


def compute_diffs(original: list[dict], edited: list[dict]) -> list[dict]:
    """逐字段对比 original 和 edited，返回有差异的用例列表"""
    # 建立 edited 的 id 索引
    edited_map = {tc.get("id", ""): tc for tc in edited}

    diffs = []
    for orig_tc in original:
        tc_id = orig_tc.get("id", "")
        edit_tc = edited_map.get(tc_id)
        if not edit_tc:
            continue

        field_diffs = {}
        for field in _DIFF_FIELDS:
            old_val = str(orig_tc.get(field, "")).strip()
            new_val = str(edit_tc.get(field, "")).strip()
            if old_val != new_val:
                field_diffs[field] = {"before": old_val, "after": new_val}

        if field_diffs:
            diffs.append({
                "id": tc_id,
                "title": orig_tc.get("title", ""),
                "field_diffs": field_diffs,
            })

    return diffs


EXTRACT_SYSTEM_PROMPT = """你是一名测试用例偏好分析专家。用户对 AI 生成的测试用例进行了修改。
请分析这些修改，提取用户的偏好规则。

## 分析要求
1. 从修改中归纳出通用的、可复用的偏好规则，而非针对某条用例的特殊修改
2. 规则应具体、可操作（如"步骤中应使用具体页面元素名称"而非"步骤要详细"）
3. 如果多条用例的修改指向同一规则，合并为一条
4. 如果修改无法归纳出通用规则（如仅修改了某个具体数值），则跳过

## 分类（category）
- step_style：测试步骤的写法风格
- priority：优先级标注偏好
- coverage：测试覆盖范围偏好
- format：格式和结构偏好
- terminology：术语和措辞偏好
- other：其他

## 输出格式（严格 JSON）：
```json
{
  "preferences": [
    {"category": "step_style", "pattern": "测试步骤中应使用具体的页面元素名称（如'登录按钮'而非'按钮'）"}
  ]
}
```

如果修改无法提取通用规则，返回空列表：{"preferences": []}"""

EXTRACT_USER_TEMPLATE = """以下是用户对 AI 生成用例的修改记录（共 {count} 处）：

{diffs_text}

请提取用户偏好规则。"""


def extract_preferences(diffs: list[dict], client: LLMClient) -> list[dict]:
    """用 LLM 从 diff 中提取偏好规则"""
    if not diffs:
        return []

    # 格式化 diff 为可读文本
    lines = []
    for d in diffs:
        lines.append(f"【{d['id']}】{d['title']}")
        for field, change in d["field_diffs"].items():
            lines.append(f"  {field}:")
            lines.append(f"    修改前: {change['before']}")
            lines.append(f"    修改后: {change['after']}")
        lines.append("")

    diffs_text = "\n".join(lines)
    user_prompt = EXTRACT_USER_TEMPLATE.format(count=len(diffs), diffs_text=diffs_text)

    raw = client.chat(EXTRACT_SYSTEM_PROMPT, user_prompt)

    # 解析 JSON
    try:
        match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
        json_str = match.group(1) if match else raw
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start >= 0 and end > start:
            json_str = json_str[start:end]
        data = json.loads(json_str)
        prefs = data.get("preferences", [])
        # 校验
        valid = []
        for p in prefs:
            if isinstance(p, dict) and p.get("category") and p.get("pattern"):
                valid.append({"category": p["category"], "pattern": p["pattern"]})
        return valid
    except Exception:
        return []
