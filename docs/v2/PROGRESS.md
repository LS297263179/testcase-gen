# V2 蓝图与进度（交接文档）

> 本文件是 V2 重构的"单一进度真相源"。每完成一步请回来更新"进度总览"与对应 Step 段落。

## 新会话必读文档清单（按阅读顺序）

| 顺序 | 文档 | 行数 | 作用 | 何时读 |
|---|---|---|---|---|
| 1️⃣ | **`docs/v2/PROGRESS.md`**（本文件） | ~320 | 蓝图总览 + 进度总览 + 各 Step 详情 + 协作约定 | **每次新会话首先读** |
| 2️⃣ | `docs/v2/step1-data-model.md` | ~740 | Step 1 数据模型详细设计（Schema/DDL/实体关系/迁移铁律） | 要改 Schema/数据模型时读 |
| 3️⃣ | `docs/v2/step3-testpoint-generator.md` | ~283 | Step 3 测试点生成引擎设计（两阶段 LLM 派生 + fingerprint） | 要改测试点生成时读 |
| 4️⃣ | `docs/v2/step4-strategy-engine.md` | ~321 | Step 4 策略引擎设计（三策略代码派生 + 覆盖率双指标） | 要改策略引擎时读 |
| 5️⃣ | `docs/v2/step5-testcase-synthesizer.md` | ~396 | Step 5 用例合成设计（TestDataPlanner + 双指纹身份/内容分离 + 不双写覆盖） | 要改用例合成时读 |
| 6️⃣ | `docs/v2/step6-traceability-change-impact.md` | ~330 | Step 6 追溯链 + 变更影响分析设计（item 双 hash + 四态匹配 + 只读报告） | 要改追溯/变更影响时读 |
| 7️⃣ | `docs/v2/step7-ai-reviewer.md` | ~340 | Step 7 AI Reviewer 设计（6 维硬/软混合分工 + executability 加权 + coverage 双指标 + 证据锤定） | 要改评审时读 |

辅助参考：`AGENTS.md` / `CLAUDE.md`（项目速查 + Prompt 位置表）、`README.md`（面向用户的功能说明）。

---

## 0. 给新会话的上下文（一句话）

本项目 V1 = 基于 LLM 的 AI 测试工程平台（Flask + SQLite + 原生前端）。现按 **13 步蓝图**重构为 **V2**。
V2 的核心不是 `Prompt→LLM→Result`，而是 **"结构化数据 → 规则/策略 → LLM → 结构化数据 → Validator → Reviewer → 结构化数据"**：LLM 是大脑但不单独控制系统，测试的确定性关注点尽量代码化。

**当前进度（截至 2026-09）：Step 1~6 已完成并推送（三方同步至 `fcaa197`）；Step 7「AI Reviewer 6 维结构化评审」已实现并本地验证（752 passed / ruff 全绿 / schema_version=7，待用户确认后推送）；项目名已全局改为「AI 测试工程平台 / AI Test Engineering Platform」；下一步 = Step 8「去重体系（精确 + 语义双重去重）+ Optimizer」（未开始，需先讨论方案）。**

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
 ├─ Step 4：加入测试策略引擎（代码算边界/等价类/权限矩阵/覆盖义务） ✅ 完成
 ├─ Step 5：重构"测试点 → 测试用例"（LLM 合成 TestCase + steps/expected/precondition） ✅ 完成
 ├─ Step 6：建立 Traceability 追溯链（需求→测试点→用例）+ 变更影响分析 ✅ 完成
 ├─ Step 7：升级 AI Reviewer（6 维结构化评审 + Validator） ✅ 完成
 ├─ Step 8：升级去重体系（精确 + 语义双重去重） ← 下一步
 ├─ Step 9：加入人工编辑/确认闭环（TestCase 状态机 EDITED→RE_REVIEW）
 ├─ Step 10：Preference Learning（用户反馈→提示词/偏好优化）
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
| 3 测试点生成引擎 | ✅ 完成+验证(12+3门槛全过) | `core/v2/`{tp_prompts,tp_generator,tp_validator,tp_orchestrator,fingerprint} + Schema/DDL/Repo 升级(v2→v3) + 4 测试文件 | 已推 origin+gitee (e7ab544) |
| 4 测试策略引擎 | ✅ 完成+验证(12+3门槛全过) | `core/v2/strategy/`{boundary,equivalence,permission,engine,deriver,orchestrator} + Schema/DDL/Repo 升级(v3→v4) + 6 测试文件 | 已推 origin+gitee (9fb3842) |
| 5 测试点→测试用例 | ✅ 完成+验证(18门槛全过) | `core/v2/`{tc_prompts,test_data_planner,tc_generator,tc_validator,tc_orchestrator} + Schema/DDL/Repo 升级(v4→v5：TestCase 双指纹身份/内容分离 + DataPlan) + 5 测试文件 | 已推 origin+gitee (957a42c) |
| 6 追溯链+变更影响 | ✅ 完成+验证(13门槛全过) | `core/v2/`{traceability,change_impact} + Schema/DDL/Repo 升级(v5→v6：RequirementItem 双 hash identity/content + 3 枚举 + 2 追溯查询) + 3 测试文件 | 已推 origin+gitee (fcaa197) |
| 7 AI Reviewer 6维评审 | ✅ 完成+验证(14门槛全过) | `core/v2/`{review_prompts,review_hard,review_soft,review_orchestrator} + Schema/DDL/Repo 升级(v6→v7：ReviewReport 5 字段 + ReviewFinding.detail + CoverageDetail/ExecutabilityDetail + DuplicateLevel) + 4 测试文件 | 本地完成，待推送 |
| — 项目重命名 | ✅ 完成 | 全局改名「AI 测试工程平台 / AI Test Engineering Platform」（12 个版本库文件 + 本地 config.yaml/egg-info） | 已推 origin+gitee (8cc469c) |
| 8~13 | ⬜ 未开始 | — | — |

**测试基线**：V1 原有 126 例（零回归）+ V2 新增，**当前全量 752 passed**（Step 6 后 679 + Step 7 新增 73），ruff check/format 全绿。

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

## 5.6 Step 4 详情（测试策略引擎）✅

**目标**：消费 Step 2 IR 的 `FieldSpec / PermissionRule`，用**纯代码规则**（不调 LLM）确定性派生 `CoverageObligation` + `TestPoint(provenance=STRATEGY)`，硬兜底覆盖率。对应蓝图 `Strategy Engine`。

**首批三类技术**（用户拍板）：BOUNDARY_VALUE / EQUIVALENCE_CLASS / PERMISSION_MATRIX。DECISION_TABLE / STATE_TRANSITION / ERROR_GUESSING / SCENARIO 延后。

**派生关系**（用户拍板 1:N）：
- BOUNDARY_VALUE：1 obligation → 6 TestPoint（min-1/min/min+1/max-1/max/max+1）
- EQUIVALENCE_CLASS：1 obligation → N TestPoint（enum: N 合法+1 非法；pattern/required/nullable/unique/format: 各 2）
- PERMISSION_MATRIX：1 obligation → 1 TestPoint（1:1）

**Step 4 硬性约束**（用户补充，Validator 代码强制）：
- `provenance = STRATEGY`、`generation_scope = STRATEGY`、`technique != None`、`obligation_id != None`
- `module` 来源 `item.module`；`subcategory` 按 technique 命名（"边界值"/"等价类"/"权限矩阵"）
- **不生成真实测试数据**：等价类只产出抽象类标识（如 `class="invalid_pattern"`），具体数据由 Step 5 TestDataGenerator 生成
- min≤0 时的 min-1 点带 `warn="negative_value_may_be_invalid"` 标记（第一版简单规则）

**strategy_params + fingerprint**（用户补充）：
- TestPoint 新增 `strategy_params: dict` 字段（结构化参数，用于 fingerprint 区分同 obligation 下多个点）
- 示例：边界值 `{"boundary_type":"min_minus_1","value":0,"kind":"value"}`；等价类 `{"class":"valid_enum_value","value":"active"}`；权限 `{"role":"admin","resource":"order","action":"refund","allowed":true}`
- Step 4 fingerprint 公式：`sha256(version_id | "strategy" | obligation_id | technique | canonical(strategy_params))[:32]`
- **obligation 幂等**：按 natural key `(run_id, item_id, technique, target)` 复用旧 id → 下游 TestPoint fingerprint 稳定

**覆盖率双指标严格分离**（用户修订）：
- **Strategy Obligation Coverage**（Step 4 内部硬指标）= `obligation_coverage_ratio(run_id)` == 1.0
- **RequirementItem Coverage**（整体软指标）= 被至少一个 TestPoint（LLM 或 strategy）引用的 item / 全部 item
- 两者不混为一谈：纯功能类 item（无 fields/permissions）Step 4 无法派生 obligation，但可能被 Step 3 LLM 覆盖

**交付文件**（`core/v2/strategy/` 包）：
| 文件 | 职责 |
|---|---|
| `boundary.py` | 边界值策略：六点派生 + strategy_params + min≤0 合理性提示 |
| `equivalence.py` | 等价类策略：6 种字段属性 → 抽象类标识（不含真实数据） |
| `permission.py` | 权限矩阵策略：PermissionRule → 1:1 派生 |
| `engine.py` | 三策略汇总 `derive_obligations(items, run_id)` |
| `deriver.py` | obligation → TestPoint 分派 + fingerprint 统一计算 + 去重 |
| `orchestrator.py` | `apply_strategy_engine`（Run 状态机 + 持久化 + add_coverage + 双指标）+ `generate_test_points_full`（Step 3+4 一站式） |
| `fingerprint.py`（补） | `compute_strategy_testpoint_fingerprint` + `canonical_strategy_params` |

**Schema/DDL/Repository 升级**（`schema_version` 3 → 4）：
- `core/schemas/common.py`：`GenerationScope` 新增 `STRATEGY = "strategy"`
- `core/schemas/testpoint.py`：TestPoint 新增 `strategy_params: dict | None`
- `core/v2/ddl.py`：`test_points` 加 `strategy_params_json` 列 + v3→v4 自动升级分支
- `core/v2/repository.py`：`save_test_point` fingerprint 兜底区分 LLM/STRATEGY 两种公式；序列化 `strategy_params`；新增 `get_obligation_by_natural_key`

**测试**（纯代码，不调 LLM）：
- `tests/test_strategy_boundary.py`（42 例）、`test_strategy_equivalence.py`（29 例）、`test_strategy_permission.py`（15 例）
- `tests/test_tp_strategy_deriver.py`（18 例）、`test_strategy_orchestrator.py`（16 例）、`test_step4_acceptance.py`（15 例）

**12 条主验收门槛 + 3 条附加：全部 PASSED**
1. ✅ min_value+max_value → BOUNDARY_VALUE obligation + 6 TestPoint
2. ✅ min_length+max_length → BOUNDARY_VALUE obligation + 6 TestPoint
3. ✅ enum_values → EQUIVALENCE_CLASS obligation + N+1 TestPoint
4. ✅ required=True → EQUIVALENCE_CLASS obligation（必填 + 空值非法）
5. ✅ pattern 非空 → EQUIVALENCE_CLASS obligation（合法匹配 + 非法不匹配，抽象类标识）
6. ✅ PermissionRule → PERMISSION_MATRIX obligation + 1:1 TestPoint
7. ✅ Strategy TestPoint 硬性约束（provenance/scope/technique/obligation_id）
8. ✅ Strategy Obligation Coverage == 1.0（硬指标）
9. ✅ fingerprint 幂等（natural key 对齐 obligation.id → TestPoint fingerprint 稳定）
10. ✅ Step 3+4 合流后 generation_scope 三值齐全（item/cross_item/strategy）
11. ✅ Run 状态机 STRATEGIZING→DONE + counts 累加
12. ✅ V1 零回归 + schema_version=4
附加 A. ✅ fingerprint 公式验证（canonical_strategy_params 确定性）
附加 B. ✅ 覆盖率双指标严格分离（硬 1.0 ≠ 软 0.5）

**诚实边界**：边界值的 float precision 处理、复杂正则的精确样例生成、permission condition 的规则化验证均留待迭代（首批采用简单规则）。纯功能类 item（无 fields/permissions）Step 4 无法派生 obligation，依赖 Step 3 LLM 覆盖（软指标报告里会列出）。**★覆盖语义是「结构性」而非「语义性」**：`Strategy Obligation Coverage == 1.0` 仅保证每个 obligation 都派生了 TestPoint 并登记回链（派生即登记，无语义验证环节），不保证 TestPoint 在语义上真正满足 obligation 的测试设计意图——后者是 Step 5（TestCase 合成检验可执行性）与 Step 7（AI Reviewer coverage 维度）的职责，Step 4 有意到此为止（详见 `step4-strategy-engine.md` §5.4）。

---

## 5.7 Step 5 详情（测试点 → 测试用例）✅

**设计文档**：`docs/v2/step5-testcase-synthesizer.md`（含 7 核心原则 + 架构 + 18 门槛 + ADR）。

**目标**：消费 Step 3+4 合流后的 `TestPoint[]`（LLM + STRATEGY 两种 provenance），1:1 合成可执行的 `TestCase`（含 `steps: list[TestStep]` / `expected` / `precondition`）。对应蓝图 `Test Case Generator`。

**7 条核心原则（用户冻结）**：
1. **1 TestPoint → 1 TestCase**（无论 LLM 还是 STRATEGY）
2. **TestDataGenerator：Code-first + LLM fallback**（数据来源优先级 example→default→strategy_params→enum→builtin→llm）
3. **TestStep：LLM generation + Code validation**
4. **fingerprint：基于 TestPoint 来源身份**（version_id | generation_mode | sorted(test_point_ids)），不依赖 title/steps 等可编辑内容；**身份指纹 fingerprint 与内容哈希 content_hash 分离**
5. **obligation coverage：Step 4 的 obligation_coverage 关系表是唯一事实源**，TestCase 通过 TestPoint 间接继承，**不双写**
6. **Validator：先修复可修复项，修不了再 VALIDATION_FAILED + 保存 validation_errors**
7. **V1 运行时完全不修改**

**架构数据流**：`TestPoint[] → TestDataPlanner（Code Generator + LLM Fallback，含 re.fullmatch 验证）→ DataPlan[] → TestCase Synthesizer（strategy 走代码模板 / LLM 走 LLM 合成）→ Validator（修复/判定）→ fingerprint+content_hash → SQLite`。

**generation_mode 判定**（参与 fingerprint 身份）：strategy TP + 纯代码数据 + 模板 steps → `CODE`；strategy TP + LLM 兜底数据（复杂正则）→ `HYBRID`；LLM TP + LLM steps → `LLM`。

**★ 关键设计（用户反馈修订）**：
- **双指纹分离**：`fingerprint`（身份，不含 title/steps）保证人工修改标题后仍是同一 TestCase（Step 9 Revision 基础）；`content_hash`（内容）追踪内容变化。公式见 `core/v2/fingerprint.py`。
- **obligation coverage 不双写**：查询路径 `TestCase → test_case_points → TestPoint.obligation_id → CoverageObligation`（间接继承）；需直接查询时用 SQL View（派生，非事实源），Step 5 暂未建 View。
- **DataPlan 中间层持久化**：`TestPoint → DataPlan → TestCase`（非直接改 TestCase），DataPlan 存 `TestCase.data_plan`，便于审计 + 未来 Playwright/API 复用。
- **LLM 复杂正则数据必过代码验证**：LLM 生成样例 → `re.fullmatch` 验证 → 不符合预期 retry（≤3 次）/ fail（记 validation_errors）。LLM 不能自证正确。
- **迁移用例 fingerprint 留 NULL**：V1 迁移用例无 test_point_ids，若强算会 UNIQUE 碰撞坦缩，故 `save_test_case` 仅在 test_point_ids 非空时算 fingerprint（SQLite UNIQUE 允许多 NULL）。

**交付文件**（全在 `core/v2/`）：
| 文件 | 职责 |
|---|---|
| `tc_prompts.py` | 用例合成 Prompt（`test-case-synthesizer-v1`）+ 复杂正则数据 Prompt（`test-data-pattern-v1`） |
| `test_data_planner.py` | TestDataPlanner：数据来源优先级 6 级 + Code Generator（boundary/equivalence/permission）+ LLM Fallback（复杂正则 + re.fullmatch 验证）→ DataPlan[] |
| `tc_generator.py` | strategy 代码模板合成（`synthesize_strategy_template`）+ LLM 合成（`synthesize_with_llm`，复用 `parser.extract_json`） |
| `tc_validator.py` | 可修复项修正（seq 重排/空 step 丢弃/枚举兜底/module 覆写）+ 不可修复判定 + fingerprint/content_hash + validation_errors + 去重 |
| `tc_orchestrator.py` | `synthesize_test_cases`（复用 Run，GENERATING→DONE）+ `generate_test_cases_full`（Step 3+4+5 一站式） |

**Schema/DDL/Repository 升级**（`schema_version` 4 → 5）：
- `core/schemas/common.py`：新增 `GenerationMode(StrEnum)` = CODE / LLM / HYBRID
- `core/schemas/testcase.py`：新增 `DataPlanItem`（field/strategy/value/source/generator/expected_valid）+ TestCase 4 字段（generation_mode / content_hash / validation_errors / data_plan）
- `core/v2/fingerprint.py`：新增 `compute_testcase_fingerprint`（身份）+ `compute_testcase_content_hash`（内容）
- `core/v2/ddl.py`：`test_cases` 加 4 列 + `idx_cases_fp` 升级为 `UNIQUE(fingerprint)` + v4→v5 自动升级分支（补列 + 补算 content_hash/fingerprint + 索引升级）
- `core/v2/repository.py`：`save_test_case` 改 fingerprint 幂等 upsert（复用旧 ULID，仅 test_point_ids 非空时算）+ 序列化新字段 + 新增 `get_test_case_by_fingerprint` / `list_test_cases_by_version`

**测试**（全 mock，不调真实 API）：`test_test_data_planner.py`(44) + `test_tc_generator.py`(18) + `test_tc_validator.py`(31) + `test_tc_orchestrator.py`(21) + `test_step5_acceptance.py`(21) = **135 例**。

**18 条验收门槛：全部 PASSED**（1 test_point_ids 真实 / 2 1:1 派生 / 3 steps 良构 / 4 type 枚举兜底 / 5 fingerprint 格式+UNIQUE / 6 幂等 / 7 Run 复用+DONE / 8 状态机 VALIDATED·VALIDATION_FAILED / 9 关联表+不级联删 TP / 10 数据来源优先级+LLM 兜底 / 11 V1 零回归+schema=5 / 12 一站式入口 / 13 数据满足 FieldSpec 约束 / 14 复杂正则 re.fullmatch 验证 / 15 同 TP 同 Version 唯一 TC / 16 改 title·steps 不改身份指纹 / 17 TC→TP→obligation 反查 / 18 VALIDATION_FAILED 保存 validation_errors）。

**诚实边界**：LLM 合成的 steps 质量（可执行性/业务贴合度）靠 Prompt 约束，需真实 API 抽查；复杂正则 LLM 兜底样例多样性 mock 无法确定性验证；content_hash 的实际用途在 Step 9 Revision 才真正消费（Step 5 只计算并持久化）。纯代码模板合成的 strategy 用例文案较机械，可读性优化留待 Step 7 Reviewer / Step 9 人工编辑。

---

## 5.8 Step 6 详情（Traceability 追溯链 + 变更影响分析）✅

**设计文档**：`docs/v2/step6-traceability-change-impact.md`（含 6 核心原则 + 匹配算法 + 13 门槛 + ADR）。

**目标**：基于 Step 1~5 已落地的追溯链，建立正/反向追溯查询 + 需求变更影响分析（diff 两个 RequirementVersion → 定位受影响 TP/TC）。对应蓝图 `Traceability`。

**6 条核心原则（用户冻结）**：
1. **纯只读**：只产出 `ChangeImpactReport`（内存 dataclass，不持久化），绝不改实体状态/重生成资产
2. **RequirementItem 双 hash**（对齐 Step 5 TestCase）：`fingerprint`（identity = sha256(module|type|normalize(statement))，**不含 doc_id**）+ `content_hash`（= statement+fields+rules+permissions+acceptance）
3. **匹配逻辑**：identity 同→比 content_hash（同=UNCHANGED/异=MODIFIED）；identity 异→相似度兜底（ratio≥阈值=MODIFIED，否则 ADDED/DELETED）
4. **职责边界**：Step 6 发现影响 / Step 7 AI Review / Step 9 人工确认，不碰状态机/不重生成
5. **影响清单可解释**：每条变更带 affected_reason + impact_level + recommended_actions
6. **V1 运行时零修改**

**★ 核心难点解决（用户反馈）**：statement 未改但 FieldSpec 改了（年龄 18~60→18~65）——identity fingerprint 不变但 content_hash 变 → 判 MODIFIED。content_hash 分量比对定位 affected_reason（FIELD_CONSTRAINT_CHANGED / BUSINESS_RULE_CHANGED / PERMISSION_CHANGED / ACCEPTANCE_CHANGED）。

**为何 identity 不含 doc_id**：doc_id 是文档实体身份，非 item 逻辑身份本体；同 doc 多 Version 共用 doc_id（匹配只在同 doc 两版本间），需区分不同文档时在查询范围限定 doc_id。

**交付文件**（`core/v2/`）：
| 文件 | 职责 |
|---|---|
| `traceability.py` | 正向 `trace_forward_from_item`（item→TP→TC→obligation）+ 反向 `trace_backward_from_case`（TC→TP→item→version→doc→source_ref）；复用 resolver.TargetResolver |
| `change_impact.py` | `ItemMatcher`（identity+content+相似度）+ `ImpactResolver`（追溯+reason+level）+ `analyze_change_impact(v_old, v_new) → ChangeImpactReport` |

**Schema/DDL/Repository 升级**（`schema_version` 5 → 6）：
- `core/schemas/common.py`：新增 `ChangeType` / `AffectedReason` / `ImpactLevel` 三枚举
- `core/schemas/requirement.py`：RequirementItem 新增 `fingerprint`（identity）+ `content_hash`（内容）
- `core/v2/fingerprint.py`：新增 `compute_item_identity_fingerprint` + `compute_item_content_hash` + `_canonical_list`
- `core/v2/ddl.py`：`requirement_items` 加 2 列 + **普通索引** `idx_items_fingerprint`（★ 非 UNIQUE，同 doc 跨版本 item fingerprint 相同）+ v5→v6 升级分支
- `core/v2/repository.py`：`save_item` 兜底计算两 hash + `_row_to_item` 反序列化 + 新增 `list_test_points_by_item` / `list_test_cases_by_test_point`

**测试**（全代码，不调 LLM）：`test_traceability.py`(11) + `test_change_impact.py`(19) + `test_step6_acceptance.py`(13) = **43 例**（+ Step1 层回归补断言 4 例）。

**13 条验收门槛：全部 PASSED**（1 旧版本不被修改 / 2 四态识别 / 3 FieldSpec 隐性变更可发现 / 4 追溯影响不漏 / 5 只读无状态变 / 6 identity 不含 doc_id / 7 跨 doc 不可比 / 8 相似度兜底 / 9 affected_reason 细分 / 10 impact_level 代码化 / 11 content 分量比对 / 12 schema=6+索引非UNIQUE / 13 V1 零回归）。

**诚实边界**：相似度阈值（0.6）为启发式，边界 case 可能误判（可配）；impact_level 首批代码规则，更细语义判断留 Step 7 AI；ChangeImpactReport 不持久化（返回值），历史留存/前端可视化留 Step 13；ADDED item 仅建议“需新增覆盖”，不自动触发 Step 3/4/5。

---

## 5.9 Step 7 详情（AI Reviewer 6 维结构化评审）✅

**设计文档**：`docs/v2/step7-ai-reviewer.md`（含 9 核心原则 + 硬/软分工 + 14 门槛 + ADR）。

**目标**：消费 Step 5 的 TestCase[]（status=VALIDATED），产出结构化多轮 `ReviewReport`（6 维 `ReviewScores` + `ReviewFinding`）。硬指标由 Validator 产 `provenance=validator` 的 finding；语义/遗漏由 LLM 产 `provenance=llm` 的 finding。对应蓝图 `AI Reviewer(6维)`。

**9 条核心原则（用户冻结）**：
1. 混合分工：确定性维度代码硬算，语义维度 LLM 软判
2. **executability 双子指标加权**：`structural(Code)×0.4 + semantic(LLM)×0.6`（非简单平均；steps 非空只证形式，不证可执行）
3. **coverage 双指标不合成**：Report 保留 strategy_obligation_coverage + requirement_item_coverage + uncovered_item_ids
4. **duplication 分两级**：EXACT（content_hash 精确）/ SEMANTIC（相似度+similarity）；只报告+auto_fixable，绝不 merge/delete（Step 8）
5. **REVIEWED ≠ 质量合格**：仅代表“评审完成”，低分+多 findings 照样转 REVIEWED；不新增 TestCase 状态
6. **评分可解释**：LLM soft score 必带 score+reason+findings
7. **LLM Review 引用证据**：finding.target 指向真实 TestCase/RequirementItem（ReferentialValidator 校验），missing_risk 引用 item/rule（接 source_ref）
8. **只发现不修复**：发现问题→记 finding→标 auto_fixable→停止（Optimizer 是 Step 8，绝不偷塞）
9. V1 运行时（core/reviewer.py 自由文本评审）零修改

**架构数据流**：`TestCase[] → Validator → {Hard Review Engine（coverage/duplication/consistency/executability结构）+ LLM Soft Review Engine（accuracy/missing_risk/executability语义）} → ReviewScores(6维)+明细+findings → ReviewReport → TestCase VALIDATED→REVIEWED → auto_fixable 仅标记 → Step 8`。

**交付文件**（`core/v2/`）：
| 文件 | 职责 |
|---|---|
| `review_prompts.py` | 结构化评审 Prompt（`test-case-reviewer-v1`）：只评 3 软维度 + 强制 reason + 证据锤点 |
| `review_hard.py` | Hard Review Engine：coverage 双指标 + duplication 两级 + consistency + executability 结构层 → 分数 + validator findings（纯代码确定性） |
| `review_soft.py` | LLM Soft Review Engine：软维度 score+reason+findings 解析 + 证据锤定（target_ref 支持 ULID/display_id）+ ReferentialValidator 校验 + 非法兜底 |
| `review_orchestrator.py` | `review_test_cases`：Hard+Soft 合成 ReviewScores（executability 加权）+ ReviewReport 持久化 + TestCase→REVIEWED + Run 状态机 |

**Schema/DDL/Repository 升级**（`schema_version` 6 → 7）：
- `core/schemas/common.py`：新增 `DuplicateLevel(StrEnum)` = EXACT / SEMANTIC
- `core/schemas/review.py`：新增 `CoverageDetail`（双指标+uncovered）/ `ExecutabilityDetail`（structural/semantic+权重）；ReviewFinding 加 `detail`；ReviewReport 加 review_target_type/review_target_ids/coverage_detail/executability_detail/dimension_reasons（ReviewScores 6 维不变）
- `core/v2/ddl.py`：review_reports 加 5 列 + review_findings 加 detail_json + v6→v7 升级分支
- `core/v2/repository.py`：save/get/list_review_report 序列化新字段 + finding.detail + 新增 `get_latest_review_report`

**测试**（全 mock）：`test_review_hard.py`(17) + `test_review_soft.py`(19) + `test_review_orchestrator.py`(19) + `test_step7_acceptance.py`(15) = **70 例**（+ Step1 层回归补 3 例）。

**14 条验收门槛：全部 PASSED**（1 报告持久化+6维 / 2 coverage 双指标 / 3 executability 加权 / 4 duplication 两级+不删 / 5 provenance 可区分 / 6 soft score 带 reason / 7 missing_risk 证据引用 / 8 非法 target 丢弃 / 9 REVIEWED≠合格低分也转 / 10 review_target 填充 / 11 只标记不修复 / 12 多次Review硬指标稳定软分波动 / 13 schema=7+新列 / 14 V1 零回归）。

**诚实边界**：LLM 软分波动（mock 只验证硬指标确定性稳定 + LLM 输出被正确解析/校验/证据锤定，不验证真实打分质量）；executability 0.4/0.6 与 overall 均值权重为经验值，留 Step 12 Benchmark 调；review_target 预留字段当前仅 TESTCASE；review_result（PASS/NEEDS_OPTIMIZATION/CRITICAL）不实现（用户明确现在不加状态）；消费 ChangeImpactReport 的“变更后加权复审”首版不做（留接口）。

---

## 6. 期间修复的重要 bug（Step 1 潜伏）

**`INSERT OR REPLACE` + `ON DELETE CASCADE` 陷阱**：`INSERT OR REPLACE` = 先 DELETE 再 INSERT，DELETE 会级联删子表。Step 2 的 `build_requirement_ir` 重存 doc 更新 `latest_version_id` 时，会**级联删光该 doc 的所有 version→item**（若 Step 3 重存 run 更新状态，会删光其所有用例）。
**修复**：`core/v2/repository.py` 全部 11 个 save 改用 **upsert（`INSERT ... ON CONFLICT(id) DO UPDATE`）**，原地更新不删行不级联。
**回归锁定**：`TestUpsertNoCascade`（重存 doc/version/run 后子数据必须存活）。

---

## 7. 代码结构

```
core/schemas/    # Pydantic 唯一真源：common/requirement/testpoint/testcase/strategy/review/run/preference/reserved/__init__
core/v2/         # V2 持久层 + 领域服务（独立 data_v2.db，schema_version=7）
  db.py          #   连接管理（WAL/foreign_keys/写锁）
  ddl.py         #   建表 SQL + schema_version（含 v2→v3→v4→v5→v6→v7 自动升级分支）
  repository.py  #   Pydantic↔SQLite 映射（全部 upsert；TestPoint/TestCase 按 fingerprint upsert；obligation 按 natural key 对齐）
  resolver.py    #   TargetResolver + ReferentialValidator（多态目标）
  fingerprint.py #   业务确定性指纹（Step3 LLM + Step4 strategy + Step5 TestCase 身份/内容 + Step6 Item identity/content）
  migrate_v1_to_v2.py
  prompts.py ingestion.py parser.py validator.py ir.py                    # Step 2
  tp_prompts.py tp_generator.py tp_validator.py tp_orchestrator.py        # Step 3
  strategy/      # Step 4 策略引擎包
    boundary.py equivalence.py permission.py                              #   三策略
    engine.py deriver.py orchestrator.py                                  #   汇总/派生/编排
  tc_prompts.py test_data_planner.py tc_generator.py tc_validator.py tc_orchestrator.py  # Step 5
  traceability.py change_impact.py                                       # Step 6 追溯链 + 变更影响分析
  review_prompts.py review_hard.py review_soft.py review_orchestrator.py  # Step 7 AI Reviewer（硬/软混合）
docs/v2/         # 设计文档（step1-data-model.md + step3/step4/step5/step6/step7 + 本文件）
tests/           # test_schemas/state_machine/v2_repository/v2_roundtrip/resolver/migration
                 # ir_*/step2_acceptance
                 # tp_generator/tp_validator/tp_orchestrator/step3_acceptance
                 # strategy_boundary/strategy_equivalence/strategy_permission
                 # tp_strategy_deriver/strategy_orchestrator/step4_acceptance
                 # test_data_planner/tc_generator/tc_validator/tc_orchestrator/step5_acceptance
                 # traceability/change_impact/step6_acceptance
                 # review_hard/review_soft/review_orchestrator/step7_acceptance
```

## 8. 运行 / 验证命令（PowerShell）

```powershell
# venv 已就绪（若无：python -m venv .venv; .\.venv\Scripts\pip install -e ".[dev]"）
.\.venv\Scripts\python.exe -m pytest -q                    # 期望 752 passed
.\.venv\Scripts\python.exe -m ruff check .                 # 期望 All checks passed
.\.venv\Scripts\python.exe -m ruff format --check .        # 期望全部 formatted
.\.venv\Scripts\python.exe start.py -p 5000 --no-browser   # 启动 V1（V2 尚未接入前端）
```

## 9. 下一步 = Step 8「去重体系（精确 + 语义）+ Optimizer」（未开始，需先讨论）

**预期范围**：消费 Step 7 的 `ReviewReport`（尤其 duplication findings 与 auto_fixable 标记），实现精确去重（fingerprint/content_hash）+ 语义去重（相似度），以及 Optimizer（修复可自动修复的 finding）。对应蓝图 `Optimizer(自动优化/去重)`。

**已具备的基础**（Step 1~7 已落地）：
- Step 7 duplication findings 已分两级（EXACT content_hash / SEMANTIC similarity）+ auto_fixable=true + detail.counterpart_id
- TestCase 双指纹（fingerprint 身份 / content_hash 内容）为精确去重提供键
- ReviewReport 多轮 revision + trigger_type（after_optimizer 已预留）
- V1 `core/generator.py` 有 deduplicate/deduplicate_by_steps（Jaccard）可参考（但不改 V1）

**开工前需与用户讨论确认的点**（沿用“先讨论→确认→实现”节奏）：
- **去重策略**：精确（content_hash）直接删 vs 语义（相似度）保留哪条（评分高的/步骤全的）
- **Optimizer 范围**：仅去重，还是也修复其他 auto_fixable finding（格式/命名）+ 补充 missing_risk 遗漏用例
- **去重/优化后的状态与审计**：被删用例如何处理（ARCHIVED？保留痕迹）；优化后是否触发 trigger_type=after_optimizer 的重评审（Step 7 revision+1）
- **幂等与可回滚**：Optimizer 多次运行结果一致；是否保留优化前快照（TestCaseRevision 预留）
- **与 Step 9 人工确认的边界**：Optimizer 自动处理 vs 交人工确认的分工

## 10. 协作约定（重要）

- **每步先讨论方案 → 用户确认 → 再实现 → 验证 → 用户确认后再提交推送**；不要抢跑下一步。
- **未经用户明确要求不 `git commit`/push**。Git 约定：不直推 main（除非明确要求）；commit 带中文；推双远程 origin(github)+gitee，gitee 绕代理：`git -c http.proxy="" -c https.proxy="" push gitee main`。
- V1 运行时（`core/db.py`、`web/`、V1 表、`data.db`）**保持不动**；V2 全部新增、独立 `data_v2.db`。
- 完成里程碑后：跑全量 pytest + ruff 给真实证据，逐条核对验收标准，保持工作区稳定供 review。
