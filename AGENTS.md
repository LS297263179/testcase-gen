# testcase-gen — AI 测试用例生成器

## 一句话
Flask + SQLite + LLM 的测试用例自动化生成工具。

## 技术栈
Python >= 3.11 / Flask / SQLite / ruff (line-length 120, double-quotes) / pytest

## 快速命令
```
启动:        python start.py [-p 5000] [--debug]
测试:        pytest [-v] [-k keyword] [tests/test_file.py]
Lint:        ruff check . && ruff format --check .
依赖安装:    pip install -e ".[dev]"
```

## 项目结构（核心文件）
```
core/generator.py    → 用例生成 + 所有 Prompt
                       ├── ANALYSIS_PROMPT        # 需求分析拆解
                       ├── ANALYSIS_PROMPT_WITH_IMAGE
                       ├── MODULE_PROMPT           # 按模块生成
                       └── SYSTEM_PROMPT           # 一次性生成
core/db.py           → SQLite 操作
core/llm_client.py   → LLM 调用封装
core/reviewer.py     → 评审 + 优化（REVIEW_SYSTEM_PROMPT）
core/preferences.py  → 偏好学习（EXTRACT_SYSTEM_PROMPT）
core/output.py       → Excel/MD 导出 + 去重
web/data.py          → 测试点 Prompt（TEST_POINTS_PROMPT）+ API
web/generate.py      → 用例生成/评审/优化 API
static/app.js        → 前端全部逻辑
config.yaml          → 配置（不追踪 git）
```

## 开发规则

### 修改 Prompt 守则
1. 定位到对应文件的对应变量名
2. 修改后**必须重启应用**生效
3. **必须向后兼容**旧数据格式 — 旧格式无 `subcategories`，已在 `_normalize_points_format()` 处理
4. 修改后运行 `pytest` 验证

### Git 约定
- 不要直接推 main 分支，除非用户明确要求
- commit 信息带中文描述
- 推送两个 remote：`origin`（github）和 `gitee`
- 推送到 gitee 需绕过代理：`git -c http.proxy="" -c https.proxy="" push gitee main`

### 代码规范
- ruff 自动检查 + 格式化
- `known-first-party = ["core", "web"]`（isort 不会把内部模块当三方库）
- 变量/函数用 snake_case，类用 PascalCase

## 数据模型
```
测试点（三级结构）:
  module → subcategories[] → {name, points[] → {title, description}}

测试用例:
  session → testcases[] → {id, module, title, precondition, steps, expected, priority, type}
```

### 兼容性
- 数据库中的旧测试点（无 subcategories）在读取时自动通过 `_normalize_points_format()` 转换为新格式
- 前端 `renderTestPoints()` 同时支持新旧两种格式

## Prompt 速查

| 你要改什么 | 文件 | 变量名 |
|-----------|------|--------|
| 测试点生成质量 | `web/data.py` | `TEST_POINTS_PROMPT` |
| 用例-需求分析逻辑 | `core/generator.py` | `ANALYSIS_PROMPT` |
| 用例-分模块生成内容 | `core/generator.py` | `MODULE_PROMPT` |
| 用例-快速生成内容 | `core/generator.py` | `SYSTEM_PROMPT` |
| 用例评审维度 | `core/reviewer.py` | `REVIEW_SYSTEM_PROMPT` |
| 偏好规则提取 | `core/preferences.py` | `EXTRACT_SYSTEM_PROMPT` |
| 用例数量控制 | `core/generator.py` | `COMPLEXITY_CASE_COUNT` |

## 常见开发场景

### 场景 1：想让 AI 生成的测试点更详细
→ 编辑 `web/data.py` 的 `TEST_POINTS_PROMPT`，加强 `description` 字段的要求描述

### 场景 2：想让测试用例覆盖更多安全测试
→ 编辑 `core/generator.py` 的 `MODULE_PROMPT` 或 `SYSTEM_PROMPT`，在覆盖维度/方法论中强化安全相关描述

### 场景 3：调整生成速度 vs 质量
→ 调大 `config.yaml` 的 `max_tokens` 提高质量（但更慢）
→ 调小或调大 `COMPLEXITY_CASE_COUNT` 控制用例数量
→ 开启 `enable_thinking: true`（深度推理，更慢但更准）

### 场景 4：换模型
→ 改 `config.yaml` 的 `generate.api_type`、`base_url`、`api_key`、`model`
