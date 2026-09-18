# V2 Step 10 真实运行记录（脱敏永久存档）

> **定位**：本文档是 Step 10.5 真实 LLM 全链路运行的**可提交仓库的脱敏永久记录**（Step 10.7 沉淀）。
> 真实运行证据（`data/data_v2.db` + `output/v2_real_run_*.json`）被 `.gitignore` 排除，**仅用于本地验收，不随仓库分发**；
> 本文档把其核心指标与关键字段脱敏提炼为表格/列表，作为仓库内可长期追溯的运行证据（呼应 PROGRESS §9.9 用户 P0）。
>
> **脱敏原则**：run_id/doc_id/version_id 等 ULID 保留（随机标识、审计必需、无隐私）；需求正文、token、API Key、Authorization、base_url、用户敏感信息（user_id ULID）全部脱敏/不记录；不粘贴 output JSON 全文。
>
> - 真实运行日期：2026-09-17
> - 记录沉淀日期：2026-09-18（Step 10.7）

---

## §1 运行元信息

| 字段 | 值 |
|---|---|
| run_id | `01M2QBWABYW3VTNYK5BBX2WDN4`（保留，供审计追溯） |
| doc_id | `01M2QBV4SJDHTF1GRYJEG76FQZ`（保留） |
| version_id | `01M2QBV4T7MWH1CXZM2S61N221`（保留） |
| 触发路径 | CLI `scripts/v2_real_run.py`（PROGRESS §9.3-3：CLI 为权威真实运行验证路径） |
| 运行用户 | CLI 本地自动开通用户（默认名 `cli_real_run`，为 `scripts/v2_real_run.py` 公开默认值）；**user_id ULID 脱敏不记录** |
| 需求场景 | 订单退款申请（含 FieldSpec / BusinessRule / PermissionRule 三类结构化数据）；正文见仓库内 `examples/v2_sample_requirement.md`，**本文档不摘录需求正文与字段明细** |
| 运行结果 | success=true，run_status=done，failed_step=null |

---

## §2 运行环境与配置（白名单脱敏）

> config_snapshot 由 `scripts/v2_real_run.py` 的白名单机制挑字段（`CONFIG_SNAPSHOT_WHITELIST`）+ 落盘前 `assert_no_sensitive` 双保险，从设计上根绝凭证泄漏。

**生成链路（generate）**

| 项 | 值 |
|---|---|
| provider | 阿里云百炼 OpenAI 兼容网关 |
| api_type | openai |
| model | `deepseek-v4-flash-0731` |
| temperature | 0.3 |
| max_tokens | 8192 |
| enable_thinking | false（关闭时显式下发，避免网关默认开思考致正文为空） |

**评审链路（review）**：enabled=true，复用同 model（`deepseek-v4-flash-0731`），temperature=0.3。

**Prompt 版本（6 个，供审计"这批产物由哪些 prompt 版本产出"）**

| 用途 | 版本 |
|---|---|
| parser（Step 2 IR） | `requirement-parser-v1` |
| tp_generator（Step 3 Phase A） | `test-point-generator-v1` |
| tp_completer（Step 3 Phase B） | `test-point-completer-v1` |
| tc_synthesizer（Step 5） | `test-case-synthesizer-v1` |
| tc_pattern_data（Step 5 复杂正则） | `test-data-pattern-v1` |
| reviewer（Step 7） | `test-case-reviewer-v1` |

**安全声明**：本节**不含 api_key / base_url / Authorization / token**（仅记录 provider、model、temperature、max_tokens、enable_thinking、prompt_version 等非敏感参数）。

---

## §3 全链路执行总览

| 指标 | 值 |
|---|---|
| 总耗时 | 566148 ms（约 9 分 26 秒） |
| 总 LLM 调用数 | 157 |
| RequirementItem | 28 |
| TestPoint 合计 | 174（127 LLM + 47 STRATEGY） |
| TestCase 合计 | 174（1:1 派生自 TestPoint） |
| CoverageObligation | 25 |
| 最终状态 | DONE（Runtime 独家收尾，counts 聚合写回） |

---

## §4 六阶段明细

| 阶段 | 耗时(ms) | LLM calls | 产物计数 |
|---|---|---|---|
| ir（Step 2） | 38452 | 0 | items=28（IR 校验：去重 0 / 低置信 0 / 分段 1） |
| testpoints（Step 3） | 133737 | 29 | LLM points=127（Phase A=119 + Phase B=8） |
| strategy（Step 4） | 1827 | 0 | strategy points=47 + obligations=25（boundary=3 / equivalence=9 / permission=13） |
| testcases（Step 5） | 347883 | 128 | cases=174（validated=174 / failed=0；127 LLM 合成 + 47 代码模板） |
| review（Step 7） | 42153 | 0 | reviewed=174 |
| optimizer（Step 8） | 855 | 0 | archived=24 |

> 阶段顺序严格为 ir → testpoints → strategy → testcases → review → optimizer（与 `core/v2/runtime.py::STEP_ORDER` 一致）；每阶段均 success=true、duration_ms>0、artifact_ids 非空。
> 耗时大头在 testcases（约 5.8 分钟，128 次 LLM 合成调用），符合"1 TestPoint → 1 TestCase 逐条 LLM 合成"的设计。

---

## §5 质量指标（Review 6 维）

**6 维分数**

| 维度 | 分数 | 来源 |
|---|---|---|
| coverage | 100.0 | 硬（代码算，双指标） |
| accuracy | 78.0 | 软（LLM） |
| executability | 89.2 | 加权（结构 0.4 + 语义 0.6） |
| consistency | 100.0 | 硬（代码算） |
| missing_risk | 72.0 | 软（LLM） |
| duplication | 76.44 | 硬（代码算，两级去重检测） |
| **overall** | **≈85.9** | 6 维均值 |

**coverage 双指标（严格分离，不合成）**

| 指标 | 值 | 性质 |
|---|---|---|
| strategy_obligation_coverage | 1.0 | 硬指标（每个 obligation 都派生了 TestPoint 并登记回链） |
| requirement_item_coverage | 1.0 | 软指标（最终合流后：每个 item 至少被一个 TP 引用） |
| uncovered_item_ids | []（空） | 无未覆盖项 |

> 注：Step 4 策略引擎单独跑完的**中间态** item_coverage=0.61（仅 47 个 strategy points 覆盖时）；Step 3 的 127 个 LLM points 合流后，最终 requirement_item_coverage 达 1.0。这印证了 PROGRESS §5.6 的设计——纯功能类 item 由 LLM 覆盖、字段/权限类由 strategy 硬兜底。

**executability 明细（加权）**：structural_score=100.0 × 0.4 + semantic_score=82.0 × 0.6 = 89.2。

**findings**：共 61 条（auto_fixable=44）；按维度分布：duplication=44 / accuracy=6 / executability=6 / missing_risk=5。

---

## §6 Optimizer 去重结果（Step 8）

| 指标 | 值 |
|---|---|
| processed_findings | 44（消费 Review 的 duplication findings） |
| archived_cases | 24（状态转 ARCHIVED，保留审计不物理删除） |
| skipped_findings | 20 |
| skip_reasons | already_resolved=20（canonical pair 去重后已解决） |
| actions_count | 24 |

> 说明：44 条 duplication finding 经 canonicalize pairs（min_id/max_id 唯一化）后，24 对执行归档、20 条因另一边已归档而 skip（already_resolved），无"两边都被归档"的 P0 坑。

---

## §7 验收结论（引用 Step 10.6，不重复跑）

| 验收项 | 结果 |
|---|---|
| 22 条门槛（`python scripts/v2_step10_verify.py --with-real-llm`） | 22 PASS / 0 FAIL / 0 SKIP，exit 0 |
| 全量 pytest | 980 passed + 3 skipped（real_llm marker 默认 skip；启用后 983 passed） |
| ruff check . | All checks passed |
| ruff format --check . | 148 files already formatted |
| schema_version | 10 |

> 门槛 7-9（真实 LLM 调用成功 / 真实数据入库 / Step 2→8 真实跑通）由本文档 + 本地 `output/v2_real_run_*.json` + `data/data_v2.db` 佐证；`tests/test_step10_real_llm.py`（real_llm marker）在 `V2_RUN_REAL_LLM=1` 时读取该产物断言，本地验收用、非永久 CI 资产。

---

## §8 脱敏与安全声明（用户 P0 清单）

| 类别 | 处理 |
|---|---|
| run_id / doc_id / version_id（ULID） | **保留**（随机标识、审计追溯必需、无隐私） |
| API Key / Authorization / token / base_url | **绝不出现**（config 仅白名单：provider/model/temperature/max_tokens/enable_thinking/prompt_versions） |
| 需求正文 / 字段明细 | **脱敏**：仅引用 `examples/v2_sample_requirement.md` 路径 + 场景类型概述，不摘录正文 |
| 用户敏感信息（user_id ULID） | **脱敏**：仅记 CLI 默认用户名 `cli_real_run`（代码公开默认值），不记 user_id ULID |
| output JSON 全文 | **不粘贴**：仅提炼核心指标/关键字段为表格/列表 |

- 文档落盘前经 grep 自检：`api_key|authorization|bearer|token|sk-[A-Za-z0-9]{20}` 无真实凭证命中（"不含 api_key"等说明性文字除外）。
- 真实 `output/v2_real_run_*.json` 与 `data/data_v2.db` 留在 gitignore 路径，**不提交仓库**。

---

## §9 观察与已知限制（诚实记录，不做路线评审）

1. **软分偏低仅记录不评判**：accuracy=78 / missing_risk=72 为 LLM 软分，相对硬指标（coverage/consistency=100）偏低；质量调优属 Step 11+（真实 LLM Quality Evaluation）范畴，本文档只如实记录单次实测值，不下质量结论。
2. **单次样本无统计代表性**：本记录来自 1 次真实运行（单需求、样本量=1），LLM 软分有波动，不代表稳定基线；Benchmark / 自动评测留 Step 12。
3. **Web 同步 timeout 为 MVP 已知限制**：本次经 CLI 权威路径跑通（约 9.5 分钟）；Web `POST /api/v2/runs` 同步执行，超长链路受部署环境 HTTP timeout 限制（PROGRESS §9.3-3），异步化留未来。
4. **Step 10 正式收尾，不提前进入 Step 11**：本文档标志 Step 10「V2 Runtime + Productization」全部完成；后续须先按 PROGRESS §9.6 做「V2 中后期路线评审」（A-F 问题）再定 Step 11~13 顺序，该评审由用户另开一轮讨论，不在 10.7 内触发。
