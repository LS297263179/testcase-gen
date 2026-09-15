---
name: review-test-cases
description: "评审已生成的测试用例质量（6 维评审：覆盖率/准确性/可执行性/一致性/遗漏风险/重复检测）。适合在生成测试用例后需要质量检查时使用"
argument-hint: "[粘贴用例 JSON]"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
user-invocable: true
---

# /review-test-cases

对已生成的测试用例进行 6 维质量评审。

## 用法
```
/review-test-cases [粘贴用例 JSON]
```

## 评审维度
1. **覆盖率** — 正常流程、边界条件、异常场景是否覆盖
2. **准确性** — 步骤和预期结果是否合理
3. **可执行性** — 步骤是否具体可操作
4. **一致性** — 编号、命名、格式是否规范
5. **遗漏风险** — 是否有明显遗漏的场景
6. **重复检测** — 是否有重复或高度相似的用例

## 流程
1. 获取用户提供的测试用例
2. 参考 `core/reviewer.py` 的 `REVIEW_SYSTEM_PROMPT` 维度
3. 逐条评审并给出改进建议
4. 输出评审报告
