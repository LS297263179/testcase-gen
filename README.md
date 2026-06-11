# AI 测试用例生成器

基于大模型（LLM）的测试用例自动生成工具。输入需求描述或 UI 截图，自动生成测试点和测试用例，支持 Excel / Markdown 多格式导出，提供 Web 可视化界面。

## 功能特性

### 核心能力

- **多模态输入**：支持 Markdown / TXT 文档、Excel 表格、UI 截图 / 设计稿、手动输入，文字与图片可混合使用
- **智能分段生成**：自动拆解需求为功能模块，按模块并行生成（最多 5 路并发），覆盖更全面、速度更快
- **图片深度识别**：图片贯穿整个生成链路（模块分析 + 每个模块生成），AI 会结合界面元素编写用例
- **数量可控**：单次生成上限可配置（默认 100 条），超出时按优先级智能裁剪
- **思考模式**：支持 LLM 深度推理（enable_thinking），复杂需求下用例质量更高

### 测试点生成

- **需求到测试点**：输入需求描述，AI 自动按模块拆解测试点，树形结构展示
- **需求分析预览**：生成前先分析需求复杂度和模块拆解，用户确认后再生成
- **项目材料引用**：生成测试点时可勾选已保存的项目材料，AI 结合材料信息生成更精准的测试点
- **多格式导出**：支持导出为 Markdown 格式
- **自动保存**：生成的测试点自动保存到数据库，支持历史查看和加载

### 测试用例生成

- **完整用例生成**：从需求直接生成包含前置条件、测试步骤、预期结果的完整用例
- **XMind 转用例**：上传 XMind 思维导图，AI 自动解析结构并生成测试用例，导出 Excel
- **双向追溯**：每条用例标注来源需求模块
- **项目材料引用**：生成用例时可勾选已保存的项目材料作为参考
- **测试点引用**：生成用例时可选择已生成的测试点作为参考，AI 结合测试点细化用例
- **AI 评审**：6 维质量评审（覆盖率、准确性、可执行性、一致性、遗漏风险、重复检测）
- **精准优化**：根据评审报告自动优化用例，展示变更对比（新增 / 修改 / 删除）
- **批量操作**：支持全选、批量修改优先级、批量删除

### 项目材料管理

- **材料创建**：输入标题 + 文本内容 + 上传多张图片，保存为项目材料
- **材料引用**：生成测试点或测试用例时，勾选材料即可让 AI 参考
- **展开查看**：材料列表支持展开 / 折叠查看内容和图片

### 去重与质量

- **多层去重**：Prompt 层引导 LLM 避免重复 → 精确去重（标题+预期） → 步骤语义去重（Jaccard 相似度 > 0.7）
- **变更对比**：优化后基于内容相似度匹配展示新增 / 修改 / 删除的 diff

### 历史记录与偏好学习

- **SQLite 持久化**：每次生成自动保存到本地数据库，支持历史记录浏览、详情查看、删除
- **偏好学习**：用户编辑用例后，AI 自动提取修改差异并归纳为偏好规则
- **偏好注入**：后续生成时自动将活跃偏好规则注入 Prompt，用例风格越用越贴合用户习惯
- **用户隔离**：偏好规则按用户隔离，多用户互不影响

### 用户系统

- **注册 / 登录**：用户名 + 密码认证（密码最少 8 位），密码哈希存储，支持多用户
- **数据隔离**：每个用户只能查看自己的历史记录、项目材料、测试点和偏好规则
- **会话管理**：基于 Flask session 的登录态，401 自动跳转登录页
- **速率限制**：基于 IP 的登录 / 注册速率限制（60 秒内最多 10 次），支持反向代理

### 安全特性

- **API Key 加密存储**：使用 Fernet 对称加密，API Key 不再以明文形式存入数据库
- **Secret Key 持久化**：支持环境变量 → config.yaml → 随机生成三级 fallback
- **越权防护**：所有资源操作（删除材料/测试点/历史记录）均校验 user_id
- **路径安全**：文件下载接口校验路径，防止目录穿越攻击
- **文件大小限制**：上传文件最大 32MB

### 模型配置

- **预设快速切换**：内置 MiMo、DeepSeek（阿里云）、通义千问（阿里云）、Kimi、OpenAI GPT 预设
- **Key 智能复用**：同平台模型切换自动复用 API Key，不同平台提示输入
- **自定义配置**：支持手动配置 Base URL、模型名、API Key、Temperature 等参数
- **独立评审模型**：评审可配置不同于生成的模型，灵活组合
- **Web 界面配置**：在「模型配置」页面直接修改，无需手动编辑 config.yaml

### 导出与交互

- **多格式导出**：支持 Excel / Markdown / JSON 格式
- **SSE 实时进度**：生成、评审、优化全程显示当前步骤
- **粘贴图片**：在文本框直接 Ctrl+V 粘贴截图，自动添加到上传区

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
secret_key: "your-random-secret-key"   # Flask session 加密密钥

generate:
  api_type: "openai"
  base_url: "https://your-api-endpoint/v1"
  api_key: "your-api-key"
  model: "your-model"
  image_model: "your-vlm"        # 图片模型（可选）
  temperature: 0.3
  max_tokens: 4096
  enable_thinking: true

review:
  enabled: false                  # 设为 true 启用独立评审模型

output:
  dir: "./output"
  format: "all"

testcase:
  default_priority: "P1"
  max_testcases: 100
  case_types: [功能测试, 边界测试, 异常测试, 兼容性测试, 性能测试]
```

> 也可以启动后在 Web 界面的「模型配置」页面直接配置，无需手动编辑文件。

### 3. 启动

```bash
python start.py
```

浏览器自动打开 `http://localhost:5000`，注册账号后即可使用。

**命令行版：**

```bash
python main.py your_requirement.md
```

## Docker 部署

```bash
# 创建 .env 文件（从模板复制）
cp .env.example .env
# 编辑 .env，填入配置

# 启动
docker-compose up -d

# 访问 http://localhost:5000
```

**环境变量（.env）：**

| 变量 | 说明 |
|------|------|
| `FLASK_SECRET_KEY` | Flask session 加密密钥 |
| `FERNET_KEY` | API Key 加密密钥（不设置则自动生成） |
| `GENERATE_API_TYPE` | 生成模型 API 类型（openai / anthropic） |
| `GENERATE_BASE_URL` | 生成模型 Base URL |
| `GENERATE_API_KEY` | 生成模型 API Key |
| `GENERATE_MODEL` | 生成模型名称 |
| `GENERATE_IMAGE_MODEL` | 图片模型名称（可选） |
| `REVIEW_ENABLED` | 是否启用独立评审模型 |
| `REVIEW_BASE_URL` | 评审模型 Base URL |
| `REVIEW_API_KEY` | 评审模型 API Key |
| `REVIEW_MODEL` | 评审模型名称 |

## 使用说明

### Web 版

```bash
python start.py              # 默认 5000 端口，自动打开浏览器
python start.py -p 8080      # 指定端口
python start.py --host 0.0.0.0  # 监听所有地址（局域网访问）
python start.py --no-browser # 不自动打开浏览器
python start.py --debug      # 调试模式
```

**界面布局：**

页面采用顶部 Header + 左侧导航栏 + 右侧内容区的布局：

| 导航项 | 功能 |
|--------|------|
| 工作台 | 统计概览、快速入口、最近生成记录 |
| 测试用例 | 输入需求、上传文件、生成完整测试用例 |
| 测试点 | 输入需求、生成测试点、导出 Markdown |
| XMind 转用例 | 上传 XMind 思维导图，AI 转换为测试用例并导出 Excel |
| 项目材料 | 创建 / 管理项目资料（标题+文本+图片） |
| 模型配置 | 预设模型切换、自定义生成和评审模型参数 |

**测试用例页面（左右分栏）：**

| 左侧 | 右侧 |
|------|------|
| 需求输入文本框 | 用例统计 + 操作按钮 |
| 图片上传区 | 用例表格（可批量选择） |
| 参数配置（优先级 / 类型） | AI 评审报告 |
| 项目材料勾选 | 优化变更对比 |
| 测试点勾选 | 偏好规则管理 |
| 需求分析结果 | |

**测试点页面（左右分栏）：**

| 左侧 | 右侧 |
|------|------|
| 需求输入文本框 | 测试点树形展示（按模块分组） |
| 图片上传区 | 导出 Markdown |
| 项目材料勾选 | 历史记录（加载 / 删除） |

**项目材料页面：**

- 左侧：创建材料（标题 + 文本 + 多张图片）
- 右侧：已有材料列表（展开 / 折叠查看）

### XMind 转用例

在「XMind 转用例」页面，可上传 XMind 思维导图文件，AI 自动解析结构并生成测试用例。

**模板结构（4层）：**

```
项目名称（根节点）
  └─ 功能模块       → Excel【module】字段
       └─ 测试场景   → 归类用例
            └─ 用例标题 → Excel【title】字段
                 ├─ 步骤：xxx   → Excel【steps】字段
                 └─ 预期结果：xxx → Excel【expected】字段
```

- 每个用例下挂「步骤」和「预期结果」两个子节点
- 优先级（priority）、前置条件（precondition）、备注（remark）由 AI 自动生成
- 可点击「下载模板」获取示例模板，模板中包含详细的层级说明和字段映射

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

## 项目结构

```
testcase-gen/
├── start.py              # Web 版启动器
├── web.py                # Flask Web 应用（全部 API + SSE 流式）
├── templates/
│   └── index.html        # Web 前端（工作台 + 测试用例 + 测试点 + 材料 + 模型配置）
├── main.py               # 命令行入口
├── config.yaml           # 配置文件（模型、输出、用例参数）
├── config.yaml.example   # 配置文件模板
├── .env.example          # 环境变量模板（Docker 部署用）
├── Dockerfile            # Docker 镜像定义
├── docker-compose.yml    # Docker Compose 编排
├── .gitignore
├── .dockerignore
├── requirements.txt      # Python 依赖
├── llm_client.py         # LLM 调用封装（双 SDK + 多模态 + 思考模式 + 重试）
├── reader.py             # 需求文档读取（MD / TXT / Excel / 图片）
├── generator.py          # 测试用例生成（并行分段 + Prompt + 去重 + JSON 容错解析）
├── reviewer.py           # 测试用例评审（6 维评审 + 重复检测）+ 精准优化
├── output.py             # 输出模块（Excel + Markdown）
├── xmind_utils.py        # XMind 文件解析 + 模板生成（支持 XMind 8+ JSON 和旧版 XML）
├── db.py                 # SQLite 数据库（用户 + 历史 + 材料 + 测试点 + 偏好 + API Key 加密）
├── preferences.py        # 偏好学习模块
├── examples/
│   └── sample_requirement.md
├── data/                 # 数据目录（自动创建）
│   └── data.db           # SQLite 数据库文件
└── output/               # 生成的文件（自动创建）
```

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/register` | POST | 用户注册 |
| `/api/login` | POST | 用户登录 |
| `/api/logout` | POST | 用户登出 |
| `/api/me` | GET | 获取当前用户信息 |
| `/api/dashboard` | GET | 仪表盘统计 |
| `/api/model-presets` | GET | 预设模型列表 |
| `/api/model-config` | GET/POST | 获取/保存模型配置 |
| `/api/analyze` | POST | 需求分析（SSE） |
| `/api/generate` | POST | 生成测试用例（SSE） |
| `/api/generate-points` | POST | 生成测试点（SSE） |
| `/api/export-points` | POST | 导出测试点 |
| `/api/review` | POST | AI 评审（SSE） |
| `/api/optimize` | POST | 优化用例（SSE） |
| `/api/download/<filename>` | GET | 下载文件 |
| `/api/history` | GET | 历史记录列表 |
| `/api/history/<id>` | GET/DELETE | 获取/删除历史记录 |
| `/api/preferences` | GET | 偏好规则列表 |
| `/api/preferences/extract` | POST | 提取偏好规则（SSE） |
| `/api/materials` | GET/POST | 项目材料列表/创建 |
| `/api/materials/<id>` | GET/DELETE | 获取/删除项目材料 |
| `/api/test-points` | GET | 测试点历史列表 |
| `/api/test-points/<id>` | GET/DELETE | 获取/删除测试点 |
| `/api/xmind-template` | GET | 下载 XMind 参考模板 |
| `/api/xmind2case` | POST | 上传 XMind 转换为测试用例（SSE） |

## 支持的模型

| 模型 | Provider | Base URL | 说明 |
|------|----------|----------|------|
| MiMo | mimo | `https://token-plan-cn.xiaomimimo.com/v1` | 小米，多模态 |
| DeepSeek V4 Pro | dashscope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 阿里云 |
| 通义千问 Max | dashscope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 阿里云 |
| Kimi | moonshot | `https://api.moonshot.cn/v1` | 月之暗面 |
| GPT-4o | openai | `https://api.openai.com/v1` | OpenAI |

> 阿里云平台的 DeepSeek 和通义千问共用同一个 API Key。

## 常见问题

**Q: 报错 `ModuleNotFoundError`**
A: 运行 `pip install -r requirements.txt` 安装依赖。

**Q: 报错 `API Key 无效`**
A: 在「模型配置」页面检查 API Key 是否正确，确认模型服务是否到期。

**Q: 生成速度慢**
A: 已支持模块并行生成（最多 5 路并发）。可关闭思考模式（`enable_thinking: false`）或换用更快的模型。

**Q: 如何切换模型**
A: 在「模型配置」页面点击预设按钮即可切换。同平台模型（如阿里云的 DeepSeek 和千问）自动复用 Key。

**Q: 测试点和测试用例的区别**
A: 测试点是轻量级的测试大纲（按模块列出测试点标题），适合快速梳理测试范围。测试用例是完整的测试文档（包含前置条件、步骤、预期结果），适合执行测试。

**Q: 项目材料怎么用**
A: 先在「项目材料」页面创建资料（标题 + 文本 + 图片），然后在生成测试点或测试用例时，勾选要引用的材料，AI 会结合材料内容生成更精准的结果。

**Q: 测试点怎么用**
A: 先在「测试点」页面生成测试点，然后在「测试用例」页面生成用例时，勾选要引用的测试点，AI 会根据测试点细化生成更完整的测试用例。

**Q: 历史记录保存在哪里**
A: 保存在 `data/data.db`（SQLite 数据库）。删除该文件会清空所有数据。

**Q: API Key 是如何存储的**
A: 使用 Fernet 对称加密后存入数据库，不会以明文形式存储。加密密钥可通过环境变量 `FERNET_KEY` 指定，不设置则自动生成。

**Q: Docker 部署后数据在哪里**
A: 数据库文件在 `data/` 目录，生成的文件在 `output/` 目录，两个目录都通过 docker-compose 挂载到宿主机。

## License

MIT
