# testcase-gen — AI 测试工程平台

## 一句话
Flask + SQLite + LLM 的 AI 测试工程平台（AI Test Engineering Platform）。含两条并存产品线：
**V1**（`/`，Prompt→用例的自由文本链路）与 **V2**（`/v2`，结构化测试工程链路 + Benchmark 质量评价）。

## 技术栈
Python >= 3.11 / Flask / SQLite / Pydantic v2 / ruff (line-length 120, double-quotes) / pytest
V2 前端：React + TypeScript + Vite（源码 `frontend-v2/`，构建产物 `static/v2/` 入库，部署无需 Node）

## 快速命令
```
启动:        python start.py [-p 5000] [--debug]          # V1 在 /，V2 在 /v2
测试:        pytest [-v] [-k keyword] [tests/test_file.py]
全量基线:    1378 passed + 3 skipped（3 = real_llm 标记用例，默认跳过；V2_RUN_REAL_LLM=1 启用）
Lint:        ruff check . && ruff format --check .
依赖安装:    pip install -e ".[dev]"

V2 一键验收: python scripts/v2_step10_verify.py [--with-real-llm]     # 22 条门槛报告表
V2 真实运行: python scripts/v2_real_run.py                            # CLI 权威真实链路
Benchmark:   python scripts/v2_benchmark.py [--dry-run] [--case bc_01_login]
Compare(S8): python scripts/v2_benchmark_compare.py --baseline benchmark/baselines/baseline-v0.1.json --candidate benchmark/runsets/<runset_id>
```

## 配置优先级（易错点）
```
LLM 生效源：环境变量 FLASK_SECRET_KEY（仅 secret_key）
           > V1 库 data/data.db 的 settings.model_config（网页「模型配置」保存，api_key 经 Fernet 加密）
           > config.yaml（gitignored，仅兜底）
⇒ 只改 config.yaml 往往不生效：网页保存过配置后 DB 会遮蔽它（DB 存成 "{}" 的历史缺陷已由守卫修复）。
   改模型要同时同步 config.yaml 兜底与注释；写 DB 会让 data.db 哈希变化（属预期，不是异常）。
```

## 项目结构（核心文件）

### V1
```
core/generator.py    → 用例生成 + 所有 Prompt
                       ├── ANALYSIS_PROMPT        # 需求分析拆解
                       ├── ANALYSIS_PROMPT_WITH_IMAGE
                       ├── MODULE_PROMPT           # 按模块生成
                       └── SYSTEM_PROMPT           # 一次性生成
core/db.py           → SQLite 操作（★ V2 不复用它，V2 有 core/v2/db.py）
core/llm_client.py   → LLM 调用封装（含 calls/attempts/retries/failures 对账；★ 不记录 token 用量）
core/reviewer.py     → 评审 + 优化（REVIEW_SYSTEM_PROMPT）
core/preferences.py  → 偏好学习（EXTRACT_SYSTEM_PROMPT）
core/output.py       → Excel/MD 导出 + 去重
web/data.py          → 测试点 Prompt（TEST_POINTS_PROMPT）+ API
web/generate.py      → 用例生成/评审/优化 API
static/app.js        → V1 前端全部逻辑
config.yaml          → 配置（不追踪 git）
```

### V2（`core/v2/`，独立库 `data/data_v2.db`，`ddl.SCHEMA_VERSION = 10`）
```
schemas/             → Pydantic 唯一真源（requirement/testpoint/testcase/strategy/review/run/…）
prompts.py ir.py parser.py validator.py ingestion.py   → Step 2 Requirement IR（分段/去重/严格校验）
tp_prompts.py tp_generator.py tp_validator.py tp_orchestrator.py  → Step 3 测试点（Phase A 逐项 + Phase B 跨项）
strategy/{boundary,equivalence,permission,engine,deriver,orchestrator}.py → Step 4 策略引擎（纯代码派生 obligation）
tc_prompts.py test_data_planner.py tc_generator.py tc_validator.py tc_orchestrator.py → Step 5 用例合成（Code-first + LLM 兜底）
traceability.py change_impact.py → Step 6 追溯链 + 变更影响（只读）
review_prompts.py review_hard.py review_soft.py review_orchestrator.py → Step 7 六维评审（硬/软混合）
optimizer.py optimizer_orchestrator.py → Step 8 去重归档（canonicalize pairs + 有条件重评审）
human_editor*.py     → Step 9 人工编辑（白名单 + Revision 快照 + 乐观锁）
runtime.py client_factory.py bootstrap.py → Step 10 顶层 Runtime（run_v2_pipeline）+ LLM 客户端工厂 + 建库
web/v2_service.py web/v2_routes.py → 14 个 /api/v2/* 端点（复用 V1 登录态与 CSRF）
frontend-v2/ templates/v2.html static/v2/ → V2 React SPA
fingerprint.py       → 业务确定性指纹（TestPoint / TestCase 身份与内容分离 / RequirementItem 双 hash）
repository.py ddl.py db.py resolver.py → 持久层（全 upsert，禁止 INSERT OR REPLACE）+ 多态引用代码层校验
```

### V2 质量评价（`core/v2/eval/` + `benchmark/`，Step 11 S1~S8）
```
eval/schema.py         → Case/Gold/Manifest 契约 + loader（fail-fast 数据集校验）
eval/matching.py       → 四态 Matching（match_keys 权威源 > statement 重算 > similarity 仅产 CANDIDATE）
eval/rails.py          → 三轨关联：identity（仅诊断）> obligation bridge > scenario anchor；D10 不猜 item_id
eval/metrics_hard.py   → 硬指标 + RunObservation 统一输入契约（Cost/Latency 含用量对账）
eval/metrics_soft.py   → S5 语义三态 + forbidden suspect + candidate 预审 + 批次失败证据（禁落原文）
eval/runner_lib.py     → S6：五态分类（阶段门控）+ 环境指纹 + 原子写/敏感自检 + 生产库护栏
eval/compare.py        → S8：baseline↔runset 只读 Compare/Delta（脱敏投影、可比性判据、None-safe、无阈值）
scripts/v2_benchmark.py         → S6 Runner（★ 独立进程，严禁在 Flask 内调用）
scripts/v2_benchmark_compare.py → S8 CLI（只读、零 LLM、零 DB；退出码 0/2/3/5，刻意无 1）
benchmark/{cases,gold,manifests}/ → bench-v0.1 数据集（10 case + 83 条人工独立 Gold）
benchmark/baselines/baseline-v0.1.json → 正式基线（机器可读，入库）
benchmark/runsets/ · benchmark/compares/ → 本地产物（均 gitignore）
docs/v2/PROGRESS.md    → ★ 单一进度真相源；新会话先读它 + docs/v2/step11-benchmark-evaluation.md
```

## 开发规则

### 修改 Prompt 守则
1. 定位到对应文件的对应变量名（见下方 Prompt 速查）
2. 修改后**必须重启应用**生效
3. **必须向后兼容**旧数据格式 — 旧格式无 `subcategories`，已在 `_normalize_points_format()` 处理
4. 修改后运行 `pytest` 验证
5. V2 的 6 个业务 Prompt 各有版本常量，改动正式 Prompt 会改变 runset 环境指纹 ⇒ 需重算 baseline

### Benchmark / 评价层纪律（Step 11 冻结边界）
- **不改** Gold、bench-v0.1 数据集、Matching / Metrics / S5 判定规则、Runtime、orchestrator、`core/schemas`、DDL、S6 CLI 语义
- 质量指标**只做观察不做门禁**：不加固定阈值、不因 regression 返回非 0、不接 CI Gate
- baseline 必须**单一模型 + 全量 10 case 一次跑完 + 工作树干净**，禁止跨模型/跨 attempt 拼接
- 评价层零真实 LLM 的改动优先离线验证；`identity_match_rate` 仅诊断，**禁止当覆盖读数**
- 禁止反向修改 Gold 迁就模型

### Git 约定
- 不要直接推 main 分支，除非用户明确要求
- commit 信息带中文描述
- 推送两个 remote：`origin`（github）和 `gitee`
- 推送到 gitee 需绕过代理：`git -c http.proxy="" -c https.proxy="" push gitee main`
- 不提交：`config.yaml`、`data/*.db`、`benchmark/runsets/`、`benchmark/compares/`、`output/`、截图

### 代码规范
- ruff 自动检查 + 格式化
- `known-first-party = ["core", "web"]`（isort 不会把内部模块当三方库）
- 变量/函数用 snake_case，类用 PascalCase
- 持久层一律 `INSERT ... ON CONFLICT(id) DO UPDATE`（`INSERT OR REPLACE` 会触发级联删子数据）

## 数据模型
```
[V1]
测试点（三级结构）:  module → subcategories[] → {name, points[] → {title, description}}
测试用例:            session → testcases[] → {id, module, title, precondition, steps, expected, priority, type}

[V2]（每层都有 fingerprint / content_hash 与 provenance，身份与内容分离）
RequirementDoc → RequirementVersion → RequirementItem{FieldSpec, BusinessRule, PermissionRule}
Run{generation_config_id} + GenerationConfig{model/prompt/generator/reviewer 版本}
TestPoint{generation_scope: item|cross_item|strategy, item_ids M:N, obligation_id?}
CoverageObligation + obligation_coverage 关系表（★ 覆盖唯一事实源，TestCase 间接继承不双写）
TestCase{steps[], status 状态机, generation_mode: code|llm|hybrid, data_plan, fingerprint, content_hash}
ReviewReport{6 维 ReviewScores + ReviewFinding} · TestCaseRevision{changed_fields, 乐观锁}
```

### 兼容性
- 数据库中的旧测试点（无 subcategories）在读取时自动通过 `_normalize_points_format()` 转换为新格式
- 前端 `renderTestPoints()` 同时支持新旧两种格式
- V2 独立数据库 `data/data_v2.db`；V1 `data.db` 保持不动。V1→V2 迁移遵循「不猜历史关系、未知=NULL、provenance=migrated」

## Prompt 速查

### V1
| 你要改什么 | 文件 | 变量名 |
|-----------|------|--------|
| 测试点生成质量 | `web/data.py` | `TEST_POINTS_PROMPT` |
| 用例-需求分析逻辑 | `core/generator.py` | `ANALYSIS_PROMPT` |
| 用例-分模块生成内容 | `core/generator.py` | `MODULE_PROMPT` |
| 用例-快速生成内容 | `core/generator.py` | `SYSTEM_PROMPT` |
| 用例评审维度 | `core/reviewer.py` | `REVIEW_SYSTEM_PROMPT` |
| 偏好规则提取 | `core/preferences.py` | `EXTRACT_SYSTEM_PROMPT` |
| 用例数量控制 | `core/generator.py` | `COMPLEXITY_CASE_COUNT` |

### V2（含版本常量，改动会进环境指纹）
| 你要改什么 | 文件 | 变量名 / 版本常量 |
|-----------|------|-------------------|
| 需求抽取（IR） | `core/v2/prompts.py` | `IR_EXTRACTION_PROMPT` / `PARSER_PROMPT_VERSION=requirement-parser-v1` |
| 测试点 Phase A（逐项） | `core/v2/tp_prompts.py` | `TEST_POINT_GENERATOR_PROMPT` / `test-point-generator-v1` |
| 测试点 Phase B（跨项） | `core/v2/tp_prompts.py` | `TEST_POINT_COMPLETER_PROMPT` / `test-point-completer-v1` |
| 用例合成内容 | `core/v2/tc_prompts.py` | `TEST_CASE_SYNTHESIZER_PROMPT` / `test-case-synthesizer-v1` |
| 复杂正则测试数据 | `core/v2/tc_prompts.py` | `TEST_DATA_PATTERN_PROMPT` / `test-data-pattern-v1` |
| V2 六维评审 | `core/v2/review_prompts.py` | `TEST_CASE_REVIEWER_PROMPT` / `test-case-reviewer-v1` |
| 基准语义评价（S5） | `core/v2/eval/semantic_prompts.py` | `SEMANTIC_EVAL_PROMPT` / `benchmark-semantic-eval-v1` |

### V2 链路入口函数
```
run_v2_pipeline(...)                        # core/v2/runtime.py  一次串起 Step 2→8
build_requirement_ir / generate_test_points / apply_strategy_engine
synthesize_test_cases / review_test_cases / optimize_duplicates
load_benchmark_suite(...) → scripts/v2_benchmark.py → runset → scripts/v2_benchmark_compare.py
```

## 常见开发场景

### 场景 1：想让 AI 生成的测试点更详细
→ 编辑 `web/data.py` 的 `TEST_POINTS_PROMPT`，加强 `description` 字段的要求描述

### 场景 2：想让测试用例覆盖更多安全测试
→ 编辑 `core/generator.py` 的 `MODULE_PROMPT` 或 `SYSTEM_PROMPT`，在覆盖维度/方法论中强化安全相关描述
→ V2：编辑 `core/v2/tp_prompts.py`（维度枚举 `_DIMENSION_ENUM`）与 `core/v2/tc_prompts.py`，并跑 Benchmark 看覆盖读数变化

### 场景 3：调整生成速度 vs 质量
→ 调大 `config.yaml` 的 `max_tokens` 提高质量（但更慢）
→ 调小或调大 `COMPLEXITY_CASE_COUNT` 控制用例数量
→ 开启 `enable_thinking: true`（深度推理，更慢但更准；★ 关闭时须显式下发 `enable_thinking=False`，否则部分网关默认开思考会把正文留空）
→ 注意：V2 Step 5 是「每个测试点 1 次 LLM 调用」，IR 粒度翻倍 ⇒ 墙钟与额度同步翻倍

### 场景 4：换模型
→ 先看实际生效源（DB `settings.model_config` 优先于 `config.yaml`）：网页「模型配置」保存，或 `core.db.save_model_config`
→ 同步改 `config.yaml` 的 `generate`/`review` 段（`api_type`、`base_url`、`api_key`、`model`）与注释
→ 若该模型用于跑 baseline：换模型即修订裁决 D5，须用户拍板；**换模型 = 换调用预算**，须重算并按同一模型整轮重跑

### 场景 5：验证一次改动有没有让质量退化
```bash
python scripts/v2_benchmark.py                                  # 产出新 runset（真实 LLM）
python scripts/v2_benchmark_compare.py \
  --baseline benchmark/baselines/baseline-v0.1.json \
  --candidate benchmark/runsets/<新 runset_id>                   # 零 LLM，只看 delta
```
跨模型/跨数据集会被判 `not_comparable`（不静默混算）；`improvement/regression` 只是陈述，不构成门禁。

### 场景 6：改持久层/Schema 前
→ 先读 `docs/v2/PROGRESS.md`（§4 Step 1 数据模型与迁移铁律、§6 `INSERT OR REPLACE` + 级联删除陷阱）与 `docs/v2/step1-data-model.md`
→ `schema_version` 变更必须带自动升级分支；V1 库与 V1 运行时零修改
