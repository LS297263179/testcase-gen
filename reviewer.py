"""测试用例评审模块 - 使用 LLM 对生成的用例进行评审"""

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
