"""测试用例评审模块 - 使用 LLM 对生成的用例进行评审和优化"""

from llm_client import LLMClient

REVIEW_SYSTEM_PROMPT = """你是一名资深测试架构师，负责评审测试用例的质量。

请从以下维度评审测试用例：
1. **覆盖率**：是否覆盖了正常流程、边界条件、异常场景
2. **准确性**：测试步骤是否正确，预期结果是否合理
3. **可执行性**：步骤是否具体可执行，是否缺少关键信息
4. **一致性**：用例编号、命名、格式是否规范
5. **遗漏风险**：是否有明显遗漏的测试场景
6. **重复检测**：仔细检查是否有重复或高度相似的用例（测试目标、操作步骤、预期结果实质相同，仅措辞不同的视为重复）

## 输出格式
请严格按以下格式输出：

## 评审结论
- 总体评分：X/10
- 用例数量：N 条
- 风险等级：高/中/低
- 重复用例数：M 条

## 重复用例清单
（逐行列出每组重复用例，格式如下。如果没有重复则写"无"）
- 重复组1：[TC_XXX 标题A] 与 [TC_YYY 标题B] 重复，原因：xxx，建议保留 TC_XXX（更完善）
- 重复组2：...

## 发现的问题
（按严重程度列出，不包括已列在重复清单中的）

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

## 核心原则
- 保证用例质量，覆盖充分，不遗漏关键场景
- **严格删除评审报告中标记的重复用例**，只保留每组中推荐保留的那条
- 对有问题的用例进行修改优化
- 根据评审报告补充遗漏的用例

## 优化规则
1. **删除重复**：评审报告的「重复用例清单」中明确标记了哪些用例重复，请严格按照建议删除应删除的用例，只保留推荐的那条
2. **修复问题**：修复评审报告「发现的问题」中指出的问题（步骤不清晰、预期不合理等）
3. **补充遗漏**：根据评审报告「补充用例建议」新增用例
4. **保留优质用例**：未被标记为重复、且未被指出问题的用例保持原样输出
5. 用例编号从 TC_001 开始重新排列

## 输出格式（严格 JSON）：
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

## 优化步骤
1. 先查看评审报告中的「重复用例清单」，删除所有标记为重复的用例（保留推荐的那条）
2. 再修复「发现的问题」中指出的用例质量问题
3. 最后根据「补充用例建议」补充遗漏的用例

原始用例共 {total} 条，去除重复后数量减少是正常的。

---需求文档---
{requirement}
---需求文档结束---

---评审报告---
{review_report}
---评审报告结束---

---原始测试用例（共 {total} 条）---
{testcases_text}
---原始测试用例结束---

请输出优化后的完整测试用例列表（JSON格式），确保：
- 评审报告中标记的重复用例已全部删除
- 有问题的用例已修复
- 遗漏的场景已补充"""


def _format_tc_text(tc: dict) -> str:
    """格式化单条用例为可读文本"""
    lines = [f"[{tc.get('id', '')}] {tc.get('title', '')} "
             f"(优先级:{tc.get('priority', '')} 类型:{tc.get('type', '')})"]
    lines.append(f"  前置条件: {tc.get('precondition', '无')}")
    lines.append(f"  步骤: {tc.get('steps', '')}")
    lines.append(f"  预期: {tc.get('expected', '')}")
    return "\n".join(lines)


def optimize_testcases(client: LLMClient, requirement: str,
                       testcases: list[dict], review_report: str) -> list[dict]:
    """根据评审报告优化测试用例：删除标记的重复用例 + 修复问题 + 补充遗漏"""
    from generator import _parse_response, _deduplicate

    total = len(testcases)

    testcases_text = "\n\n".join(_format_tc_text(tc) for tc in testcases)

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

    # 兜底去重：对 LLM 未完全处理的重复做一轮程序化去重
    before_dedup = len(result)
    result = _deduplicate(result)
    if before_dedup != len(result):
        print(f"[INFO] 兜底去重: {before_dedup} -> {len(result)}")

    if not result:
        print(f"[WARN] 优化后用例为空，回退使用原始用例")
        return testcases

    removed = total - len(result)
    if removed > 0:
        print(f"[INFO] 优化完成: 原始 {total} 条 -> 优化后 {len(result)} 条（净减少 {removed} 条）")
    elif removed < 0:
        print(f"[INFO] 优化完成: 原始 {total} 条 -> 优化后 {len(result)} 条（补充了 {-removed} 条）")
    else:
        print(f"[INFO] 优化完成: 数量不变 {total} 条（已修复质量问题）")

    return result
