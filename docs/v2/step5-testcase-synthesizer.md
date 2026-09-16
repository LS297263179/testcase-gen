# Step 5 设计文档：测试用例合成引擎（Test Case Synthesizer）

> 对应 `docs/v2/PROGRESS.md` §5.7 与蓝图 Step 5。
> 本文档是 Step 5 的**单一设计真相源**，新会话接手 Step 6 前建议先读本文 + `step4-strategy-engine.md`。

---

## 0. 一句话

**Step 5 = 测试点 → 测试用例的合成引擎**：消费 Step 3+4 合流后的 `TestPoint[]`（LLM + STRATEGY 两种 provenance），经 **TestDataPlanner**（Code-first + LLM fallback）产出结构化 `DataPlan`，再由 **TestCase Synthesizer**（strategy 走代码模板 / LLM 走 LLM 合成）产出完整 `TestCase`（steps/expected/precondition），Validator 先修复后判定，**fingerprint 身份与 content_hash 内容分离**，obligation coverage 通过 TestPoint 间接继承**不双写**。

---

## 1. 定位与边界

### 1.1 在 V2 蓝图中的位置

```
Step 2 IR ─┬─→ Step 3 TestPoint Generator (LLM 语义派生)  ─┐
           │                                              ├─→ 合流 test_points 表
           └─→ Step 4 策略引擎 (代码派生 Obligation→TP) ──┘
                                                          ↓
                                          Step 5 Test Case Synthesizer
                                          （TestDataPlanner → Synthesizer → Validator）
                                                          ↓
                                                    test_cases 表
```

**Step 3/4/5 职责链**（用户总结）：
- Step 3 回答"**应该测什么？**"（LLM 语义测试点、跨项联动、风险场景）
- Step 4 回答"**哪些确定性测试必须覆盖？**"（Code 边界/等价类/权限 → CoverageObligation）
- Step 5 回答"**这个测试具体怎么执行？**"（Code+LLM 测试数据/步骤/预期/前置条件）

### 1.2 严格不做（Step 5 边界外）

| 项 | 归属 |
|---|---|
| 6 维 AI Reviewer 评审 | Step 7 |
| 精确 + 语义双重去重 | Step 8（Step 5 只做 fingerprint 严格去重） |
| 人工编辑/确认闭环（EDITED→RE_REVIEW） | Step 9 |
| Preference Learning | Step 10 |
| Traceability 追溯链查询 / 变更影响分析 | Step 6 |
| Web API / 前端 / Excel 导出 | Step 13 |
| 改 V1 `core/generator.py` / `web/generate.py` / `data.db` | 永不做 |

---

## 2. 7 条核心原则（用户冻结，贯穿全 Step）

1. **1 TestPoint → 1 TestCase**（无论 LLM 还是 STRATEGY provenance）
2. **TestDataGenerator：Code-first + LLM fallback**（80% 代码确定性，20% 复杂正则 LLM 兜底）
3. **TestStep：LLM generation + Code validation**（LLM 自由生成，Validator 强制修正）
4. **fingerprint：基于 TestPoint 来源身份**，不依赖 title/steps 等可编辑内容；**身份指纹与内容哈希分离**
5. **obligation coverage：Step 4 的 `obligation_coverage` 关系表是唯一事实源**，TestCase 通过 TestPoint 间接继承，**不双写**
6. **Validator：先修复可修复项，修不了再 VALIDATION_FAILED + 保存 validation_errors**
7. **V1 运行时（`core/db.py`、`web/`、`data.db`）完全不修改**

---

## 3. 架构数据流

```
TestPoint[]（Step 3+4 合流，LLM + STRATEGY）
      │
      ↓
TestDataPlanner ─────────────────────────────────┐
      │  数据来源优先级（Code Generator）：        │
      │    1 FieldSpec.example  2 FieldSpec.default│
      │    3 strategy_params.value  4 enum_values  │
      │    5 data_type 内置样例库                   │
      │  LLM Fallback（复杂正则）：                 │
      │    └── 代码 re.fullmatch 验证 ★（不符→retry/fail）
      ↓                                            │
DataPlan[]（结构化，持久化到 TestCase.data_plan）  │
      │                                            │
      ├── STRATEGY TP → synthesize_strategy_template（代码模板，不调 LLM）
      └── LLM TP      → synthesize_with_llm（LLM 合成自然语言步骤）◄┘
      ↓
TestCase[]（status=GENERATED）
      ↓
Validator（先修复 → 后判定）
  可修复：seq 重排 / 空 action step 丢弃 / type·priority 枚举兜底 / module 覆写 / fingerprint+content_hash 计算
  不可修复：steps=[] / title 空 / expected 空 / test_point_ids 空
      ├── 修复成功 → status=VALIDATED
      └── 不可修复 → status=VALIDATION_FAILED + validation_errors
      ↓
SQLite（test_cases 表，UNIQUE(fingerprint) 幂等 upsert 复用旧 ULID）
```

---

## 4. Schema / DDL 变更（schema_version 4 → 5）

### 4.1 新增枚举（`core/schemas/common.py`）

```python
class GenerationMode(StrEnum):
    """TestCase 生成方式（参与 fingerprint 身份）"""
    CODE = "code"        # 纯代码生成（strategy TP + 代码数据 + 模板 steps）
    LLM = "llm"          # LLM 生成（LLM TP + LLM steps）
    HYBRID = "hybrid"    # 混合（strategy TP + LLM 兜底数据）
```

### 4.2 新增 DataPlanItem（`core/schemas/testcase.py`）

```python
class DataPlanItem(BaseModel):
    """测试数据计划项 - TestDataPlanner 产物，持久化便于审计与未来 Playwright/API 复用"""
    field: str            # 字段名
    strategy: str         # boundary / equivalence / permission / ...
    value: str | int | float | bool | None   # 具体测试数据
    source: str           # example / default / strategy_params / enum / builtin / llm
    generator: str        # code / llm
    expected_valid: bool  # 该数据预期合法还是非法（用于 expected 文案）
```

### 4.3 TestCase 新增 4 字段

```python
generation_mode: GenerationMode = GenerationMode.LLM   # 参与 fingerprint 身份
content_hash: str | None = None                        # 内容哈希（与 fingerprint 分离）
validation_errors: list[str] = []                      # VALIDATION_FAILED 时保存不可修复原因
data_plan: list[DataPlanItem] = []                     # 结构化测试数据计划
```

### 4.4 DDL 升级（`core/v2/ddl.py`，SCHEMA_VERSION=5）

- `test_cases` 新增列：`generation_mode TEXT NOT NULL DEFAULT 'llm'`、`content_hash TEXT`、`validation_errors_json TEXT NOT NULL DEFAULT '[]'`、`data_plan_json TEXT NOT NULL DEFAULT '[]'`
- `idx_cases_fp`（普通索引）升级为 `CREATE UNIQUE INDEX ux_test_cases_fingerprint ON test_cases(fingerprint)`
- `create_v2_schema()` 新增 v4→v5 自动升级分支：ALTER TABLE 补 4 列 + 已有行补算 content_hash（总是可行）与 fingerprint（仅 test_point_ids 非空时）+ 索引升级

---

## 5. TestDataPlanner（`core/v2/test_data_planner.py`）

### 5.1 分工边界（★ 用户核心设计）

- **STRATEGY TestPoint**：Step 4 只产出抽象类标识（如 `class="invalid_pattern"`），本模块据 `obligation.params + strategy_params + FieldSpec` 生成**具体测试数据**。
- **LLM TestPoint**：无确定性字段数据需求，DataPlan 留空；LLM 合成器直接消费 TestPoint + RequirementItem context（已含字段 example/default）。

### 5.2 数据来源优先级（Code Generator，用户拍板）

```
1. FieldSpec.example      （明确样例，最优先）
2. FieldSpec.default      （默认值）
3. strategy_params.value  （boundary/enum 已带具体值）
4. enum_values            （从 FieldSpec 取合法枚举）
5. data_type 内置样例库    （email/phone/url/id_card 合法+非法样例；含校验位正确的身份证）
6. 必要时 LLM             （复杂正则 valid_pattern/invalid_pattern）
```

### 5.3 各 strategy_params class → 具体数据

| class / boundary_type | 数据来源 | expected_valid |
|---|---|---|
| boundary `min/max/min+1/max-1` | strategy_params.value（length 类构造对应长度字符串） | True |
| boundary `min-1/max+1` | strategy_params.value | False |
| `valid_enum_value` | strategy_params.value | True |
| `invalid_enum_value` | 造不在 enum_values 集合的值 | False |
| `required_provided` / `non_null_value` | example→default→enum→builtin | True |
| `required_missing` | `""` | False |
| `null_value` | `None` | False |
| `unique_new_value` / `unique_duplicate_value` | example→builtin + precondition 前置提示 | True / False |
| `valid_format` / `invalid_format` | 内置样例库（按 data_type） | True / False |
| `valid_pattern` / `invalid_pattern` | example→代码启发式→**LLM 兜底** | True / False |
| permission | role/resource/action 组装 + precondition 角色登录 | allowed |

### 5.4 复杂正则 LLM 兜底 + re.fullmatch 验证（★ 门槛 14）

**原则**：LLM 可以生成测试数据，但**不能自证正确**——代码来验证。

```
LLM 生成样例 → re.fullmatch(pattern, sample)
  ├─ valid_pattern：须完全匹配 → ✅ 接受 / ❌ 拒绝
  └─ invalid_pattern：须不匹配   → ✅ 接受 / ❌ 拒绝
不符合预期 → retry（≤3 次）→ 全失败则 value=None + 记 issue（绝不入库未验证数据）
```

代码启发式（`_heuristic_pattern_sample`）先覆盖简单正则（`\d{n}` / `[A-Za-z]` 等），失败才落 LLM。非法正则（`re.error`）无法验证 → 返回 False（不接受）。

---

## 6. TestCase Synthesizer（`core/v2/tc_generator.py`）

### 6.1 两条合成路径

- **STRATEGY TestPoint → `synthesize_strategy_template`（纯代码模板，不调 LLM）**
  Step 4 已把义务展开成原子级 TestPoint，其步骤是确定的（"在字段 X 输入值 Y，预期接受/拒绝"）。
  模板合成比 LLM 更可复现、无幻觉、无 API 成本，契合 V2「确定性关注点代码化」原则。
  - boundary → 2 步（输入边界值 + 提交校验）；equivalence → 2 步；permission → 1 步（角色执行操作）
  - type 由 technique 映射：BOUNDARY_VALUE→BOUNDARY / EQUIVALENCE_CLASS→EQUIVALENCE / PERMISSION_MATRIX→PERMISSION
- **LLM TestPoint → `synthesize_with_llm`（调 LLM 合成自然语言步骤）**
  输入：TestPoint + RequirementItem context + DataPlan；输出：完整 steps[]/expected/precondition/type/priority。
  复用 Step 2 `parser.extract_json` 鲁棒提取；module 由代码覆写为 TestPoint.module。

### 6.2 generation_mode 判定（plan D6）

| 场景 | generation_mode |
|---|---|
| strategy TP + 纯代码数据 + 模板 steps | CODE |
| strategy TP + LLM 兜底数据（复杂正则） | HYBRID |
| LLM TP + LLM steps | LLM |

generation_mode 参与 fingerprint → 同一 TestPoint 用不同 mode 生成视为不同身份。

---

## 7. Validator（`core/v2/tc_validator.py`，先修复后判定）

### 7.1 可修复项（代码直接修，GENERATED → VALIDATED）

- steps 的 seq 不连续 → 重排 1..N（忽略 LLM 给的 seq）
- action 为空的 step → 丢弃 + 记 issue
- type 非法 → 按 dimension 兜底推断（再退 FUNCTIONAL）
- priority 非法 → 沿用 TestPoint.priority
- module → 覆写为 TestPoint.module（不接受 LLM 改写）
- fingerprint / content_hash → 代码计算

### 7.2 不可修复项（GENERATED → VALIDATION_FAILED + validation_errors）

- steps 全空（无有效步骤）
- title 空 / expected 空
- test_point_ids 空（1:1 派生应关联 1 个 TestPoint）

### 7.3 Step 5 硬性覆写

- `test_point_ids = [tp.id]`（1:1）；`module = tp.module`
- `provenance`：mode=LLM→LLM；mode=CODE/HYBRID→STRATEGY
- `confidence_level`：CODE→HIGH（确定性代码合成）；LLM/HYBRID→MEDIUM
- `status`：VALIDATED / VALIDATION_FAILED（由修复结果决定）

> VALIDATION_FAILED 的用例**仍持久化**（含 validation_errors），留给 Step 7 Reviewer / Retry 处理；其 fingerprint 照常计算（身份不依赖内容）。

---

## 8. 双指纹：fingerprint（身份）/ content_hash（内容）★ 用户核心修订

### 8.1 为什么分离

fingerprint 标识"**这是哪一个 TestCase**"（身份），而非"当前文本长什么样"（内容）。
若 title 进入 fingerprint，人工修改标题后旧 fp ≠ 新 fp → DB 认为是两个 TestCase，但实际应是**同一 TestCase 的不同 revision**（Step 9 场景）。故身份与内容必须分离。

### 8.2 公式（`core/v2/fingerprint.py`）

```python
# 身份（不含 title/steps 等可编辑内容；不含 run_id → 跨 run 幂等）
fingerprint = "tc_" + sha256(version_id | generation_mode | ",".join(sorted(test_point_ids)))[:32]

# 内容（追踪内容是否变化，Step 9 Revision / Preference Learning 数据源）
content_hash = "tch_" + sha256(
    normalize(title) | normalize(precondition) | normalize(render_steps_text())
    | normalize(expected) | type | priority
)[:32]
```

### 8.3 幂等 upsert（`repository.save_test_case`）

- 入库前兜底计算 content_hash（总是可算）与 fingerprint（**仅 test_point_ids 非空时**）
- 按 fingerprint 查旧行：存在且 id 不同 → 复用旧 ULID（保证下游 test_case_points 不断链 + UNIQUE 不破）
- **迁移用例 fingerprint 留 NULL**：V1 迁移用例无 test_point_ids，若强算会得到 `sha256(version_id|mode|"")` 的相同指纹 → 同 run 下多个迁移用例 UNIQUE 碰撞坦缩。留 NULL（SQLite UNIQUE 允许多个 NULL）。

---

## 9. obligation coverage 不双写（★ 用户核心修订）

Step 4 已确定 `obligation_coverage` 是覆盖关系**唯一事实源**（obligation → TestPoint）。
Step 5 **不再**维护 obligation → TestCase 的独立关系，避免双重事实源不一致（obligation→TP ✅ 但 obligation→TC ❌）。

**追溯路径**（间接继承）：
```
TestCase → test_case_points → TestPoint.obligation_id → CoverageObligation
```

若未来（Step 6/7）需要 DB 层直接 `obligation → testcase` 查询，建**派生 SQL View**（如 `obligation_test_cases`），View 非事实源。Step 5 暂未建 View。

---

## 10. 交付文件清单

### 10.1 新增（`core/v2/`）

| 文件 | 职责 |
|---|---|
| `tc_prompts.py` | `test-case-synthesizer-v1`（用例合成）+ `test-data-pattern-v1`（复杂正则数据）+ 版本号 |
| `test_data_planner.py` | 数据来源优先级 6 级 + Code Generator + LLM Fallback（re.fullmatch 验证）→ DataPlan[] |
| `tc_generator.py` | `synthesize_strategy_template`（代码模板）+ `synthesize_with_llm`（LLM 合成） |
| `tc_validator.py` | 修复策略 + 硬性覆写 + fingerprint/content_hash + validation_errors + 去重 |
| `tc_orchestrator.py` | `synthesize_test_cases`（复用 Run）+ `generate_test_cases_full`（Step 3+4+5 一站式） |

### 10.2 升级

| 文件 | 变更 |
|---|---|
| `core/schemas/common.py` | 新增 `GenerationMode(StrEnum)` |
| `core/schemas/testcase.py` | 新增 `DataPlanItem` + TestCase 4 字段 |
| `core/schemas/__init__.py` | 导出 `GenerationMode` / `DataPlanItem` |
| `core/v2/fingerprint.py` | 新增 `compute_testcase_fingerprint` + `compute_testcase_content_hash` |
| `core/v2/ddl.py` | SCHEMA_VERSION=5 + test_cases 补 4 列 + UNIQUE(fingerprint) + v4→v5 升级分支 |
| `core/v2/repository.py` | `save_test_case` fingerprint 幂等 upsert + 序列化新字段 + `get_test_case_by_fingerprint` / `list_test_cases_by_version` |

### 10.3 测试（`tests/`，全 mock）

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `test_test_data_planner.py` | 44 | 数据来源优先级、各 strategy 数据、LLM 兜底 + re.fullmatch、边界情况 |
| `test_tc_generator.py` | 18 | 模板合成、LLM 合成、generation_mode、_render_value |
| `test_tc_validator.py` | 31 | 可修复/不可修复、双指纹分离、状态机、provenance/confidence、去重 |
| `test_tc_orchestrator.py` | 21 | 端到端、Run 复用、幂等、关联表、GenerationConfig 审计、一站式 |
| `test_step5_acceptance.py` | 21 | 18 条门槛逐条对应 |
| **合计** | **135** | |

---

## 11. 18 条验收门槛（全部 PASSED）

| # | 门槛 |
|---|---|
| 1 | 每个 TestCase 的 test_point_ids 全部指向真实存在的 TestPoint |
| 2 | LLM TP → 1 TC；Strategy TP → 1 TC（含真实测试数据） |
| 3 | steps 非空、seq 连续、action 非空（Validator 修复或判定 FAILED） |
| 4 | type 枚举合法（LLM 乱写被代码兜底） |
| 5 | fingerprint 非空、格式正确（tc_+32hex）、UNIQUE 约束生效 |
| 6 | 幂等：同 version 重跑不产生重复（fingerprint upsert 复用旧 ULID） |
| 7 | Run 复用（不新建）+ 状态机终态 DONE + counts.cases 累加 |
| 8 | TestCase 状态机 GENERATED→VALIDATED（修复成功）/ VALIDATION_FAILED（不可修复） |
| 9 | test_case_points M:N 写入正确；删 TestCase 不级联删 TestPoint |
| 10 | TestDataPlanner 覆盖数据来源优先级 + 复杂正则 LLM 兜底 |
| 11 | V1 零回归 + schema_version=5 |
| 12 | 一站式入口 generate_test_cases_full 串联 Step 3+4+5 |
| 13 | TestDataPlanner 输出数据通过对应 FieldSpec 约束验证（enum∈集合 / boundary∈[min,max]） |
| 14 | LLM 复杂正则数据经 re.fullmatch 验证，不符合预期则 retry/fail（绝不入库未验证数据） |
| 15 | 同一 TestPoint 在同一 Version 下只对应一个 TestCase（fingerprint UNIQUE 保证） |
| 16 | 修改 title/steps 不改变 identity fingerprint（身份/内容分离验证） |
| 17 | TestCase 可通过 TestPoint 反查 CoverageObligation（间接继承，obligation_coverage 不含 TestCase 目标） |
| 18 | VALIDATION_FAILED 保存明确 validation_errors（持久化后仍可读出） |

---

## 12. 关键决策记录（ADR 风格）

### D1. 为什么 fingerprint 不含 title/steps？
身份 vs 内容分离。人工修改 title 后身份不变 → 同一 TestCase 不同 revision（Step 9 基础）。内容变化由 content_hash 单独追踪。见 §8。

### D2. 为什么 obligation coverage 不延伸到 TestCase 层？
避免双重事实源不一致。TestCase 通过 TestPoint 间接继承。需直接查询时用 SQL View（派生，非事实源）。见 §9。

### D3. 为什么引入 DataPlan 中间层并持久化？
职责拆分：TestPoint → DataPlan → TestCase，而非 TestDataGenerator 直接改 TestCase。便于审计数据来源、未来 Playwright/API/参数化执行复用、Step 9 区分代码/LLM 数据。

### D4. 为什么 LLM 复杂正则数据必须代码验证？
LLM 可以生成测试数据，但不能自证正确。`re.fullmatch` 验证 + retry/fail，与 V2「LLM 产物过 Validator」原则一致。见 §5.4。

### D5. 为什么 Validator 区分可修复 vs 不可修复？
可修复项代码直接修（避免浪费 LLM 重生成）；不可修复项保存 validation_errors 留给 Reviewer/Retry。对齐用户反馈"失败不能简单等同于模型错误"。见 §7。

### D6. 为什么 strategy TestPoint 用代码模板而非 LLM 合成步骤？
Step 4 已把义务展开成原子级 TestPoint，步骤是确定的。模板合成可复现、无幻觉、无 API 成本，契合「确定性关注点代码化」。LLM 合成只用于语义型的 LLM TestPoint。

### D7. 为什么迁移用例 fingerprint 留 NULL？
V1 迁移用例无 test_point_ids，强算会导致同 run 下多个迁移用例 UNIQUE 碰撞坦缩（实测曾触发 TC_001 被 TC_002 覆盖）。留 NULL（SQLite UNIQUE 允许多 NULL），Step 5 生成的用例必有 test_point_ids（1:1）。见 §8.3。

---

## 13. 诚实边界（mock 无法确定性验证）

1. **LLM 合成 steps 质量**（可执行性/业务贴合度）靠 Prompt 约束，需真实 API 抽查。
2. **复杂正则 LLM 兜底样例多样性**：mock 只验证"代码会 re.fullmatch 验证 + retry/fail"，不验证"LLM 真能生成多样合法样例"。
3. **content_hash 实际用途**：Step 9 Revision 才真正消费，Step 5 只计算并持久化。
4. **纯代码模板合成的 strategy 用例文案较机械**：可读性优化留待 Step 7 Reviewer / Step 9 人工编辑。

---

## 14. 验证命令（PowerShell）

```powershell
# 全量测试（期望 632 passed = Step 4 后 497 + Step 5 新增 135）
.\.venv\Scripts\python.exe -m pytest -q

# Step 5 专项（期望 135 passed = 44+18+31+21+21）
.\.venv\Scripts\python.exe -m pytest tests/test_test_data_planner.py tests/test_tc_generator.py tests/test_tc_validator.py tests/test_tc_orchestrator.py tests/test_step5_acceptance.py -v

# 代码质量
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

---

## 15. 下一步 = Step 6「Traceability 追溯链 + 变更影响分析」

见 `docs/v2/PROGRESS.md` §9。开工前需与用户讨论确认：
- 追溯链查询 API 形态（正向 item→TP→TC / 反向 TC→TP→item→version）
- 变更影响分析的 diff 策略（RequirementItem 变更如何判定）
- 受影响资产的处置（标记需复审 vs 自动重生成 vs 仅报告，与 Step 9 边界）
- 是否建派生 SQL View（obligation_test_cases 等）
- 跨版本追溯（旧版本 TestCase 在新版本下的状态标记）
