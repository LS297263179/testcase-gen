# AI 测试用例生成器

基于大模型（LLM）的测试用例自动生成工具。输入需求文档或 UI 截图，自动批量生成高质量测试用例，支持 Excel 和 Markdown 双格式导出，提供 Web 可视化界面。

## 功能特性

### 核心能力

- **多模态输入**：支持 Markdown / TXT 文档、Excel 表格、UI 截图 / 设计稿、手动输入，文字与图片可混合使用
- **智能分段生成**：自动拆解需求为功能模块，按模块并行生成（最多 5 路并发），覆盖更全面、速度更快
- **图片深度识别**：图片贯穿整个生成链路（模块分析 + 每个模块生成），AI 会结合界面元素编写用例
- **数量可控**：单次生成上限可配置（默认 100 条），超出时按优先级智能裁剪
- **思考模式**：支持 LLM 深度推理（enable_thinking），复杂需求下用例质量更高
- **双模型调度**：文本需求用强模型，图片输入自动切换多模态模型

### 去重与质量

- **多层去重**：Prompt 层引导 LLM 避免重复 → 程序化兜底去重（标准化文本 + 模块/标题/预期结果三键匹配）
- **AI 评审**：6 维质量评审（覆盖率、准确性、可执行性、一致性、遗漏风险、重复检测），输出带重复用例清单的评审报告
- **精准优化**：根据评审报告的重复清单精准删除重复用例，修复质量问题，补充遗漏场景
- **变更对比**：优化后基于内容相似度匹配展示新增 / 修改 / 删除的 diff，不因 ID 重编导致误判

### 历史记录与偏好学习

- **SQLite 持久化**：每次生成自动保存到本地数据库（`data.db`），支持历史记录浏览、详情查看、删除
- **偏好学习**：用户在 Web 界面编辑用例后，AI 自动提取修改差异并归纳为偏好规则（如步骤风格、术语偏好等）
- **偏好注入**：后续生成时自动将活跃偏好规则注入 Prompt，用例风格越用越贴合用户习惯
- **偏好管理**：支持启用 / 禁用 / 修改 / 删除偏好规则，低权重规则自动停用

### 导出与交互

- **双格式导出**：同时输出 Excel（可导入禅道 / Jira）和 Markdown（可 Git 管理）
- **SSE 实时进度**：生成、评审、优化全程显示当前步骤，每个模块完成时实时通知
- **粘贴图片**：在文本框直接 Ctrl+V 粘贴截图，自动添加到上传区
- **Web 界面**：支持拖拽上传、图片预览、在线查看用例详情、一键下载

### 兼容性

- **双 SDK 支持**：同时兼容 Anthropic 和 OpenAI 格式的 API
- **国产模型支持**：DeepSeek、通义千问、智谱、Moonshot、小米 MiMo 等

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API

编辑 `config.yaml`，填入你的 API 信息：

```yaml
generate:
  api_type: "openai"
  base_url: "https://your-api-endpoint/v1"
  api_key: "your-api-key"
  model: "your-model"            # 文本生成模型
  image_model: "your-vlm"        # 图片输入模型（可选）

review:
  enabled: false                 # 设为 true 启用独立评审模型
```

### 3. 启动

**Web 版（推荐）：**

```bash
python start.py
```

浏览器自动打开 `http://localhost:5000`，可视化操作。

**命令行版：**

```bash
python main.py your_requirement.md
```

## 使用说明

### Web 版

```bash
python start.py              # 默认 5000 端口，自动打开浏览器
python start.py -p 8080      # 指定端口
python start.py --no-browser # 不自动打开浏览器
```

**界面功能：**

| 区域 | 功能 |
|------|------|
| 文字描述 | 输入需求文本（可选，与图片配合使用效果更佳） |
| 文件上传 | 支持需求文档 + UI 截图混合上传，支持粘贴图片、拖拽上传 |
| 参数配置 | 设置默认优先级（P0-P3）、用例类型（逗号分隔） |
| 结果展示 | 用例统计、表格展示、点击"详情"展开完整步骤 |
| 导出下载 | 一键下载 Excel 或 Markdown 文件 |
| AI 评审 | 点击"AI 评审"，实时显示评审进度，输出含重复检测的评审报告 |
| 优化用例 | 评审后点击"根据评审优化用例"，精准去重 + 修复问题，并展示变更 diff |
| 历史记录 | 查看所有生成记录，支持详情回溯、重新评审、删除 |
| 偏好学习 | 编辑用例后点击"提取偏好"，AI 自动归纳修改规则，后续生成自动应用 |

### 命令行版

```
python main.py [需求文档路径] [选项]
```

| 参数 | 说明 |
|------|------|
| `source` | 需求文档路径（.md / .txt / .xlsx），不指定则手动输入 |
| `-c, --config` | 配置文件路径，默认 `config.yaml` |
| `-o, --output` | 输出目录，默认 `./output` |
| `-f, --format` | 输出格式：`excel` / `markdown` / `all` |
| `-r, --review` | 启用 AI 评审用例 |

```bash
python main.py examples/sample_requirement.md
python main.py requirement.md -f excel
python main.py requirement.md -o ./my_output
python main.py requirement.md -r
```

## 生成流程

```
需求输入（文本 + 图片）
    │
    ▼
Step 1: 需求分析（有图片时结合界面元素）──→ 拆解功能模块 + 测试维度
    │           ↑
    │       注入用户偏好规则
    ▼
Step 2: 并行生成（最多 5 路并发）──→ 每个模块独立调用 LLM，图片全程传递
    │
    ▼
Step 3: 去重 + 限数 ──→ 程序化去重 + 超出上限按优先级裁剪 + 统一编号
    │
    ▼
导出（Excel + Markdown）+ 自动保存历史记录
    │
    ▼
AI 评审 ──→ 6 维评审报告（含重复用例清单）
    │
    ▼
精准优化 ──→ 按清单删除重复 + 修复问题 + 补充遗漏
    │
    ▼
变更对比 ──→ 基于内容相似度展示新增 / 修改 / 删除
    │
    ▼
用户编辑 ──→ AI 提取偏好规则 ──→ 存入数据库（下次生成自动应用）
```

## 输出示例

### Markdown

```markdown
## 用户登录

| 编号   | 标题                           | 优先级 | 类型     |
|--------|--------------------------------|--------|----------|
| TC_001 | 正常流程：登录成功             | P0     | 功能测试 |
| TC_002 | 边界测试：手机号位数边界       | P1     | 边界测试 |
| TC_003 | 异常测试：手机号被封禁         | P1     | 异常测试 |
```

### Excel

- 表头带筛选器，可按模块 / 优先级 / 类型快速筛选
- 优先级按颜色区分（P0 红、P1 橙、P2 黄、P3 绿）
- 可直接导入禅道、Jira 等测试管理平台

## 项目结构

```
testcase-gen/
├── start.py             # Web 版启动器
├── web.py               # Flask Web 应用（SSE 流式 API + 并行生成）
├── templates/
│   └── index.html       # Web 前端页面
├── main.py              # 命令行入口
├── config.yaml          # 配置文件（模型、输出、用例参数）
├── config.yaml.example  # 配置文件模板
├── .gitignore
├── requirements.txt     # Python 依赖
├── llm_client.py        # LLM 调用封装（双 SDK + 多模态 + 思考模式）
├── reader.py            # 需求文档读取（MD / TXT / Excel / 图片）
├── generator.py         # 测试用例生成（并行分段 + Prompt + 去重 + JSON 容错解析）
├── reviewer.py          # 测试用例评审（6 维评审 + 重复检测）+ 精准优化
├── output.py            # 输出模块（Excel + Markdown）
├── db.py                # SQLite 数据库（历史记录 + 偏好规则持久化）
├── preferences.py       # 偏好学习模块（从用户编辑中提取偏好规则）
├── examples/
│   └── sample_requirement.md  # 示例需求文档
├── data.db              # SQLite 数据库文件（自动创建）
└── output/              # 生成的用例文件（自动创建）
```

## 配置说明

### config.yaml

```yaml
# ---------- 测试用例生成模型 ----------
generate:
  api_type: "openai"           # anthropic / openai
  base_url: "..."              # API 地址
  api_key: "..."               # API Key
  model: "your-model"          # 文本生成模型
  image_model: "your-vlm"      # 图片输入模型（可选，不填则图片也用主模型）
  temperature: 0.3             # 温度 (0.0-1.0)，越低越确定
  max_tokens: 4096             # 最大 token 数
  max_retries: 3               # 失败重试次数
  enable_thinking: true        # 思考模式，深度推理质量更高

# ---------- 测试用例评审模型 ----------
review:
  enabled: false               # 设为 true 启用独立评审模型
  api_type: "openai"
  base_url: "..."
  api_key: "..."
  model: "..."

# ---------- 输出配置 ----------
output:
  dir: "./output"              # 输出目录
  format: "all"                # excel / markdown / all

# ---------- 用例配置 ----------
testcase:
  default_priority: "P1"       # P0(阻塞) / P1(严重) / P2(一般) / P3(轻微)
  max_testcases: 100           # 单次生成最大用例数
  case_types:                  # 用例类型（可自行增减）
    - "功能测试"
    - "边界测试"
    - "异常测试"
    - "兼容性测试"
    - "性能测试"

# ---------- 数据库配置 ----------
database:
  path: "./data.db"            # SQLite 数据库文件路径

# ---------- 偏好学习配置 ----------
preferences:
  enabled: true                # 是否启用偏好学习
  max_injected: 10             # 注入 prompt 的最大偏好条数
```

### 支持的模型

| 模型 | api_type | base_url | model |
|------|----------|----------|-------|
| 小米 MiMo | `openai` | `https://token-plan-cn.xiaomimimo.com/v1` | `mimo-v2.5-pro` |
| DeepSeek | `openai` | `https://api.deepseek.com` | `deepseek-chat` |
| 通义千问 | `openai` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 GLM | `openai` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| Moonshot | `openai` | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |

## 用例级别说明

| 级别 | 含义 | 说明 |
|------|------|------|
| P0 | 阻塞 | 核心功能不可用，必须修复 |
| P1 | 严重 | 主要功能异常，影响业务流程 |
| P2 | 一般 | 次要功能问题，不影响主流程 |
| P3 | 轻微 | 体验优化类问题 |

## 常见问题

**Q: 报错 `ModuleNotFoundError: No module named 'xxx'`**
A: 运行 `pip install -r requirements.txt` 安装依赖。

**Q: 报错 `API Key 无效` 或 `401 Unauthorized`**
A: 检查 `config.yaml` 中的 `api_key` 是否正确，确认模型服务是否到期。

**Q: 生成的 JSON 解析失败**
A: 已内置多级容错（json -> 修复控制字符 -> json5 -> 正则提取），一般可自动恢复。如仍失败，会自动重试生成。

**Q: 生成速度慢怎么办**
A: 已支持模块并行生成（最多 5 路并发），多个模块同时调用 LLM。如仍较慢，可关闭思考模式（`enable_thinking: false`）或换用更快的模型。

**Q: 生成的用例太多 / 太少**
A: 在 `config.yaml` 中调整 `max_testcases`（默认 100）。超出上限时自动按优先级保留高优先级用例。如用例太少，可在需求中更详细地描述各功能模块。

**Q: 优化后用例数量减少了**
A: 这是正常的去重结果。评审阶段会检测重复用例并输出清单，优化阶段按清单精准删除重复项，同时补充遗漏场景。如果优化结果为空会自动回退到原始用例。

**Q: 图片上传后 AI 没有识别到界面元素**
A: 确认 `config.yaml` 中配置了 `image_model`（多模态模型）。图片会贯穿整个生成链路：模块分析时结合图片拆解模块，每个模块生成时结合图片中的具体元素编写用例。

**Q: 如何切换模型**
A: 编辑 `config.yaml`，修改 `generate` 下的 `api_type`、`base_url`、`api_key`、`model`。

**Q: 思考模式是什么**
A: 开启后 LLM 会先做深度推理再输出，对复杂需求的场景覆盖更全面，但响应更慢、token 消耗更多。在 `config.yaml` 中设置 `enable_thinking: true` 开启。

**Q: Web 版端口被占用**
A: 使用 `python start.py -p 8080` 指定其他端口。

**Q: 历史记录保存在哪里**
A: 保存在项目目录下的 `data.db`（SQLite 数据库）。删除该文件会清空所有历史记录和偏好规则。

**Q: 偏好学习是如何工作的**
A: 在 Web 界面生成用例后，你可以直接编辑用例内容，然后点击"提取偏好"按钮。AI 会对比修改前后的差异，归纳出通用的偏好规则（如"步骤应使用具体页面元素名称"），并存入数据库。后续生成时这些规则会自动注入 Prompt，让用例风格越来越贴合你的习惯。你可以在"偏好管理"面板中启用、禁用或删除规则。

**Q: 如何导入禅道 / Jira**
A: 下载生成的 Excel 文件，在禅道的"测试-用例"页面使用"导入"功能，选择对应格式即可。

## License

MIT
