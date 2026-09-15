# V2 蓝图与进度（交接文档）

> **新会话请先读本文件**，再按需读 `docs/v2/step1-data-model.md`（Step 1 数据模型详细设计）。
> 本文件是 V2 重构的"单一进度真相源"。每完成一步请回来更新"进度总览"与对应 Step 段落。

---

## 0. 给新会话的上下文（一句话）

本项目 V1 = 基于 LLM 的测试用例生成器（Flask + SQLite + 原生前端）。现按 **13 步蓝图**重构为 **V2**。
V2 的核心不是 `Prompt→LLM→Result`，而是 **"结构化数据 → 规则/策略 → LLM → 结构化数据 → Validator → Reviewer → 结构化数据"**：LLM 是大脑但不单独控制系统，测试的确定性关注点尽量代码化。

**当前进度：Step 1 已完成并推送；Step 2 已完成、通过 14 项验收门槛、已推送；Step 3 已完成、通过 15 项验收门槛（含 12 条主门槛 + 3 条附加）、待用户确认后推送；Step 4 未开始（需先讨论方案）。**

---

## 1. 核心原则（贯穿所有 Step，务必遵守）

```
结构化数据 → 规则/策略 → LLM → 结构化数据 → Validator → Reviewer → 结构化数据
```

**必须代码化（不让模型自由发挥）的确定性关注点**：边界值、等价类、重复检测、字段校验、Schema 校验、ID 生成、格式校验、必填项、权限矩阵、覆盖率计算。
**只交给 LLM 的**：需求语义理解、内容生成（用例文案）、软性判断（评审中的"遗漏风险"等）。
LLM 的输入/输出两端都必须是已定义 Schema 的结构化数据；LLM 产物一律先过 Validator（代码）再入库。

---

## 2. V2 完整蓝图（13 步 + 远期）

```
现有 V1
 ├─ Step 1：冻结数据模型 / Schema          ✅ 完成
 ├─ Step 2：建立 Requirement IR             ✅ 完成
 ├─ Step 3：重构"需求 → 测试点"             ✅ 完成
 ├─ Step 4：加入测试策略引擎（代码算边界/等价类/权限矩阵/覆盖义务）
 ├─ Step 5：重构"测试点 → 测试用例"
 ├─ Step 6：建立 Traceability 追溯链（需求→测试点→用例）+ 变更影响分析
 ├─ Step 7：升级 AI Reviewer（6 维结构化评审 + Validator）
 ├─ Step 8：升级去重体系
 ├─ Step 9：加入人工编辑/确认闭环
 ├─ Step 10：Preference Learning
 ├─ Step 11：Prompt 分层 + 版本管理
 ├─ Step 12：Benchmark / 自动评测
 └─ Step 13：前端 V2 + Excel/MD/JSON 输出

远期（V2 稳定后）：AI 自动执行 → Playwright/API → 失败分析 → 自动修复 → AI Testing Agent
```

**目标数据流（蓝图）**：
```
用户需求(PDF/Word/图片/MD/Text) → Ingestion(解析/图片理解) → Requirement Parser
  → Requirement IR → { Test Point Generator + Strategy Engine } → Test Case Generator
  → AI Reviewer(6维) → Optimizer(自动优化/去重) → Human Review(人工确认/编辑)
  → Traceability → Preference Learning → Excel/Markdown/JSON
```

---

## 3. 进度总览

| Step | 状态 | 关键产物 | 提交 |
|---|---|---|---|
| 1 冻结数据模型 | ✅ 完成+验证 | 设计文档 + `core/schemas/`(10) + `core/v2/`持久层 + 迁移 + 测试(209) | 已推 origin+gitee |
| 2 Requirement IR | ✅ 完成+验证(14门槛全过) | `core/v2/`{prompts,ingestion,parser,validator,ir} + 4 测试文件 + 修复 Step1 upsert bug | 已推 origin+gitee |
| 3 测试点生成引擎 | ✅ 完成+验证(12+3门槛全过) | `core/v2/`{tp_prompts,tp_generator,tp_validator,tp_orchestrator,fingerprint} + Schema/DDL/Repo 升级(v2→v3) + 4 测试文件 | 待用户确认后推 |
| 4~13 | ⬜ 未开始 | — | — |

**测试基线**：V1 原有 126 例（零回归）+ V2 新增，**当前全量 345 passed**（Step 1 层 290 + Step 3 新增 55），ruff check/format 全绿。

---

## 4. Step 1 详情（冻结数据模型）✅

**设计文档**：`docs/v2/step1-data-model.md`（rev.2，含 P0×6 + P1×5 评审反馈 + 端到端示例 + 20 点对照表）。**新会话必读**。

**核心实体（Pydantic v2 为唯一真源）**：
- 需求层：`RequirementDoc → RequirementVersion → RequirementItem → FieldSpec/BusinessRule/PermissionRule`
  - **RequirementVersion**（P0-1）：需求变更=新增版本，不覆盖历史；历史测试资产仍指向旧版本。
  - **FieldSpec**：字段级类型/约束（长度/范围/正则/枚举/必填/可空/唯一）——Step 4 策略引擎据此算边界值/等价类。
- 测试资产：`TestPoint`（关联 `item_ids`）、`TestCase`（含 `steps: list[TestStep]`、`status` 状态机、`fingerprint`）
- 策略：`CoverageObligation`（代码推导的"必须覆盖项"）+ `obligation_coverage` 关系表（覆盖**唯一事实源**）
- 评审：`ReviewReport`（多轮 `revision`+`trigger_type`）+ `ReviewScores`（6 维固定字段）+ `ReviewFinding`（多态 target）
- 审计：`Run`（绑定 `requirement_version_id` + `generation_config_id`）、`GenerationConfig`（model/prompt/generator/reviewer 版本）
- 预留(🟡只设计不建表)：`TestScenario`、`TestCaseRevision`、`LLMInvocation`

**关键决策**：
1. **独立数据库 `data/data_v2.db`**（V1 用 `data.db` 保持不动、零风险、可整体回滚）；V2 用 **ULID** 主键；迁移=读 V1 写 V2。
2. Pydantic v2（`extra="forbid"` 严格）；枚举用 `StrEnum`；`core/db.py` 是 V1 模块，故 V2 持久层用 `core/v2/` 包（不能建 `core/db/` 包）。
3. 多态引用 `(target_type,target_id)` 无 DB 外键 → 由 `core/v2/resolver.py` 的 `ReferentialValidator` 在代码层校验（DB 做不到，Domain 补）。
4. `web/__init__.py` 的 E402（Blueprint 在 app 之后导入）用 per-file-ignore 保留。

**迁移**：`core/v2/migrate_v1_to_v2.py`，遵循铁律"不猜历史关系、未知=NULL、provenance=migrated"。真实 data.db 实测：7 runs / 106 用例零丢失 / 3 孤儿记录归 legacy 用户不丢弃。

---

## 5. Step 2 详情（Requirement IR）✅

**目标**：原始需求（文本/MD/图片）→ 结构化、经代码校验、已持久化的 IR。对应蓝图 `Ingestion → Requirement Parser → Requirement IR`。

**交付文件**（全在 `core/v2/`）：
| 文件 | 职责 |
|---|---|
| `prompts.py` | IR 抽取 Prompt（版本 `requirement-parser-v1`）+ 分段提示 |
| `ingestion.py` | 归一化输入（复用 V1 `core/reader`：text/md/excel/图片）；PDF/Word 按确认延后 |
| `parser.py` | LLM 调用 + 鲁棒 JSON 提取 + 白名单过滤 + Pydantic 校验 + 代码侧修复(类型推断/置信度clamp/source_ref归一) + **超长自动分段** |
| `validator.py` | 代码级 IR 校验：去重(module+归一statement) + 结构检查 + 置信度分级 + seq 重排 + 结构化报告 |
| `ir.py` | `build_requirement_ir`（Doc→Version→解析→校验→持久化，支持 v1/v2+）；**`ingest_and_build_ir`**（文件/文本一步到 IR 的顶层链式入口） |

**测试**（全 mock，不依赖真实 API）：`tests/test_ir_parser.py`、`test_ir_validator.py`、`test_ir_ingestion.py`、`test_ir_build.py`、`test_step2_acceptance.py`（14 项验收逐条对应）。

**14 项验收门槛：全部 PASSED**（文本/Markdown/图片解析、严格 Pydantic、自动分段、分段合并、去重、不臆造、source_ref、低置信识别、创建 v1、从 doc 建 v2、v1/v2 互不覆盖、mock 全过）。

**诚实边界**：#8"不臆造规则"、#9"尽可能 source_ref"——测试证明的是**代码侧**不臆造/会抽取；真实 LLM 是否臆造/是否每项都给 source_ref 靠 Prompt 约束，需真实 API 抽查（mock 无法确定性验证）。

---

## 5.5 Step 3 详情（测试点生成引擎）✅

**目标**：消费 Step 2 的 `RequirementVersion + RequirementItem[]`，两阶段 LLM 生成 `TestPoint(provenance=LLM)`，M:N 关联回 `item_ids`。对应蓝图 `Requirement IR → Test Point Generator`。

**两阶段生成策略**（用户确认）：
- **Phase A**（`test-point-generator-v1`）：逐 `RequirementItem` 独立调 LLM，产基础 TestPoint（`generation_scope=item`、`item_ids` 强制覆写为 `[当前 item.id]`）。
- **Phase B**（`test-point-completer-v1`）：全量 items + Phase A 摘要一起喂 LLM，只补跨项交互/联动/状态迁移测试点（`generation_scope=cross_item`、`item_ids ≥ 2`）；items 数量 > 30 时按 module 分批。

**Step 3 硬性约束**（Validator 代码强制覆写，与 Step 4 策略引擎边界对齐）：
- `provenance = LLM`、`technique = None`、`obligation_id = None`、`status = DRAFT`
- `module` 必须来自关联 `RequirementItem.module`（Phase A 直接覆写；Phase B 校验 ∈ items.module 集合）
- `subcategory` 允许 LLM 在 IR 语义范围内合理归纳（如"输入校验"/"字段联动"）
- `generation_scope ∈ {item, cross_item}`、`fingerprint` 非空

**业务确定性 fingerprint**（幂等身份，替代不稳定的 ULID）：
```
fingerprint = sha256(version_id | generation_scope | sorted(item_ids) | module | subcategory | normalize(title))[:32]
            = "tp_" + 32位十六进制
```
DB 层加 `UNIQUE(fingerprint)` 索引；Repository.save_test_point 按 fingerprint 查旧行，同 fingerprint 复用旧 ULID（保证下游 `test_case_points`/`obligation_coverage` 不断链）。

**交付文件**（全在 `core/v2/`）：
| 文件 | 职责 |
|---|---|
| `fingerprint.py` | `compute_testpoint_fingerprint` + `normalize_text`（业务确定性指纹计算） |
| `tp_prompts.py` | Phase A/B 两个 Prompt + 版本号 + user prompt 模板 |
| `tp_generator.py` | LLM 调用 + 鲁棒 JSON 提取（复用 Step 2 `parser.extract_json`）+ Phase B 按 module 分批 |
| `tp_validator.py` | 白名单过滤 + Pydantic 严格校验 + Step 3 硬性覆写 + item_ids 归一 + 枚举兜底 + fingerprint 计算 + 去重 + 覆盖率报告 + 语义相似 warning |
| `tp_orchestrator.py` | `generate_test_points`（Run+GenerationConfig 强制创建 → Phase A/B → Validator → dedupe → upsert → coverage → Run.DONE）；`generate_test_points_from_files`（Step 2+3 一步链式入口） |

**Schema/DDL/Repository 升级**（`schema_version` 2 → 3）：
- `core/schemas/common.py`：新增 `GenerationScope(StrEnum)` = ITEM / CROSS_ITEM
- `core/schemas/testpoint.py`：TestPoint 新增 `generation_scope`（默认 ITEM）+ `fingerprint`（可选，Repository 入库前兜底计算）
- `core/v2/ddl.py`：`test_points` 表新增两列 + `UNIQUE(fingerprint)` 索引；`create_v2_schema()` 含 v2→v3 自动升级分支（ALTER TABLE 补列 + 已有行补算 fingerprint）
- `core/v2/repository.py`：`save_test_point` 按 fingerprint upsert（同 fingerprint 复用旧 ULID）；新增 `get_test_point_by_fingerprint` / `list_test_points_by_version` / `list_test_points_by_run`
- `core/v2/migrate_v1_to_v2.py`：V1 迁移过来的 TestPoint 显式设 `generation_scope=ITEM` + 补算 fingerprint

**测试**（全 mock，不依赖真实 API）：
- `tests/test_tp_generator.py`（17 例）：Phase A/B JSON 提取、异常处理、分批、prompt 内容验证
- `tests/test_tp_validator.py`（27 例）：硬性覆写、item_ids 归一、枚举兜底、去重、覆盖率、语义 warning
- `tests/test_tp_orchestrator.py`（13 例）：端到端、Run/Config 落库、幂等重跑、关联表、边界情况
- `tests/test_step3_acceptance.py`（15 例）：12 条主门槛 + 3 条附加（generation_scope / fingerprint / V1 零回归）

**12 条主验收门槛 + 3 条附加：全部 PASSED**
1. ✅ orchestrator 能生成 TestPoint[] 并全部通过 Pydantic 严格校验
2. ✅ 每个 TestPoint 的 `item_ids` 全部指向真实存在的 RequirementItem
3. ✅ Phase A 的 `item_ids` 严格等于 `[输入 item.id]`，LLM 乱写被代码覆写
4. ✅ Phase B 的 `len(item_ids) >= 2`；过滤非法 id 后 <2 的被丢弃
5. ✅ dimension/priority 非法值被代码兜底；`technique=None`、`obligation_id=None`、`provenance=LLM` 硬性覆写
6. ✅ 按 fingerprint 严格去重；跨 phase 不做语义合并（generation_scope 不同即业务身份不同）
7. ✅ items 过多时 Phase B 能按 module 分批 + 合并结果不重不漏
8. ✅ Run + GenerationConfig 正确落库；`run.status` 终态 DONE；`run.requirement_version_id` 指向输入 version
9. ✅ `test_point_items` 关联表 M:N 写入正确；删 TestPoint 不级联删 RequirementItem
10. ✅ 幂等：同 version 重复调用不产生孤儿/重复（fingerprint upsert 复用 ULID）
11. ✅ 覆盖率报告能列出未被任何 TestPoint 引用的 RequirementItem（软指标，不阻塞入库）
12. ✅ module 必须来自 IR items；subcategory 允许 LLM 语义归纳
附加 A. ✅ `generation_scope` 正确赋值（Phase A=item, Phase B=cross_item）
附加 B. ✅ `fingerprint` 非空、格式正确（`tp_` + 32 hex）、DB UNIQUE 约束生效
附加 C. ✅ V1 零回归（`web/data.py`、`core/generator.py`、`data.db` 不动）

**诚实边界**：门槛 12 "subcategory 不得引入 IR 中不存在的业务实体/业务规则" 靠 Prompt 约束，代码侧只校验非空；真实 LLM 是否越界需真实 API 抽查（mock 无法确定性验证）。覆盖率软指标只报告不阻塞，未覆盖的 item 由 Step 4 策略引擎硬兜底（例如 field_spec 有 min/max 但 Phase A 没生成 boundary 测试点，Step 4 会派生 obligation → strategy TestPoint）。

---

## 6. 期间修复的重要 bug（Step 1 潜伏）

**`INSERT OR REPLACE` + `ON DELETE CASCADE` 陷阱**：`INSERT OR REPLACE` = 先 DELETE 再 INSERT，DELETE 会级联删子表。Step 2 的 `build_requirement_ir` 重存 doc 更新 `latest_version_id` 时，会**级联删光该 doc 的所有 version→item**（若 Step 3 重存 run 更新状态，会删光其所有用例）。
**修复**：`core/v2/repository.py` 全部 11 个 save 改用 **upsert（`INSERT ... ON CONFLICT(id) DO UPDATE`）**，原地更新不删行不级联。
**回归锁定**：`TestUpsertNoCascade`（重存 doc/version/run 后子数据必须存活）。

---

## 7. 代码结构

```
core/schemas/    # Pydantic 唯一真源：common/requirement/testpoint/testcase/strategy/review/run/preference/reserved/__init__
core/v2/         # V2 持久层 + 领域服务（独立 data_v2.db，schema_version=3）
  db.py          #   连接管理（WAL/foreign_keys/写锁）
  ddl.py         #   建表 SQL + schema_version（含 v2→v3 自动升级分支）
  repository.py  #   Pydantic↔SQLite 映射（全部 upsert；TestPoint 按 fingerprint upsert）
  resolver.py    #   TargetResolver + ReferentialValidator（多态目标）
  fingerprint.py #   TestPoint 业务确定性指纹（Step 3 新增）
  migrate_v1_to_v2.py
  prompts.py ingestion.py parser.py validator.py ir.py                    # Step 2
  tp_prompts.py tp_generator.py tp_validator.py tp_orchestrator.py        # Step 3
docs/v2/         # 设计文档（step1-data-model.md + step3-testpoint-generator.md + 本文件）
tests/           # test_schemas/state_machine/v2_repository/v2_roundtrip/resolver/migration
                 # ir_*/step2_acceptance
                 # tp_generator/tp_validator/tp_orchestrator/step3_acceptance
```

## 8. 运行 / 验证命令（PowerShell）

```powershell
# venv 已就绪（若无：python -m venv .venv; .\.venv\Scripts\pip install -e ".[dev]"）
.\.venv\Scripts\python.exe -m pytest -q                    # 期望 345 passed
.\.venv\Scripts\python.exe -m ruff check .                 # 期望 All checks passed
.\.venv\Scripts\python.exe -m ruff format --check .        # 期望全部 formatted
.\.venv\Scripts\python.exe start.py -p 5000 --no-browser   # 启动 V1（V2 尚未接入前端）
```

## 9. 下一步 = Step 4「测试策略引擎」（未开始，需先讨论）

**预期范围**：基于 Step 2 IR 的 `FieldSpec / BusinessRule / PermissionRule`，用**代码**确定性推导 `CoverageObligation`（边界值/等价类/权限矩阵/决策表/状态迁移），再派生 `TestPoint(provenance=STRATEGY, technique=<对应技术>, obligation_id=<回链>)` 硬兜底覆盖率。与 Step 3 的 LLM 派生 TestPoint 合流写入同一张表，用 `provenance` 区分。

**开工前需与用户讨论确认的点**（沿用"先讨论→确认→实现"节奏）：
- 覆盖义务推导的**技术选型优先级**：boundary_value / equivalence_class / decision_table / state_transition / permission_matrix / error_guessing / scenario 中哪几类先做
- **CoverageObligation 与 TestPoint 的派生关系**：一个 obligation 派生一个 TestPoint，还是一个 obligation 派生多个（例如边界值 6 个点）
- **覆盖率硬指标**：Step 4 完成后是否要求 `obligation_coverage` 关系表达到 100%（Step 3 的软报告升级为硬约束）
- 是否复用 Step 3 的 `fingerprint` 幂等机制（strategy TestPoint 的 fingerprint 公式是否需含 `technique + obligation_id`）
- Step 3 未覆盖的 item 在 Step 4 如何**硬兜底**：例如 `field_spec` 有 min/max 但 Phase A 没生成 boundary TestPoint，Step 4 是否强制派生

## 10. 协作约定（重要）

- **每步先讨论方案 → 用户确认 → 再实现 → 验证 → 用户确认后再提交推送**；不要抢跑下一步。
- **未经用户明确要求不 `git commit`/push**。Git 约定：不直推 main（除非明确要求）；commit 带中文；推双远程 origin(github)+gitee，gitee 绕代理：`git -c http.proxy="" -c https.proxy="" push gitee main`。
- V1 运行时（`core/db.py`、`web/`、V1 表、`data.db`）**保持不动**；V2 全部新增、独立 `data_v2.db`。
- 完成里程碑后：跑全量 pytest + ruff 给真实证据，逐条核对验收标准，保持工作区稳定供 review。
