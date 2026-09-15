---
name: generate-test-points
description: "从需求描述生成结构化测试点（调用 TEST_POINTS_PROMPT 流程）。适合用户粘贴需求文档后需要快速输出测试点场景时使用"
argument-hint: "<需求描述>"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
user-invocable: true
---

# /generate-test-points

根据用户输入的需求描述，生成结构化的测试点。

## 用法
```
/generate-test-points <需求描述或粘贴需求文档内容>
```

## 流程
1. 引导用户提供需求描述（如果未提供）
2. 读取 `web/data.py` 中的 `TEST_POINTS_PROMPT` 确认当前提示词
3. 根据需求分析模块和测试维度
4. 生成测试点 JSON（module → subcategories → points）
5. 输出格式化的测试点报告

## 输出格式
```markdown
## 📋 测试点报告

### 模块名称
#### 子分类名称
- **测试点标题**：具体描述
```

## 注意事项
- 测试点数量控制在 60-120 条
- 注意向后兼容（`_normalize_points_format`）
- 每个模块按子分类组织
