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
| 8️⃣ | `docs/v2/step8-dedup-optimizer.md` | ~271 | Step 8 Dedup Optimizer 设计（canonicalize pairs + survivor 优先级 + 双边状态检查 + 有条件重评审） | 要改去重/优化时读 |
| 9️⃣ | `docs/v2/step9-human-edit.md` | ~283 | Step 9 Human Editor 设计（编辑白名单 + Revision 快照 + changed_fields + 乐观锁 + Validator + AFTER_HUMAN_EDIT 重评审） | 要改人工编辑时读 |
| 🔟 | `docs/v2/v2-ui-react-refactor.md` | — | V2 前端 React+TS+Vite 重构设计（Run-centric IA + 退役清单） | 要改 V2 前端时读 |
| 1️⃣1️⃣ | `docs/v2/step11-benchmark-evaluation.md` | ~575 | **Step 11 冻结设计 v1.0**（架构边界 18 条 / Gold 独立性 / 四态 Matching / Metrics 手册 / 双轨评价 / Reliability 五态 / bench-v0.1 数据契约）+ **附录 B：S6 Runner 实施说明**（源 3 阶段门控 / S6 冻结决策 / 指纹与落盘安全）+ **附录 C：S7 三轨关联口径**（AUTO_HIT 重定义 / identity 仅诊断 / bridge·anchor 准入六条 / D10 不猜 item_id / 双模型对照证据 / 与 S3 差异清单 / 挂账） | 要动 Benchmark/评价层（S7~S8）前**必读** |

辅助参考：`AGENTS.md` / `CLAUDE.md`（项目速查 + Prompt 位置表）、`README.md`（面向用户的功能说明）。

---

## 0. 给新会话的上下文（一句话）

本项目 V1 = 基于 LLM 的 AI 测试工程平台（Flask + SQLite + 原生前端）。现按 **13 步蓝图**重构为 **V2**。
V2 的核心不是 `Prompt→LLM→Result`，而是 **"结构化数据 → 规则/策略 → LLM → 结构化数据 → Validator → Reviewer → 结构化数据"**：LLM 是大脑但不单独控制系统，测试的确定性关注点尽量代码化。

**当前进度（截至 2026-09-21）：Step 1~10 全部完成并推送（含 `cdb325c` V2 产品级 UI/UX 重构——React+TS+Vite `frontend-v2/`，产物 `static/v2/v2_react.js`+`v2_react.css`，Run-centric IA，只读端点 requirements 13→14，旧 `v2_app.js`/`v2_style.css` 退役，详见 `docs/v2/v2-ui-react-refactor.md`——与 `d1c5345` V2 Product Polish）。中后期路线评审已由用户拍板：Step 11 = Quality Evaluation Foundation + Benchmark Foundation，冻结设计见 `docs/v2/step11-benchmark-evaluation.md`。Step 11 已完成 S1~S6：设计冻结 + eval schema/loader + 确定性评价引擎 matching/metrics_hard + **bench-v0.1 正式数据集**（10 case / 10 requirement / 83 人工独立 Gold / manifest）+ S5 语义评价层 + **S6 Benchmark Runner**（`scripts/v2_benchmark.py` 独立进程 + 独立 benchmark DB + 五态启发式分类 + 环境指纹 + runset 快照）+ **S7 三轨关联改造**（架构裁决 D1~D10/D13：`rails.py` identity 诊断 > obligation bridge > scenario anchor，`AUTO_HIT` 重定义为"确定性关联命中"并带 `via`，新增 `identity_match_rate`/`via_counts`/`identity_diagnostics` 报告面，`metrics_soft.py` 零改动；bc_01 Gold → `gold-v0.2`；另含 D7② V1 空配置守卫与 D6 `config.yaml` baseline 模型同步 `qwen3.8-flash`）。S1~S4 已推（`0cb4bf5`）、S5 已推（`7b59bfe`）、S6 已推（`f72db13`）、S7 本地待推。全量 **1276 passed + 3 skipped**，ruff 双绿，schema_version=10，V1 除 D7② 4 行守卫外零改动。下一步 = S7 正式 baseline-v0.1（10 case 全量 runset）+ S8 Compare（均待用户指令，见 §11）。**

**★ Step 9 后真实项目状态审计结论（Step 10 的由来）**：V2 后端代码完整但**完全未产品化**——`data/data_v2.db` 是空库（Tables: []，从未运行 create_v2_schema）、`web/` 目录 0 处引用 `core.v2`（前端纯 V1）、无 CLI 入口、无顶层 Runtime 串联 Step 2→8、V2 LLM 从未真实调用（875 tests 全 mock/临时 DB）。因此 Step 10 不新增 AI 功能，专注把已有能力变成用户可真实运行的产品链路。

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

> ★ **2026-09 蓝图调整**：Step 9 完成后的真实项目状态审计发现 V2 完全未产品化（空库/无 Web/无 CLI/无 Runtime/LLM 从未真实调用），因此原 Step 10~13 重新排序：**优先做 Runtime + Productization**，Preference Learning 顺延至 Step 13。Step 11~13 的最终顺序以 Step 10 完成后的真实运行数据路线评审为准（不冻结）。

```
现有 V1
 ├─ Step 1：冻结数据模型 / Schema          ✅ 完成
 ├─ Step 2：建立 Requirement IR             ✅ 完成
 ├─ Step 3：重构"需求 → 测试点"             ✅ 完成
 ├─ Step 4：加入测试策略引擎（代码算边界/等价类/权限矩阵/覆盖义务） ✅ 完成
 ├─ Step 5：重构"测试点 → 测试用例"（LLM 合成 TestCase + steps/expected/precondition） ✅ 完成
 ├─ Step 6：建立 Traceability 追溯链（需求→测试点→用例）+ 变更影响分析 ✅ 完成
 ├─ Step 7：升级 AI Reviewer（6 维结构化评审 + Validator） ✅ 完成
 ├─ Step 8：升级去重体系（精确 + 语义双重去重）+ Optimizer ✅ 完成
 ├─ Step 9：加入人工编辑/确认闭环（Revision 快照 + 乐观锁 + Validator + Re-review） ✅ 完成
 ├─ Step 10：V2 Runtime + Productization（DB 真初始化/顶层 Runtime/Web API/前端接线/真实 LLM Run/E2E 验证/运行数据） ✅ 完成
 ├─ Step 11：Quality Evaluation + Benchmark Foundation（原 Step 11+12 合并；★路线评审已拍板，设计冻结 v1.0）
 │          S1 设计冻结 ✅ / S2 Schema+Loader ✅ / S3 确定性评价引擎 ✅ / S4 bench-v0.1 数据集 ✅
 │          S5 ✅ / S6 Runner ✅ / S7 三轨关联改造 ✅（baseline-v0.1 全量 runset 待跑）/ S8（Compare）⬜ 未开始
 ├─ Step 12：（并入 Step 11 的 Benchmark 轨道；剩余候选主题待 S8 后再评审）
 └─ Step 13：Preference Learning（用户反馈→提示词/偏好优化；数据源 = Step 9 changed_fields）（待路线评审）

远期（V2 稳定后）：AI 自动执行 → Playwright/API → 失败分析 → 自动修复 → AI Testing Agent
```

**目标数据流（蓝图）**：
```
用户需求(PDF/Word/图片/MD/Text) → Ingestion(解析/图片理解) → Requirement Parser
  → Requirement IR → { Test Point Generator + Strategy Engine } → Test Case Generator
  → AI Reviewer(6维) → Optimizer(自动优化/去重) → Human Review(人工确认/编辑)
  → Traceability → Preference Learning → Excel/Markdown/JSON
```

**Step 10 完成后的真实用户流程（阶段分离，用户冻结）**：
```
阶段 A（Runtime 自动，一次触发）：输入需求 → Step 2 IR → Step 3+4 TestPoints → Step 5 TestCases → Step 7 Review → Step 8 Optimizer → DONE
阶段 B（用户后续主动操作）：查看结果 → 人工编辑 TestCase（Step 9）→ 主动触发 Re-review
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
| 7 AI Reviewer 6维评审 | ✅ 完成+验证(14门槛全过) | `core/v2/`{review_prompts,review_hard,review_soft,review_orchestrator} + Schema/DDL/Repo 升级(v6→v7：ReviewReport 5 字段 + ReviewFinding.detail + CoverageDetail/ExecutabilityDetail + DuplicateLevel) + 4 测试文件 | 已推 origin+gitee (cb68f01) |
| 8 去重体系(Dedup Optimizer) | ✅ 完成+验证(14门槛全过) | `core/v2/`{optimizer,optimizer_orchestrator} + Schema/DDL 升级(v7→v8：状态机放开 REVIEWED→ARCHIVED) + 3 测试文件 | 已推 origin+gitee (909d6dd) |
| 9 人工编辑/确认闭环 | ✅ 完成+验证(19门槛全过) | `core/v2/`{human_editor,human_editor_orchestrator} + Schema/DDL/Repo 升级(v8→v9：TestCaseRevision 激活 + 乐观锁 + 状态机放开 VALIDATION_FAILED→EDITED) + 3 测试文件 | 已推 origin+gitee (d9edbbb) |
| — 项目重命名 | ✅ 完成 | 全局改名「AI 测试工程平台 / AI Test Engineering Platform」（12 个版本库文件 + 本地 config.yaml/egg-info） | 已推 origin+gitee (8cc469c) |
| 10 V2 Runtime+Productization | ✅ 完成（10.1~10.7 全部完成；10.1~10.6 已推送，10.7 待推送） | 7 子步骤，拆分见下 + 详见 §9 | — |
| ├ 10.1 V2 DB 真初始化 | ✅ 完成+推送 | `core/v2/bootstrap.py`(ensure_v2_ready) + `web/__init__.py` 启动接线 + V2_READY 状态 + 14 测试 | `e660ead` |
| ├ 10.2 顶层 Runtime | ✅ 完成+推送 | `core/v2/runtime.py`(run_v2_pipeline) + `client_factory.py` + schema 9→10（runs 加 failed_step/error_message + RunStatus.OPTIMIZING + 5 orchestrator 加 skip_run_status_update） | `5626084` |
| ├ 10.3 V2 Web API | ✅ 完成+推送 | `web/v2_service.py` + `web/v2_routes.py`（12 个 /api/v2/* 端点，复用 V1 session 鉴权 + V2_READY gating + CSRF） | `2ff8c9d` |
| ├ 10.3.1 补 GET /api/v2/runs | ✅ 完成+推送 | `repo.list_runs_by_user` + `service.list_runs` + `GET /api/v2/runs`（端点 12→13，MVP：仅本人 Run、created_at DESC、默认 50；title 取自 Doc） | `cf15866` |
| ├ 10.4 V2 前端接线 | ✅ 完成+推送 | `web/__init__.py` 加 /v2 路由 + `templates/v2.html` + `static/v2_app.js` + `static/v2_style.css`（独立页面，复用 V1 认证，不改 V1 三件套）；修复乐观锁时间戳 409 缺陷 + 3 回归 | `cf15866` |
| ├ 10.5 真实 LLM Run | ✅ 完成+推送 | `scripts/v2_real_run.py` CLI（483 行）+ `examples/v2_sample_requirement.md`（订单退款场景）+ `tests/test_v2_real_run_script.py`（20 smoke）；真实跑通 Step 2→8（run_id=`01M2QBWABYW3VTNYK5BBX2WDN4`，9m26s，157 LLM calls，28 items / 174 TPs / 174 TCs / 25 obligations，review overall=85.9，optimizer archived=24）；output JSON 无 api_key；修复 Windows GBK emoji 编码 bug | `c7955c8` |
| ├ 10.6 完整端到端验证 | ✅ 完成+推送 | `tests/test_step10_acceptance.py`（17 门槛 mock）+ `tests/test_step10_real_llm.py`（门槛 7-9 real_llm marker，本地验收用）+ `scripts/v2_step10_verify.py`（一键跑 + 22 条门槛报告表）+ pyproject 注册 real_llm marker | `ff799ab` |
| └ 10.7 记录运行数据 | ✅ 完成+推送 | `docs/v2/step10-real-run-record.md`（脱敏永久记录：9 节表格/列表，不嵌 JSON 全文）+ 更新本文件（Step 10 收尾）| `430a319` |
| — V2 UI/UX 重构（React） | ✅ 完成+已推送 | `frontend-v2/`（React+TS+Vite SPA）+ `templates/v2.html` SPA 壳 + 只读端点 requirements（13→14）+ 旧 v2_app.js/v2_style.css 退役；详见 `docs/v2/v2-ui-react-refactor.md` | 已推 origin+gitee (`cdb325c`) |
| — V2 Product Polish | ✅ 完成+已推送 | P0 视觉设计系统升级 + P1~P5 交互打磨 | 已推 origin+gitee (`d1c5345`) |
| 11 Quality Eval + Benchmark（S1~S8） | ⏳ **S1~S7 ✅（S7 三轨改造完成；baseline-v0.1 全量 runset 待跑）/ S8 ⬜** | S1 `docs/v2/step11-benchmark-evaluation.md`（冻结 v1.0）；S2 `core/v2/eval/schema.py`（Case/Gold/Manifest+loader）；S3 `core/v2/eval/`{matching,metrics_hard}.py（四态匹配+硬指标+RunObservation 契约，零 LLM）；S4 `benchmark/`（bench-v0.1：10 case+10 requirement+11 gold+manifest）+ 4 测试文件 147 例；S5 `core/v2/eval/`{metrics_soft,semantic_prompts}.py（语义三态+forbidden suspect+candidate 预审+JSON 契约）+ 34 例；S6 `core/v2/eval/runner_lib.py`+`scripts/v2_benchmark.py`（独立 CLI 进程+独立 benchmark DB+五态阶段门控分类+环境指纹+runset 快照+生产库硬护栏）+ 58 例；S7 `core/v2/eval/`{rails,textops}.py（三轨关联 identity 诊断>bridge>anchor + D10 不猜 item_id）+ `metrics_hard.py`/`runner_lib.py` 接入 + bc_01 Gold `gold-v0.2` + D7② `core/config.py` 空配置守卫 + D6 `config.yaml` 模型同步 + 48 例 + 设计文档附录 C；详见 §11 | S1~S4 已推 (`0cb4bf5`)；S5 已推 (`7b59bfe`)；S6 已推 (`f72db13`)；S7 本地待推 |
| 12~13 | ⬜ 未开始（Step 12 剩余主题与 Step 13 待 S8 后再评审） | — | — |

**测试基线**：V1 原有 126 例（零回归）+ V2 新增，**当前全量 1276 passed + 3 skipped**（Step 11 S2~S7 新增 292 例 Benchmark/Eval 测试，其中 S5 语义层 34、S6 Runner 58、S7 三轨 48；另 S7 的 D7② 在 `tests/test_regression.py` 新增 5 例 V1 配置守卫；启用 V2_RUN_REAL_LLM=1 后 +3），schema_version=10，ruff check/format 全绿。

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

## 5.10 Step 8 详情（去重体系 Dedup Optimizer）✅

**设计文档**：`docs/v2/step8-dedup-optimizer.md`（含 5 核心原则 + 数据流 + 14 门槛 + ADR）。

**目标**：消费 Step 7 `ReviewReport` 的 `duplication` findings（`auto_fixable=True`），执行确定性去重归档，触发 `after_optimizer` 重评审，形成 **生成→验证→评审→优化→复审** 自动质量闭环。对应蓝图 `Optimizer(自动优化/去重)`。

**5 条核心原则（用户冻结）**：
1. **只消费 Step 7 已确认的 duplication finding，不重新做重复判断**（职责分离）
2. **Duplicate pair 必须 canonicalize**（min_id, max_id → 唯一 pair，防止 A↔B 两条 finding 导致两边都被归档）
3. **Survivor 优先级**：HUMAN > OPTIMIZER > LLM > STRATEGY > VALIDATOR > MIGRATED → P0>P1>P2>P3 → created_at 越早 → display_id 越小（不覆盖人工意图）
4. **ARCHIVED 用例保留审计，不物理删除**（可回滚）
5. **Step 8 只改变重复用例状态，不改变 TestCase 内容**（Dedup Optimizer，不是全能 Optimizer）

**架构数据流**：`Latest ReviewReport → Filter duplication findings → Canonicalize pairs → 双边状态检查 → Determine survivor → Archive loser → OptimizerResult → 有归档？是→AFTER_OPTIMIZER 重评审(revision+1) / 否→DONE`。

**★ 关键设计（用户反馈修订）**：
- **Canonicalize pairs 是 P0 坑防护**：A↔B 两条 finding 如果不 canonicalize，会导致两边都被归档。min_id/max_id 确保唯一 pair。
- **双边状态检查**：`if A.status != REVIEWED or B.status != REVIEWED: skip`。任一方 ARCHIVED → `already_resolved`；否则 → `source_not_reviewed`。
- **Survivor 优先级 HUMAN 最前**：不覆盖人工意图。HUMAN 修改过的用例永远优先保留，即使 priority 更低。
- **created_at 越早优先**：生成序早的用例更稳定（文档明确，防止实现人员误解为“最新生成的优先”）。
- **有归档才触发重评审**：避免无意义重评审（全部 skip 时直接 DONE）。
- **OptimizerResult 内存不持久化**：第一版 ReviewFinding + OptimizerResult + TestCase status change 已足够审计。

**交付文件**（全在 `core/v2/`）：
| 文件 | 职责 |
|---|---|
| `optimizer.py` | 去重逻辑：canonicalize pairs, 双边状态检查, determine survivor, archive loser, record actions |
| `optimizer_orchestrator.py` | 编排：拉 latest report → 执行 optimizer → 有归档则触发 after_optimizer 重评审 → Run 状态机 |

**Schema/DDL 升级**（`schema_version` 7 → 8）：
- `core/schemas/common.py`：状态机放开 `REVIEWED → ARCHIVED`
- `core/v2/ddl.py`：SCHEMA_VERSION=8 + `_migrate_v7_to_v8` 分支（无 DDL 列变更，仅状态机代码层变更）
- 新增内存 dataclass：`OptimizerAction`（finding_id, case_a_id, case_b_id, kept_case_id, archived_case_id, reason, similarity）+ `OptimizerResult`（processed_findings, archived_cases, skipped_findings, actions[], skip_reasons）

**测试**（全 mock + 集成）：`test_optimizer.py`(35) + `test_optimizer_orchestrator.py`(7) + `test_step8_acceptance.py`(25) = **67 例**。

**14 条验收门槛：全部 PASSED**（1 只消费 duplication finding / 2 Canonical pair 去重 / 3 双边状态检查 / 4 already_resolved skip / 5 Survivor 优先级正确 / 6 归档不物理删除 / 7 OptimizerAction 记录完整 / 8 幂等 / 9 有归档才触发重评审 / 10 重评审 revision+1 trigger=AFTER_OPTIMIZER / 11 去重后 duplication 分数提升 / 12 schema=8+状态机生效 / 13 V1 零回归 / 14 不修改 TestCase 内容）。

**诚实边界**：不建 optimizer_actions 表（内存 OptimizerResult 足够审计）；不引入两级语义阈值（第一版统一 0.85 自动归档，留 Step 12 Benchmark 调）；不做内容优化（Dedup Optimizer 只去重）；不激活 TestCaseRevision 快照（留 Step 9/10）；不做“反悔”机制（ARCHIVED → REVIEWED 恢复留 Step 9）；不处理跨 run 重复（只在同一 run 内去重）。

---

## 5.11 Step 9 详情（人工编辑/确认闭环）✅

**设计文档**：`docs/v2/step9-human-edit.md`（含 9 核心原则 + 数据流 + 19 门槛 + ADR）。

**目标**：实现 TestCase 人工编辑闭环，激活 `TestCaseRevision` 快照（修改前完整内容 + changed_fields），引入乐观锁并发控制，保护 identity fingerprint 不变、content_hash 重算，编辑后必须过 Validator，用户主动触发 `AFTER_HUMAN_EDIT` 重评审。形成 **AI + 代码 + 人** 三者协同的完整测试设计流程。对应蓝图 `Human Review(人工确认/编辑)`。

**9 条核心原则（用户冻结）**：
1. **编辑范围白名单**：title/steps/expected/precondition/priority/module/remark 可改；test_point_ids + 系统字段不可改
2. **不自动重评审**：保存=标记 RE_REVIEW_REQUIRED；用户主动点击才触发 Review(AFTER_HUMAN_EDIT)
3. **激活 TestCaseRevision**：编辑前创建快照（revision_no+1），保存修改前完整 TestCase + changed_fields
4. **revision_no 语义区分**：test_case_revision_no（内容版本）vs review_revision（评审版本），代码不混叫
5. **fingerprint/content_hash 区分**：identity fingerprint 不变（同一 TestCase），content_hash 重算（内容变了）
6. **编辑后必须过 Validator**：FAIL→VALIDATION_FAILED，PASS→RE_REVIEW_REQUIRED
7. **VALIDATION_FAILED 可恢复**：用户重新编辑 → EDITED → Validator（不是死状态）
8. **乐观锁并发控制**：保存时 WHERE updated_at=?，冲突→整体事务回滚
9. **provenance 语义**：Revision.provenance="这一版谁改的"；TestCase.provenance="当前生效版本来源"（编辑后=HUMAN）

**架构数据流**：`TestCase(REVIEWED) → Edit API → 白名单过滤 → 状态检查 → 乐观锁 → Revision 快照(修改前) → 更新(provenance=HUMAN, fingerprint不变, content_hash重算) → Validator → FAIL:VALIDATION_FAILED / PASS:RE_REVIEW_REQUIRED → 用户点击重新评审 → Review(AFTER_HUMAN_EDIT, review_revision+1) → REVIEWED`。

**★ 关键设计（用户反馈修订）**：
- **Revision 保存修改前快照**：snapshot 是“修改前完整 TestCase”，不是修改后。这样才能恢复历史。
- **changed_fields 记录**：为 Preference Learning（蓝图调整后为 Step 13）提供数据源（用户最常改什么字段）。
- **乐观锁 + 事务回滚**：updated_at 冲突→整体回滚，无错误 Revision，无半截 TestCase。
- **VALIDATION_FAILED 可恢复**：状态机放开 VALIDATION_FAILED→EDITED，用户重新编辑修复。
- **fingerprint 不变 / content_hash 重算**：Step 5 双指纹设计的实际消费场景。
- **不自动重评审**：避免每次编辑都调 LLM（成本/延迟），用户主动点击才 Review。

**交付文件**（全在 `core/v2/`）：
| 文件 | 职责 |
|---|---|
| `human_editor.py` | 编辑逻辑：白名单过滤 + Revision 快照 + changed_fields + Validator + fingerprint/content_hash + 乐观锁 + provenance |
| `human_editor_orchestrator.py` | 编排：edit_case（单条）+ re_review_test_cases（批量接口预留，第一版内部统一 Review） |

**Schema/DDL/Repository 升级**（`schema_version` 8 → 9）：
- `core/schemas/common.py`：状态机放开 `VALIDATION_FAILED → EDITED`
- `core/schemas/reserved.py`：TestCaseRevision 扩展（revision→revision_no + changed_by + change_source）
- `core/v2/ddl.py`：SCHEMA_VERSION=9 + `test_case_revisions` 表 + `_migrate_v8_to_v9` 分支
- `core/v2/repository.py`：save/get TestCaseRevision + get_latest_revision_no + update_test_case_with_lock（乐观锁）+ ConcurrentModificationError
- `core/v2/review_orchestrator.py`：支持 RE_REVIEW_REQUIRED 状态用例的重评审

**测试**（全 mock + 集成）：`test_human_editor.py`(30) + `test_human_editor_orchestrator.py`(7) + `test_step9_acceptance.py`(22) = **59 例**。

**19 条验收门槛：全部 PASSED**（1-3 编辑范围白名单+系统字段保护 / 4-5 Revision快照+changed_fields / 6-7 provenance记录 / 8-9 fingerprint不变+content_hash重算 / 10-12 Validator+VALIDATION_FAILED可恢复 / 13 全流程状态机 / 14-15 乐观锁+事务回滚 / 16-17 不自动重评审+AFTER_HUMAN_EDIT / 18 批量接口预留 / 19 schema=9+V1零回归）。

**诚实边界**：不做前端 UI（Step 13）；不引入 batch queue/parallel/retry（批量接口仅 list 参数）；不做 Re-link TestCase（test_point_ids 修改留未来）；不加 auto_review_on_edit 配置项（太早）；不做 Revision 差异对比 UI（仅保存 snapshot+changed_fields）；不做多用户协同编辑锁（仅乐观锁）。

---

## 6. 期间修复的重要 bug（Step 1 潜伏）

**`INSERT OR REPLACE` + `ON DELETE CASCADE` 陷阱**：`INSERT OR REPLACE` = 先 DELETE 再 INSERT，DELETE 会级联删子表。Step 2 的 `build_requirement_ir` 重存 doc 更新 `latest_version_id` 时，会**级联删光该 doc 的所有 version→item**（若 Step 3 重存 run 更新状态，会删光其所有用例）。
**修复**：`core/v2/repository.py` 全部 11 个 save 改用 **upsert（`INSERT ... ON CONFLICT(id) DO UPDATE`）**，原地更新不删行不级联。
**回归锁定**：`TestUpsertNoCascade`（重存 doc/version/run 后子数据必须存活）。

---

## 7. 代码结构

```
core/schemas/    # Pydantic 唯一真源：common/requirement/testpoint/testcase/strategy/review/run/preference/reserved/__init__
core/v2/         # V2 持久层 + 领域服务（独立 data_v2.db，schema_version=10）
  db.py          #   连接管理（WAL/foreign_keys/写锁）
  ddl.py         #   建表 SQL + schema_version（含 v2→v3→…→v9→v10 自动升级分支）
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
  optimizer.py optimizer_orchestrator.py                                 # Step 8 Dedup Optimizer（去重归档 + 有条件重评审）
  human_editor.py human_editor_orchestrator.py                           # Step 9 Human Editor（人工编辑 + Revision 快照 + 乐观锁 + Validator）
  bootstrap.py   # Step 10.1 V2 DB 真初始化（ensure_v2_ready，web 启动路径调用）
  runtime.py client_factory.py                                           # Step 10.2 顶层 Runtime（run_v2_pipeline）+ LLMClient 构建工厂
  eval/        # Step 11 Benchmark 评价层（Runtime 外部只读观测，零 DB 写入）
    schema.py  #   S2：Case/Gold/Manifest 契约 + loader + fail-fast 数据集校验 + eval 私有枚举
    matching.py#   S3：四态 Matching（match_keys>重算>similarity 不计分）+ 场景锚点 + 义务 + 策略触发
    metrics_hard.py # S3：硬指标 + RunObservation 统一输入契约（Cost/Latency/双轨 Reviewer 透传）；S7 接入三轨关联 + identity 诊断单元
    metrics_soft.py semantic_prompts.py # S5：语义三态 + forbidden suspect + candidate 预审 + benchmark-soft-v1 JSON 契约（S7 零改动）
    runner_lib.py#  S6：Artifacts 装配（ARCHIVED 排除）+ 五态阶段门控分类 + Hard 序列化 + 环境指纹 + 原子写/敏感自检 + 生产库护栏
    textops.py #    S7（裁决 D8）：`anchor_satisfied` / `statement_similarity` 两个通用文本 helper（非 NLP 层，配防漂移测试）
    rails.py   #    S7（裁决 D1/D2/D3/D9/D10）：三轨关联唯一入口 identity(诊断)>bridge>anchor + RailMatch.via + IdentityDiagnostics + D10 不猜 item_id
web/             # Web 层：__init__.py（V1 蓝图 + /v2 页面路由 Step 10.4）+ v2_service.py（HTTP↔core/v2 适配 Step 10.3）+ v2_routes.py（14 个 /api/v2/* 端点，含 UI 重构新增只读 requirements）
scripts/         # CLI：v2_real_run.py（Step 10.5 真实 LLM 全链路，权威验证路径）+ v2_step10_verify.py（Step 10.6 一键验收 + 22 条门槛报告表）+ v2_benchmark.py（Step 11 S6 Benchmark Runner，独立进程 + 独立 benchmark DB + runset 快照）
benchmark/       # Step 11 S4 bench-v0.1 数据集（cases/ 10 正式+demo 的 json+requirement.md；gold/ 11；manifests/ bm-bench-v0-1 十 case + bm-demo[bench-v0.0-demo 隔离]）；runsets/（S6 输出，已 gitignore 不入库）
templates/v2.html（React SPA 壳）+ frontend-v2/（V2 前端源码 React+TS+Vite）+ static/v2/（构建产物 v2_react.js/v2_react.css，入库）  # UI 重构后形态；V1 三件套零改动
docs/v2/         # 设计文档（step1-data-model.md + step3/step4/step5/step6/step7/step8/step9 + step10-real-run-record.md + v2-ui-react-refactor.md + step11-benchmark-evaluation.md + 本文件）
tests/           # test_schemas/state_machine/v2_repository/v2_roundtrip/resolver/migration
                 # ir_*/step2_acceptance
                 # tp_generator/tp_validator/tp_orchestrator/step3_acceptance
                 # strategy_boundary/strategy_equivalence/strategy_permission
                 # tp_strategy_deriver/strategy_orchestrator/step4_acceptance
                 # test_data_planner/tc_generator/tc_validator/tc_orchestrator/step5_acceptance
                 # traceability/change_impact/step6_acceptance
                 # review_hard/review_soft/review_orchestrator/step7_acceptance
                 # optimizer/optimizer_orchestrator/step8_acceptance
                 # human_editor/human_editor_orchestrator/step9_acceptance
                 # test_v2_bootstrap/test_v2_runtime/test_v2_web_api（Step 10.1~10.4）
                 # test_v2_real_run_script（Step 10.5 CLI smoke）
                 # test_step10_acceptance（Step 10.6：17 门槛 mock）/test_step10_real_llm（门槛 7-9 real_llm marker）
                 # test_benchmark_schema（S2 契约 90）/test_benchmark_eval_s3（S3 引擎 30）
                 # test_benchmark_formal_data（S4 机械契约回归 13）/test_benchmark_bc02_synthetic（S4 正负向+通用接线 14）
                 # test_benchmark_metrics_soft（S5 语义层 34：三态/降级/双轨隔离/批次/序列化）
                 # test_benchmark_runner（S6 Runner 58：DB 隔离与生产库护栏/五态分类/ARCHIVED 口径/指纹/防泄漏/runset 契约/CLI）
                 # test_benchmark_rails（S7 三轨 48：textops+防漂移/identity 轨不变/bridge/anchor/D10 不猜/无模糊计分/S5 零改动接线/报告面守卫/两真实库回归/边界纪律）
```

## 8. 运行 / 验证命令（PowerShell）

```powershell
# venv 已就绪（若无：python -m venv .venv; .\.venv\Scripts\pip install -e ".[dev]"）
.\.venv\Scripts\python.exe -m pytest -q                    # 期望 1276 passed + 3 skipped（real_llm marker 默认 skip；启用 V2_RUN_REAL_LLM=1 后 1279）
.\.venv\Scripts\python.exe -m ruff check .                 # 期望 All checks passed
.\.venv\Scripts\python.exe -m ruff format --check .        # 期望全部 formatted
.\.venv\Scripts\python.exe start.py -p 5000 --no-browser   # 启动服务（V1 在 /，V2 在 /v2；改代码/提示词需重启，DB model_config 实时生效）
```

## 9. Step 10「V2 Runtime + Productization」（✅ 全部完成：10.1~10.7 已推送）

> 需求来源：用户提供的《V2 Step10开发基线》文档（桌面，968 行）。
> 详细实施计划：《Step 10 实施计划与代码差距分析》（用户已保存，含 10.1~10.7 每个子步骤的差距/实施/验收）。
> **当前状态（2026-09）**：Step 10 **全部完成**——10.1（`e660ead`）/10.2（`5626084`）/10.3（`2ff8c9d`）/10.3.1+10.4（`cf15866`）/10.5（`c7955c8`）/10.6（`ff799ab`）/10.7（`430a319`）已推送，origin=gitee=`430a319` 三方同步；全量 980 passed + 3 skipped，ruff 双绿，schema_version=10。真实运行数据已脱敏沉淀于 `docs/v2/step10-real-run-record.md`（详见 §9.8~§9.10）。下一步 = §9.6 中后期路线评审（待用户另开）。
>
> **10.4 完成情况（已推送 origin+gitee：`cf15866`；V1 运行时修复：`1733052`）**：
> - ✅ 10.4 completed：`/v2` 独立页面（`templates/v2.html` + `static/v2_app.js` + `static/v2_style.css`）；决策2 未登录 302 跳回 V1；V1 三件套（index.html/app.js/style.css）零改动，`style.css` 只读复用为基础样式。
> - ✅ 10.3.1 Run List included：`GET /api/v2/runs`（第 13 端点），仅本人 Run、`created_at DESC`、默认 50，`title` 取自 Doc；无 V2 user → `[]`（GET 不自动开通）。
> - ✅ 409 timestamp normalization bug fixed and regression locked：`web/v2_service._normalize_lock_ts` 归一 API 的 `...Z` 与 DB 的 `...+00:00`，修复人工编辑必现 409；回归 `test_36/37/38`。
> - ✅ Real LLM generation/re-review intentionally deferred to 10.5：`[生成]`/`[重新评审]` 会触发真实 LLM，按冻结方案留 10.5；本次仅用 repository 播种真实 schema 数据（`data_v2.db`，gitignore）做前端验证。

### 9.1 核心目标（一句话）

**把 Step 1~9 从"代码已经存在"变成"用户真的可以使用"**：真实数据库 + 真实 LLM + 真实 Runtime + 真实 Web + 真实用户流程 + 真实运行数据。不新增 AI 功能。

### 9.2 7 个子步骤（执行顺序，每个子步骤：实现→测试→验收→用户确认→再下一步）

```
10.1 V2 DB 真初始化 ✅（core/v2/bootstrap.py:ensure_v2_ready + web/__init__.py 启动接线，commit e660ead）
 ↓
10.2 顶层 Runtime ✅（core/v2/runtime.py:run_v2_pipeline + client_factory.py；schema 9→10：
      runs 加 failed_step/error_message + RunStatus 加 OPTIMIZING；底层 5 个 orchestrator 加 skip_run_status_update 参数，commit 5626084）
 ↓
10.3 V2 Web API ✅（web/v2_service.py + web/v2_routes.py，12 个 /api/v2/* 端点，复用 V1 session 鉴权，commit 2ff8c9d）
 ↓
10.4 V2 前端接线 ✅ 完成+推送（commit `cf15866`）（含 10.3.1 补 GET /api/v2/runs 端点 12→13；templates/v2.html + static/v2_app.js + v2_style.css，独立 /v2 页面，不改 V1；修复乐观锁时间戳 409 + 3 回归）
 ↓
10.5 真实 LLM Run ✅（scripts/v2_real_run.py CLI + examples/v2_sample_requirement.md，不 mock；commit c7955c8）
 ↓
10.6 完整端到端验证 ✅ 完成+推送（commit ff799ab）（tests/test_step10_acceptance.py 17 门槛 mock + tests/test_step10_real_llm.py 门槛 7-9 real_llm marker + scripts/v2_step10_verify.py 一键跑 22 条报告表）
 ↓
10.7 记录真实运行数据 ✅ 完成（待推送）（docs/v2/step10-real-run-record.md 脱敏永久记录 + 更新本文件）
 ↓
【Step 10 全部完成 ✅ 正式收尾；不机械进入 Step 11，须先做 §9.6 中后期路线评审（用户另开）】
```

### 9.3 用户冻结的 4 点修正 + 1 安全护栏（必须遵守）

1. **schema_version 统一**：开发前=9，10.2 迁移后=10，所有最终验收（含 /api/v2/health）统一写 **10**。
2. **Runtime 是 Run 状态唯一编排者**：底层 Step 3~8 orchestrator 新增 `skip_run_status_update: bool = False` 参数（默认 False 向后兼容）；Runtime 调用时传 True，底层只返回产物不改跨阶段状态，避免"两个控制器"冲突。
3. **Web 同步 timeout = MVP 已知限制**：第一版 POST /api/v2/runs 同步执行（不引入 Celery/RQ）；**CLI（10.5）是权威真实运行验证路径**，Web 超时不阻塞验收。
4. **阶段分离**：Runtime 自动完成 Step 2~8；Human Edit / Re-review 是用户后续主动操作，**不在** run_v2_pipeline 内（对齐 Step 9"不自动重评审"）。
5. **API Key 安全护栏**：真实运行脚本/日志/output JSON 绝不得出现 api_key / Authorization header；可记录 provider/model/temperature/prompt_version；验收时 grep 检查。

### 9.4 Step 10 严格禁止（不得提前塞入）

Preference Learning、新 Strategy（Decision Table/State Transition）、Playwright/API 自动执行、Agent、RAG、向量数据库、异步任务队列、auto_review_on_edit 配置项、Re-link TestCase（改 test_point_ids）。

### 9.5 验收门槛（22 条）与证据映射（10.6 已落地）

| # | 门槛 | 证据来源 |
|---|---|---|
| 1 | DB 真初始化(tables>=15) | `test_step10_acceptance.py::test_01` |
| 2 | schema_version=10 | `test_step10_acceptance.py::test_02` |
| 3 | 统一 Runtime 存在 | `test_step10_acceptance.py::test_03` |
| 4 | 单一调用完成 Step 2→8 | `test_step10_acceptance.py::test_04` |
| 5 | Web API 创建查询 Run | `test_step10_acceptance.py::test_05` |
| 6 | Web 触发生成 | `test_step10_acceptance.py::test_06` |
| 7 | 真实 LLM 调用成功 | `test_step10_real_llm.py::test_07`（real_llm marker，本地验收） |
| 8 | 真实数据入库 | `test_step10_real_llm.py::test_08`（real_llm marker，本地验收） |
| 9 | Step 2→3→4→5→7→8 真实跑通 | `test_step10_real_llm.py::test_09`（real_llm marker，本地验收） |
| 10 | Human Edit 可用 | `test_step10_acceptance.py::test_10` |
| 11 | Re-review 可用 | `test_step10_acceptance.py::test_11` |
| 12 | Revision 可查 | `test_step10_acceptance.py::test_12` |
| 13 | Traceability 可查 | `test_step10_acceptance.py::test_13` |
| 14 | Coverage 可展示 | `test_step10_acceptance.py::test_14` |
| 15 | Review 可展示 | `test_step10_acceptance.py::test_15` |
| 16 | Optimizer 可展示 | `test_step10_acceptance.py::test_16` |
| 17 | Run 状态正确且 Runtime 唯一控制 | `test_step10_acceptance.py::test_17` |
| 18 | 异常定位 failed_step | `test_step10_acceptance.py::test_18` |
| 19 | V1 零回归 | `test_step10_acceptance.py::test_19` |
| 20 | 全量 pytest 绿 | `scripts/v2_step10_verify.py`（subprocess pytest returncode） |
| 21 | ruff check | `scripts/v2_step10_verify.py`（subprocess ruff check .） |
| 22 | ruff format + API Key 安全 | `scripts/v2_step10_verify.py`（ruff format --check + 静态扫描）+ `test_step10_acceptance.py::test_22` |

一键验收：`python scripts/v2_step10_verify.py [--with-real-llm]`（输出 22 条门槛报告表；全 PASS→exit 0，任一 FAIL→exit 1）。

### 9.6 Step 10 完成后（重要）

**不机械进入 Step 11**，必须先基于 10.7 真实运行数据做「V2 中后期路线评审」，回答：A. AI/Prompt 质量是否主要瓶颈？B. 前端体验是否仍是瓶颈？C. 是否先做 Benchmark？D. 是否已有足够编辑数据做 Preference Learning？E. 是否提前自动化执行？F. 哪个能力对产品价值提升最大？Step 11~13 顺序以评审结论为准。

> **状态（10.7 完成后）**：Step 10 已全部完成并正式收尾，真实运行数据已沉淀于 `docs/v2/step10-real-run-record.md`（含 6 维评分：accuracy=78 / missing_risk=72 偏低，可作为路线评审 A/C 的输入）。本路线评审（A-F）**待用户另开一轮讨论**，不在 10.7 内触发。

### 9.7 新会话开工确认话术

> "我已阅读 PROGRESS.md + Step 10 实施计划。当前：Step 10 全部完成——10.1~10.6 已推送（最新 `ff799ab`），10.7「记录真实运行数据」已完成（待推送；`docs/v2/step10-real-run-record.md` 脱敏永久记录）。全量 980 passed + 3 skipped，ruff 双绿，schema_version=10。Step 10 正式收尾，不提前进入 Step 11；下一步 = §9.6 V2 中后期路线评审（A-F 问题）。是否开始？"

### 9.8 10.5「真实 LLM Run」新会话须知（交接）

**目标**：新增 `scripts/v2_real_run.py` CLI + `examples/v2_sample_requirement.md`，用真实 LLM 跑通 Step 2→8 全链路（不 mock）。**CLI 是权威真实运行验证路径**（Web 同步 timeout 为 MVP 已知限制）。

**可复用入口**：
- `core/v2/runtime.run_v2_pipeline(*, user_id, title, text=None, paths=None, source_type=SourceType.TEXT, client=None, stop_after=None) -> PipelineResult`（`stop_after` ∈ ir/testpoints/strategy/testcases/review/optimizer）
- `core/v2/client_factory.build_llm_client(purpose)`（purpose="generate"/"review"）

**环境 / 配置（实测可用）**：
- **LLM 实际生效源 = DB `model_config`**（`config.get_model_config()` 每次实时读，`config.yaml` 仅兜底）；改模型/Key 优先改 DB（网页「模型配置」或 `db.save_model_config`）
- 当前接入：阿里云百炼 OpenAI 兼容网关，模型 `deepseek-v4-flash-0731`，`enable_thinking=False`（已修复：关闭时显式下发，避免网关默认开思考导致正文为空）
- **安全护栏**：真实运行脚本/日志/output JSON 不得出现 api_key；可记录 provider/model/temperature/prompt_version

**注意事项**：
- 真实 LLM 运行耗时且有费用；测试点提示词已改为按需求规模自适应（不再固定 60-120 条），避免超长截断
- 10.5 前置已就绪：schema_version=10、`data_v2.db` 已初始化、Runtime/Web API/前端均已验收（942 passed）

### 9.9 10.6「完整端到端验证」（已完成+推送 ff799ab）

**目标**：把 §9.5 的 22 条门槛变成可 CI 化、可一键重跑的硬证据链；不新增 AI 功能、不动 V1、不改 Runtime/orchestrator/repository。

**交付物**：
- `tests/test_step10_acceptance.py`（17 例，mock/临时 DB）：门槛 1-6,10-19,22（静态扫描部分）。门槛 4 用「会落真实数据的 fake orchestrator」验证一次 `run_v2_pipeline` 调用后 DB 里 items/TPs/TCs/Obligation/Review 均非空 + `Run.counts` 四字段 >=1。
- `tests/test_step10_real_llm.py`（3 例，`real_llm` marker）：门槛 7-9。**默认 skip**；`V2_RUN_REAL_LLM=1` 时启用，**读 10.5 已有真实产物**（`output/v2_real_run_*.json` 通配符取最新 + 生产库 `data/data_v2.db`）做断言，不重复烧 token；产物缺失时 skip（绝不 fail）。
- `scripts/v2_step10_verify.py`：一键跑 pytest+ruff+静态扫描，输出「门槛编号/名称/证据来源/状态」四列报告表；全 PASS（SKIP 不算 FAIL）→exit 0，任一 FAIL→exit 1。
- `pyproject.toml`：注册 `real_llm` marker。

**运行命令**：
```powershell
# 业务门槛（17 例 mock）
pytest tests/test_step10_acceptance.py -v          # 期望 17 passed（+1 健全性）
# real_llm 门槛（3 例，默认 skip）
pytest tests/test_step10_real_llm.py -v            # 期望 3 skipped
$env:V2_RUN_REAL_LLM='1'; pytest tests/test_step10_real_llm.py -v   # 期望 3 passed（需本地 10.5 产物）
# 一键验收（22 条门槛报告表）
python scripts/v2_step10_verify.py                 # 期望 19 PASS / 0 FAIL / 3 SKIP
python scripts/v2_step10_verify.py --with-real-llm # 期望 22 PASS / 0 FAIL / 0 SKIP
```

**★ 用户 P0（真实证据资产化边界）**：`output/` 被 gitignore，**真实运行证据仅用于本地验收，不作为永久测试资产随仓库分发**；最终可提交仓库的脱敏证据由 **10.7** 正式沉淀（`docs/v2/step10-real-run-record.md`）。因此 `test_step10_real_llm.py` 是「本地验收用」测试，CI 上默认 skip 是合理行为。

**诚实边界**：
1. real_llm 测试依赖 10.5 本地产物（gitignore），CI/他人本地没有 → 默认 skip；启用后产物缺失也 skip。
2. verify 脚本的 pytest 输出解析依赖 `-v` 格式（正则抓 `PASSED|FAILED|SKIPPED|ERROR`），对 pytest 大版本敏感（实测 pytest 9.1.1 可用）。
3. 门槛 19 V1 零回归只断言「V1 文件存在 + V1 端点 200」，深度回归由已有 126 例 V1 测试覆盖。
4. 门槛 22 api_key 静态扫描是启发式正则（极端编码可能漏报）；10.5 的 `assert_no_sensitive` 运行时双保险已覆盖 output JSON 路径。
5. verify 脚本门槛 20 会跑一次全量 pytest（~983 例）；`--skip-pytest` 可只跑 ruff+扫描快速检查（此时门槛 1-20 标 SKIP）。

### 9.10 10.7「记录真实运行数据」（已完成，待推送）

**定位（用户冻结）**：10.7 = 真实运行证据永久化 + 脱敏 + Step 10 正式收尾，**不提前进入 Step 11**。

**交付物**：
- `docs/v2/step10-real-run-record.md`（9 节完整快照，纯表格/列表）：运行元信息 / 环境配置（白名单脱敏）/ 全链路总览 / 六阶段明细 / Review 6 维质量指标 / Optimizer 结果 / 10.6 验收结论 / 脱敏与安全声明 / 观察与已知限制。数据源 = 10.5 的 `output/v2_real_run_*.json` + `_step10_5_archive.md`（只读消费，不重跑 LLM）。
- 更新本文件（§0/§2/§3/§7/§9.2/§9.6/新增本节）标记 Step 10 收尾。

**脱敏边界（用户 P0）**：
- **保留**：run_id / doc_id / version_id（ULID，随机标识、审计必需、无隐私）。
- **脱敏/不记录**：需求正文与字段明细（仅引用 `examples/v2_sample_requirement.md` 路径 + 场景概述）、token、API Key、Authorization、base_url、用户敏感信息（user_id ULID）。
- **不嵌 JSON 全文**：完整快照 = 提炼核心指标/关键字段为表格/列表，绝不把 output JSON 全文粘贴进 Markdown（避免文档过长）。
- 真实 `output/v2_real_run_*.json` + `data/data_v2.db` 留在 gitignore 路径，不提交仓库（本地验收用，呼应 §9.9 P0）。

**诚实边界**：
1. 数据来自 10.5 单次真实运行（样本量=1），不具统计代表性；仅作 Step 10 完成证据，质量结论留 Step 11/12。
2. accuracy=78 / missing_risk=72 等 LLM 软分偏低且有波动，文档如实记录实测值、不下质量结论、不代表稳定基线。
3. 10.7 纯文档，不改任何代码、不新增测试（测试基线仍 980 passed + 3 skipped）。
4. Step 10 完成后**不机械进入 Step 11**——须先按 §9.6 做中后期路线评审（用户另开）。

## 10. 协作约定（重要）

- **每步先讨论方案 → 用户确认 → 再实现 → 验证 → 用户确认后再提交推送**；不要抢跑下一步。
- **未经用户明确要求不 `git commit`/push**。Git 约定：不直推 main（除非明确要求）；commit 带中文；推双远程 origin(github)+gitee，gitee 绕代理：`git -c http.proxy="" -c https.proxy="" push gitee main`。
- V1 运行时（`core/db.py`、`web/`、V1 表、`data.db`）**保持不动**；V2 全部新增、独立 `data_v2.db`。
- 完成里程碑后：跑全量 pytest + ruff 给真实证据，逐条核对验收标准，保持工作区稳定供 review。

---

## 11. Step 11「Quality Evaluation + Benchmark Foundation」（S1~S6 ✅ 2026-09-21；S7 三轨改造 ✅ 2026-09-22；S1~S4 已推 `0cb4bf5`，S5 已推 `7b59bfe`，S6 已推 `f72db13`，S7 本地待推）

**目标链路（冻结）**：`Benchmark Case → scripts/v2_benchmark.py → run_v2_pipeline → V2 原有产物 → Evaluator → Report → Baseline v0.1`。评价层位于 Runtime 外部，18 条架构边界冻结于 `docs/v2/step11-benchmark-evaluation.md`（不改 V1/Runtime/orchestrator/API/前端/Prompt/core.schemas/DDL，schema_version 保持 10，独立 benchmark DB）。

| 子步 | 内容 | 交付 | 状态 |
|---|---|---|---|
| S1 | 设计冻结 v1.0（含实施前核查 12 条结论：ddl.SCHEMA_VERSION=10 澄清 / BenchmarkRunStatus 五态 / MetricProvenance CODE-LLM-MIXED / 失败传播现状与启发式分类局限实录） | `docs/v2/step11-benchmark-evaluation.md` | ✅ 验收通过 |
| S2 | Benchmark 数据契约：`BenchmarkCase/Gold/Manifest` + eval 私有枚举 + loader（extra=forbid、配对/引用/版本 fail-fast、gold_authoring 正式态机检）+ demo fixture | `core/v2/eval/schema.py` + `tests/test_benchmark_schema.py`(90) | ✅ 验收通过 |
| S3 | 确定性评价引擎（零 LLM/零 DB）：四态 Matching（match_keys 权威源 > statement 重算 fallback > similarity 仅产 CANDIDATE）/ 结构化场景锚点（step.action/tc.expected）/ 义务 min_covered_points / 策略条件触发 / 硬指标 + INVALID·PENDING_REVIEW·Gold MISS 分离 / Cost-Latency / REVIEWER-BASED 透传 / `RunObservation` 统一输入契约（S6 消费） | `core/v2/eval/{matching,metrics_hard}.py` + `tests/test_benchmark_eval_s3.py`(30) | ✅ 验收通过 |
| S4 | **bench-v0.1 正式数据集**：10 业务域 case（auth/order×2/payment/admin/search/form/file/workflow/report）+ 10 纯业务 requirement + **83 条人工独立 Gold**（52 场景/52 义务/41 策略；9 条 min_covered_points>1 全部有验收标准逐字依据；83/83 指纹由 helper 生成并双层复核）+ 正式 manifest（cases=10）+ demo 隔离（bench-v0.0-demo）+ 机械契约回归（per-gold 指纹唯一）+ 正负向/通用 Synthetic | `benchmark/**` + `tests/test_benchmark_formal_data.py`(13) + `tests/test_benchmark_bc02_synthetic.py`(14) | ✅ 验收通过+已提交推送 |
| S5 | 语义评价层（Semantic Accuracy 三态 correct/partial/incorrect + pending 治理 / forbidden LLM 仅产 suspect 不写 S3 invalid / additional_valid 仅产 suggested / 双轨物理隔离透传 / 确定性分批 20+6000 独占+24000 拆分 / JSON `benchmark-soft-v1` 序列化；独立评价 Prompt 非第二套 Reviewer，零 DB 依赖） | `core/v2/eval/`{metrics_soft,semantic_prompts}.py + `tests/test_benchmark_metrics_soft.py`(34 测试) | ✅ 验收通过 |
| S6 | Benchmark Runner：`core/v2/eval/runner_lib.py`（纯函数层：Artifacts 装配含 ARCHIVED 排除 / 五态**阶段门控**分类 / Hard 序列化 / 环境指纹 / 原子写+敏感自检 / 生产库护栏）+ `scripts/v2_benchmark.py`（**独立 CLI 进程**：进程入口 `set_v2_db_path` → benchmark DB 默认 fresh → 输入预检 → `run_v2_pipeline(client=None, text=...)` → S3 常跑、非 COMPLETED 不跑 S5 LLM → `benchmark/runsets/<id>/`（runset.json + cases/*.json + manifest.resolved.json + `_COMPLETE.json`）；退出码 0/1/2/3/4/5；不新增 `--skip-soft/--compare/--stop-after` 等越权参数） | `core/v2/eval/runner_lib.py`(839) + `scripts/v2_benchmark.py`(662) + `tests/test_benchmark_runner.py`(58) + `.gitignore`(`benchmark/runsets/`) + 设计文档附录 B | ✅ 验收通过 |
| S7 | **三轨关联改造（架构裁决 D1~D10/D13 + D7②/D6）** + bc_01 双模型 smoke 复测：新增 `core/v2/eval/rails.py`（三轨关联唯一入口：Identity 仅诊断 / obligation bridge / scenario anchor，`identity>bridge>anchor` 去重，D10 `item_id` 不猜）+ `core/v2/eval/textops.py`（D8 两个通用 helper + 防漂移测试）；`metrics_hard.py` 接入（`HardMetricsReport` 新增 `identity_match_rate`/`via_counts`/`identity_diagnostics`/`anchor_scope`，`AUTO_HIT` 正式重定义为"确定性关联命中"）；`runner_lib.py` payload 扩展（8 cell + match 带 `via`，**S6 形态旧 runset 仍可加载**）；bc_01 Gold anchor 修订 → `gold-v0.2`（三条非原文锚点改为需求原文逐字）；D7② `core/config.py` 空配置守卫（独立可回滚 V1 小改动 + 5 例回归）；D6 `config.yaml` fallback 模型同步 `qwen3.8-flash`。`metrics_soft.py`/`matching.py` identity 语义/Runtime/Parser/Prompt/DDL/API/前端 **零改动**。**注：正式 baseline-v0.1（10 case 全量 runset）尚未执行**，仍需用户指令 | `core/v2/eval/`{rails,textops}.py + `metrics_hard.py`/`runner_lib.py` 改动 + `benchmark/gold/bc_01_login.gold.json`(v0.2) + `tests/test_benchmark_rails.py`(48) + `core/config.py` + `tests/test_regression.py`(+5) + 设计文档附录 C | ✅ 改造完成（G1~G4 全过）；⬜ baseline-v0.1 待跑 |
| S8 | Compare/Delta（无 CI 门禁）+ Step 11 收尾文档 | — | ⬜ |

**Gold 独立性铁律（S4 已执行并留痕）**：Gold = `原始需求 → 人工独立分析 → Gold`；禁止 runtime 输出筛选闭环；Gold 是 floor 非 ceiling；TP 数量不设门槛、precision/recall 非唯一核心；`authoring_note`/`reference_cases` 全程留痕（bc_03~bc_10 未查阅任何素材，bc_02 仅命名口径核对）。

**挂账事项**：① `ObligationExpected.target` 的 schema 注释"角色-资源"应更正为实际 `role:resource:action`（非阻塞，单独 cleanup）；② S7 解读报告时 requirement coverage 基线偏低属措辞漂移设计内结果，**禁止反向修改 Gold 迁就模型**；③ **D7①**（`POST /api/model-config` 缺空值守卫，可写入空 model/base_url）仅立案未改；④ **D13**：bc_01 §5.3 前后端双拦截覆盖、「验证码错误」场景挂 Gold v0.3；⑤ **bc_03_order_status 的 `obligations_expected` 为空** → bridge 轨对该 case 不可用，真实运行时其 requirement coverage 只能靠 identity+anchor，挂 Gold v0.3（`benchmark/gold` 属只读冻结面）；⑥ **bc_demo_login 2 条 `ri_` 指纹**（`gr-login-ok`/`gr-phone-len`）与重算值不符，因 `test_benchmark_formal_data.py` 只过滤 `bench-v0.1` 而从未触发；⑦ **D14 立案未实施**：多 Gold 场景（N>1）反查到 M 个 item 且 `M != N` 时应同按 D10 处理（`item_id=None`+pending）；前置已实测 10 个 formal case 理想 runtime 下 3 个多 Gold 场景全部 M==N、触发 0 次，故 formal 全绿契约与附录 C.5 全部数字均不受影响。

**S7 数字口径声明**：附录 C.5 表格中的 8/10、3/6、0.5994、43/66 等**只是本次三轨重构的回归对照基线（regression acceptance fixture）**，不得表述为产品质量目标、验收门槛或 AI 能力评分；deepseek 与 qwen 两列差异只陈述可观测事实，**不构成模型能力优劣排名**。裁决 D5：`qwen3.8-flash` 为 v0.1 正式 baseline 模型，DeepSeek 仅历史/对照 smoke，两者指标不得混算。
