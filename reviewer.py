"""测试用例评审模块 - 使用 LLM 对生成的用例进行评审和优化"""

from llm_client import LLMClient

REVIEW_SYSTEM_PROMPT = """你是一名资深测试架构师，负责评审测试用例的质量。

请从以下维度评审测试用例：
1. **覆盖率**：是否覆盖了正常流程、边界条件、异常场景
2. **准确性**：测试步骤是否正确，预期结果是否合理
3. **可执行性**：步骤是否具体可执行，是否缺少关键信息
4. **一致性**：用例编号、命名、格式是否规范
5. **遗漏风险**：是否有明显遗漏的测试场景

输出格式：
## 评审结论
- 总体评分：X/10
- 用例数量：N 条
- 风险等级：高/中/低

## 发现的问题
（按严重程度列出）

## 改进建议
（具体可操作的建议）

## 补充用例建议
（如有遗漏场景，列出需要补充的用例）"""

REVIEW_USER_PROMPT_TEMPLATE = """请评审以下测试用例：

---需求文档---
{requirement}
---需求文档结束---

---测试用例---
{testcases_text}
---测试用例结束---"""


def review_testcases(client: LLMClient, requirement: str,
                     testcases: list[dict]) -> str:
    """对生成的测试用例进行评审"""
    # 将用例格式化为可读文本
    lines = []
    for tc in testcases:
        lines.append(f"[{tc.get('id', '')}] {tc.get('title', '')} "
                     f"(优先级:{tc.get('priority', '')} 类型:{tc.get('type', '')})")
        lines.append(f"  前置条件: {tc.get('precondition', '无')}")
        lines.append(f"  步骤: {tc.get('steps', '')}")
        lines.append(f"  预期: {tc.get('expected', '')}")
        lines.append("")
    testcases_text = "\n".join(lines)

    user_prompt = REVIEW_USER_PROMPT_TEMPLATE.format(
        requirement=requirement,
        testcases_text=testcases_text,
    )

    return client.chat(REVIEW_SYSTEM_PROMPT, user_prompt)


OPTIMIZE_SYSTEM_PROMPT = """你是一名资深测试架构师，负责根据评审报告优化测试用例。

## 核心要求
- **必须保留所有原始用例**，不得删除任何一条
- 对有问题的用例进行修改优化
- 根据评审报告补充遗漏的用例
- 最终输出的用例数量必须 >= 原始用例数量

## 优化规则
1. 修复评审报告中指出的问题（步骤不清晰、预期不合理等）
2. 补充评审报告建议的遗漏场景（作为新增用例）
3. 未被评审报告指出问题的用例保持原样输出
4. 用例编号从 TC_001 开始重新排列

请严格按以下 JSON 格式输出，不要输出其他内容：
```json
{
  "testcases": [
    {
      "id": "TC_001",
      "module": "模块名称",
      "title": "用例标题",
      "precondition": "前置条件",
      "steps": "1. 操作步骤\\n2. 操作步骤",
      "expected": "预期结果",
      "priority": "P1",
      "type": "功能测试"
    }
  ]
}
```"""

OPTIMIZE_USER_PROMPT_TEMPLATE = """请根据评审报告优化以下测试用例。

**重要：你必须输出完整的优化后用例列表，包含所有原始用例（有问题的已修改，没问题的保持原样）加上新补充的用例。不要只输出修改过的用例！**

原始用例共 {total} 条，最终输出应 >= {total} 条。

---需求文档---
{requirement}
---需求文档结束---

---评审报告---
{review_report}
---评审报告结束---

---原始测试用例（共 {total} 条）---
{testcases_text}
---原始测试用例结束---

请输出完整的优化后测试用例列表（JSON格式）。"""


def optimize_testcases(client: LLMClient, requirement: str,
                       testcases: list[dict], review_report: str) -> list[dict]:
    """根据评审报告优化测试用例，保留全部原始用例"""
    from generator import _parse_response

    total = len(testcases)

    lines = []
    for tc in testcases:
        lines.append(f"[{tc.get('id', '')}] {tc.get('title', '')} "
                     f"(优先级:{tc.get('priority', '')} 类型:{tc.get('type', '')})")
        lines.append(f"  前置条件: {tc.get('precondition', '无')}")
        lines.append(f"  步骤: {tc.get('steps', '')}")
        lines.append(f"  预期: {tc.get('expected', '')}")
        lines.append("")
    testcases_text = "\n".join(lines)

    user_prompt = OPTIMIZE_USER_PROMPT_TEMPLATE.format(
        requirement=requirement,
        review_report=review_report,
        testcases_text=testcases_text,
        total=total,
    )

    # 优化需要输出完整用例列表，token 需求远大于普通调用
    # 每条用例约 150 tokens，预留足够空间
    optimize_max_tokens = max(16384, total * 200)
    raw = client.chat(OPTIMIZE_SYSTEM_PROMPT, user_prompt, max_tokens=optimize_max_tokens)
    result = _parse_response(raw)

    # 如果优化后数量明显减少，说明 LLM 漏掉了用例，回退返回原始用例
    if len(result) < total * 0.8:
        print(f"[WARN] 优化后用例数量({len(result)})明显少于原始数量({total})，回退使用原始用例")
        return testcases

    return result
