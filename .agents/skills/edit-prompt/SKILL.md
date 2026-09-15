---
name: edit-prompt
description: "查找并编辑项目中任意 AI 提示词（对应文件 + 变量名）。适合用户想修改测试点/测试用例/评审/偏好的生成质量时使用"
argument-hint: "[提示词关键词]"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
user-invocable: true
---

# /edit-prompt

帮助定位并编辑项目中的任意 AI 提示词。

## 用法
```
/edit-prompt 测试点    → 定位测试点提示词
/edit-prompt 评审      → 定位评审提示词
/edit-prompt 用例生成  → 定位用例生成提示词
/edit-prompt 全部列出  → 列出所有提示词
```

## 提示词对照表

| 关键词 | 文件 | 变量名 |
|--------|------|--------|
| 测试点 | `web/data.py` | `TEST_POINTS_PROMPT` |
| 需求分析 | `core/generator.py` | `ANALYSIS_PROMPT` |
| 分模块生成 | `core/generator.py` | `MODULE_PROMPT` |
| 一次性生成 | `core/generator.py` | `SYSTEM_PROMPT` |
| 用例评审 | `core/reviewer.py` | `REVIEW_SYSTEM_PROMPT` |
| 偏好提取 | `core/preferences.py` | `EXTRACT_SYSTEM_PROMPT` |
| 数量控制 | `core/generator.py` | `COMPLEXITY_CASE_COUNT` |

## 修改后
- 必须重启应用才会生效
- 运行 `/unit-test` 验证无破坏
