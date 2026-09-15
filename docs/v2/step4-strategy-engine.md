# Step 4 设计文档：测试策略引擎（Strategy Engine）

> 对应 `docs/v2/PROGRESS.md` §5.6 与蓝图 Step 4。
> 本文档是 Step 4 的**单一设计真相源**，新会话接手 Step 5 前建议先读本文 + `step3-testpoint-generator.md`。

---

## 0. 一句话

**Step 4 = 代码侧的确定性测试设计引擎**：消费 Step 2 IR 的 `FieldSpec / PermissionRule`，用**纯代码规则**（不调 LLM）派生 `CoverageObligation`（必须覆盖的测试设计要求）+ `TestPoint(provenance=STRATEGY)`（具体原子测试点），硬兜底覆盖率，与 Step 3 LLM 派生合流写入同一张 `test_points` 表。

---

## 1. 定位与边界

### 1.1 层次结构（用户补充的核心设计）

```
需求规则（RequirementItem.fields / permissions）
   ↓
CoverageObligation（"必须覆盖的测试设计要求"，不是一条测试点）
   ↓
具体 TestPoint（原子测试点，例如 age=0 / age=1 / ...）
```

**关键语义澄清**：Obligation 是"设计要求"，TestPoint 是"具体点"。一个 Obligation 可派生多个 TestPoint（1:N）。

### 1.2 在 V2 蓝图中的位置

```
Step 2 IR ─┬─→ Step 3 TestPoint Generator (LLM 语义派生)  ─┐
           │                                                ├─→ 合流 test_points 表 ─→ Step 5 用例合成
           └─→ Step 4 策略引擎 (代码派生 Obligation → TestPoint) ─┘
```

Step 3 与 Step 4 **写入同一张 `test_points` 表**，用 `provenance` + `generation_scope` 区分：
- Step 3：`provenance=LLM`，`generation_scope=item|cross_item`，`technique=None`，`obligation_id=None`
- Step 4：`provenance=STRATEGY`，`generation_scope=STRATEGY`，`technique!=None`，`obligation_id!=None`

### 1.3 严格不做（Step 4 边界外）

| 项 | 归属 |
|---|---|
| DECISION_TABLE / STATE_TRANSITION / ERROR_GUESSING / SCENARIO | 延后 |
| BusinessRule 表达式解析 | 延后（DECISION_TABLE 实施时） |
| TestPoint → TestCase 合成 | Step 5 |
| 真实测试数据生成（如非法邮箱 "abc"） | Step 5 TestDataGenerator |
| 变更影响分析、追溯链渲染 | Step 6 |
| AI Reviewer 6 维评审 | Step 7 |
| Web API / 前端 | Step 8+ |
| 调 LLM | 永不做（Step 4 是纯代码策略引擎） |

---

## 2. 首批三类技术（用户拍板）

### 2.1 BOUNDARY_VALUE（边界值）

**输入**：`FieldSpec.min_value / max_value / min_length / max_length`

**派生规则**（首批步长固定=1，float precision 留待迭代）：
- min + max 都有 → 6 点：`min-1, min, min+1, max-1, max, max+1`
- 只有 min → 3 点：`min-1, min, min+1`
- 只有 max → 3 点：`max-1, max, max+1`
- 边界重合（min=1,max=2）→ 去重后点数减少
- 数值边界与长度边界**分开派生**两个独立 obligation（语义不同）

**合理性判断**（用户补充，第一版简单规则）：
- 当 `min ≤ 0` 且 `boundary_type=min_minus_1` 时，min-1 可能为负数，对年龄/数量/长度等语义字段可能不合理
- 处理：加 `warn="negative_value_may_be_invalid"` 标记到 `strategy_params` + description 里加提示"需人工确认"
- 复杂业务语义判断（data_type/unit/是否允许负数）留待迭代

**obligation.params**：`{"field","label","kind":"value"|"length","min","max","points":[...]}`

**TestPoint.strategy_params**：`{"boundary_type":"min_minus_1"|"min"|"min_plus_1"|"max_minus_1"|"max"|"max_plus_1", "value":<数值>, "kind":"value"|"length", "warn"?:...}`

### 2.2 EQUIVALENCE_CLASS（等价类）

**输入**：`FieldSpec` 的 6 种属性（每种独立派生 obligation）

| 属性 | 派生 TestPoint | strategy_params.class |
|---|---|---|
| `enum_values` 非空 | N 合法（每枚举值）+ 1 非法 | `valid_enum_value` / `invalid_enum_value` |
| `pattern` 非空 | 1 合法 + 1 非法 | `valid_pattern` / `invalid_pattern` |
| `required=True` | 1 合法 + 1 非法 | `required_provided` / `required_missing` |
| `nullable=False` | 1 合法 + 1 非法 | `non_null_value` / `null_value` |
| `unique=True` | 1 合法 + 1 非法 | `unique_new_value` / `unique_duplicate_value` |
| `data_type ∈ {email,phone,url,id_card}` | 1 合法 + 1 非法 | `valid_format` / `invalid_format` |

**★ 关键设计（用户补充）**：Strategy Engine **不生成真实测试数据**。
- 等价类只产出**抽象类标识**（如 `class="invalid_pattern"`）
- 具体数据（如非法邮箱 "abc"、合法手机号 "13800138000"）由 **Step 5 TestDataGenerator** 生成
- 分层更干净：Strategy 负责"测什么类"，TestCase 负责"用什么数据测"

**obligation.params**：`{"field","label","kind":"enum"|"pattern"|"required"|"nullable"|"unique"|"data_type_format", ...}`（不含具体数据样例）

### 2.3 PERMISSION_MATRIX（权限矩阵）

**输入**：`PermissionRule.role × resource × action → allowed/denied`

**派生规则**：1 PermissionRule → 1 obligation → 1 TestPoint（**1:1**，用户确认合理）

**condition 处理**（plan Q3）：首批不派生额外 TestPoint（condition 是自然语言，代码难以规则化）；仅在 description 里提及，留给 Step 5 LLM 处理。

**obligation.params**：`{"role","resource","action","allowed","condition"}`

**TestPoint.strategy_params**：`{"role","resource","action","allowed"}`（**不含 condition**，避免自然语言参与 fingerprint 导致不稳定）

---

## 3. Step 4 硬性约束（用户补充，Validator 代码强制）

| 字段 | 约束 |
|---|---|
| `provenance` | 必须 = `STRATEGY` |
| `generation_scope` | 必须 = `STRATEGY` |
| `technique` | 必须 != `None`（对应 obligation.technique） |
| `obligation_id` | 必须 != `None`（回链 obligation.id） |
| `module` | 来源 `item.module` |
| `subcategory` | 按 technique 命名："边界值" / "等价类" / "权限矩阵" |
| `dimension` | BOUNDARY_VALUE→BOUNDARY；EQUIVALENCE_CLASS→EQUIVALENCE；PERMISSION_MATRIX→PERMISSION |
| `priority` | 来源 `item.priority_hint`，缺省 P1 |
| `item_ids` | `[item.id]`（strategy TestPoint 天然单 item 追溯） |
| `status` | `DRAFT` |
| `strategy_params` | 必须非空（结构化参数，fingerprint 区分依据） |
| `fingerprint` | 必须非空（Repository 入库前兜底计算） |

---

## 4. strategy_params + fingerprint（用户补充的核心设计）

### 4.1 为什么需要 strategy_params

**问题**：同一 obligation 派生多个 TestPoint（1:N），若 fingerprint 只靠 title 区分不够可靠。
例如边界值 obligation 派生 6 个点，若 title 都是"验证年龄边界"，则 `title + target` 无法区分。

**解决**：给 Strategy TestPoint 增加 `strategy_params: dict` 字段，结构化描述每个点的具体身份：
```json
{"boundary_type": "min_minus_1", "value": 0}
{"boundary_type": "min", "value": 1}
```

### 4.2 fingerprint 公式

```python
fingerprint = "tp_" + sha256(
    "|".join([
        version_id or "",
        "strategy",                              # 固定 generation_scope
        obligation_id,
        technique,
        canonical_strategy_params(strategy_params),
    ])
).hexdigest()[:32]
```

`canonical_strategy_params`：`json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(',',':'))`
- sort_keys 保证字典序一致，不受插入顺序影响
- 紧凑格式减少长度

### 4.3 obligation 幂等（natural key 对齐）

**问题**：obligation.id 是 ULID 每次新生成，若 fingerprint 含 obligation_id，重跑会产生新 TestPoint（不幂等）。

**解决**：obligation 按 **natural key** `(run_id, item_id, technique, target)` 复用旧 id：
- Repository 新增 `get_obligation_by_natural_key(run_id, item_id, technique, target)`
- orchestrator 在持久化 obligation 前查旧行，若存在则复用旧 ULID
- 下游 TestPoint 的 fingerprint（含 obligation_id）稳定 → 幂等重跑不产生重复行

---

## 5. 覆盖率双指标严格分离（用户修订）

### 5.1 Strategy Obligation Coverage（Step 4 内部硬指标）

```
= 被至少一个 strategy TestPoint 覆盖的 obligation 数 / 总 obligation 数
= repo.obligation_coverage_ratio(run_id)
```
**验收门槛：== 1.0**（每个 Step 4 派生的 obligation 至少被 1 个 TestPoint 覆盖）

### 5.2 RequirementItem Coverage（整体软指标）

```
= 被至少一个 TestPoint（LLM 或 strategy）引用的 item 数 / 全部 item 数
```
复用 Step 3 `tp_validator.build_coverage_report`，传入合流后的 `all_points`。

### 5.3 为什么不能混为一谈

纯功能类 RequirementItem（无 FieldSpec / Rule / Permission），例如：
> "用户点击'退出登录'，系统返回登录页"

Step 4 **无法**产生 obligation（没有可代码化的字段/权限），但 Step 3 LLM 完全可能已覆盖：
```
Step 3 LLM → TP-001 退出登录功能
```

所以：
- Strategy Obligation Coverage = 100% ✅（Step 4 自身职责完成）
- RequirementItem Coverage < 100%（正常，纯功能类 item 依赖 Step 3）

两个指标**独立报告、独立验收**，不混为一谈。

### 5.4 覆盖语义的精确界定（结构性 vs 语义性）★关键澄清

**Step 4 的 `Strategy Obligation Coverage == 1.0` 保证的是「结构性覆盖」，而非「语义性覆盖」**：

| 覆盖类型 | 含义 | 谁负责 |
|---|---|---|
| **结构性覆盖**（Step 4 保证） | 每个 obligation 都派生了对应的 Strategy TestPoint，并调用了 `add_coverage()` 登记回链 | Step 4 策略引擎 |
| **语义性覆盖**（Step 4 不保证） | TestPoint 经过语义验证后，真正在测试设计意义上满足 obligation 的要求 | Step 5（TestCase 合成时检验可执行性）+ Step 7（AI Reviewer 6 维评审的 coverage 维度） |

**代码证据**（`orchestrator.py` §7）：`add_coverage` 是「派生即登记」——只要 TestPoint 的 `obligation_id` 回链到某个 obligation，就立即登记，**没有任何语义验证环节**。

```
obligation
  ↓ 代码规则派生（确定性）
generated TestPoint
  ↓ add_coverage() 立即登记（无语义验证）
Coverage = 100%
```

**这是 Step 4 的有意边界，不是缺陷**：
- Step 4 的职责是「用代码确定性地把 obligation 展开为原子 TestPoint」，保证**派生完整性**（每个义务都有对应的点）
- 「TestPoint 是否真的能测到 obligation 要求的点」是**语义判断**，属于 Step 5/7 的职责：
  - **Step 5**（TestCase 合成）：把 TestPoint 变成可执行的 TestCase 时，会检验 TestPoint 的可执行性（例如抽象类标识 `class="invalid_pattern"` 需要 TestDataGenerator 生成真实数据才能执行）
  - **Step 7**（AI Reviewer）：6 维评审中的 `coverage` 维度会做语义层面的检验（TestPoint 是否真正覆盖了义务的设计意图）

**对使用方的提醒**：看到 `Strategy Obligation Coverage == 1.0` 时，应理解为「Step 4 已完成其派生职责」，而非「测试设计已语义完备」。后者需要 Step 5/7 完成后才能断言。

---

## 6. 交付文件清单

### 6.1 新增（`core/v2/strategy/` 包）

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包导出（derive_obligations / apply_strategy_engine / generate_test_points_full / StrategyResult / FullGenerationResult） |
| `boundary.py` | 边界值策略：`derive_boundary_obligations` + `derive_boundary_testpoints`（六点 + strategy_params + min≤0 warn） |
| `equivalence.py` | 等价类策略：`derive_equivalence_obligations` + `derive_equivalence_testpoints`（6 种属性 → 抽象类标识） |
| `permission.py` | 权限矩阵策略：`derive_permission_obligations` + `derive_permission_testpoints`（1:1） |
| `engine.py` | 三策略汇总：`derive_obligations(items, run_id)` |
| `deriver.py` | obligation → TestPoint 分派 + fingerprint 统一计算 + 去重：`derive_testpoints_from_obligations` |
| `orchestrator.py` | 顶层编排：`apply_strategy_engine`（Run 状态机 + 持久化 + add_coverage + 双指标）+ `generate_test_points_full`（Step 3+4 一站式） |

### 6.2 升级（Schema/DDL/Repository）

| 文件 | 变更 |
|---|---|
| `core/schemas/common.py` | `GenerationScope` 新增 `STRATEGY = "strategy"` |
| `core/schemas/testpoint.py` | TestPoint 新增 `strategy_params: dict \| None = None` |
| `core/v2/ddl.py` | `SCHEMA_VERSION=4`；`test_points` 加 `strategy_params_json` 列；`create_v2_schema()` 含 v3→v4 自动升级分支 |
| `core/v2/repository.py` | `save_test_point` fingerprint 兜底区分 LLM/STRATEGY 两种公式；序列化 `strategy_params`；`_row_to_test_point` 反序列化；新增 `get_obligation_by_natural_key` |
| `core/v2/fingerprint.py` | 新增 `compute_strategy_testpoint_fingerprint` + `canonical_strategy_params` |

### 6.3 测试（纯代码，不调 LLM）

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `tests/test_strategy_boundary.py` | 42 | 六点派生、strategy_params、min≤0 warn、异常输入 |
| `tests/test_strategy_equivalence.py` | 29 | 6 种属性、抽象类标识、无真实数据、strategy_params 唯一性 |
| `tests/test_strategy_permission.py` | 15 | 1:1 派生、allow/deny、condition 处理、strategy_params |
| `tests/test_tp_strategy_deriver.py` | 18 | 三策略分派、fingerprint 计算、去重、version_id 注入 |
| `tests/test_strategy_orchestrator.py` | 16 | Run 状态机、双指标、幂等、一站式入口、counts 累加 |
| `tests/test_step4_acceptance.py` | 15 | 12 条主门槛 + 3 条附加 |
| **合计** | **135** | |

---

## 7. 12 条主验收门槛 + 3 条附加（全部 PASSED）

| # | 门槛 | 对应测试 |
|---|---|---|
| 1 | min_value+max_value → BOUNDARY_VALUE obligation + 6 TestPoint | `test_01_boundary_value_six_points` |
| 2 | min_length+max_length → BOUNDARY_VALUE obligation + 6 TestPoint | `test_02_boundary_length_six_points` |
| 3 | enum_values → EQUIVALENCE_CLASS obligation + N+1 TestPoint | `test_03_equivalence_enum_n_plus_1` |
| 4 | required=True → EQUIVALENCE_CLASS obligation（必填 + 空值非法） | `test_04_equivalence_required` |
| 5 | pattern 非空 → EQUIVALENCE_CLASS obligation（抽象类标识，无真实数据） | `test_05_equivalence_pattern` |
| 6 | PermissionRule → PERMISSION_MATRIX obligation + 1:1 TestPoint | `test_06_permission_matrix_one_to_one` |
| 7 | Strategy TestPoint 硬性约束（provenance/scope/technique/obligation_id） | `test_07_strategy_testpoint_hard_constraints` |
| 8 | Strategy Obligation Coverage == 1.0（硬指标） | `test_08_strategy_obligation_coverage_is_one` |
| 9 | fingerprint 幂等（natural key 对齐 obligation.id → TestPoint fingerprint 稳定） | `test_09_fingerprint_idempotent_rerun` |
| 10 | Step 3+4 合流后 generation_scope 三值齐全（item/cross_item/strategy） | `test_10_merged_three_scopes_present` |
| 11 | Run 状态机 STRATEGIZING→DONE + counts 累加 | `test_11_run_state_machine_and_counts` |
| 12 | V1 零回归 + schema_version=4 | `test_12_v1_untouched_and_schema_v4` |
| 附加 A | fingerprint 公式验证（canonical_strategy_params 确定性） | `test_extra_a_fingerprint_formula` + `test_extra_a_canonical_params_deterministic` |
| 附加 B | 覆盖率双指标严格分离（硬 1.0 ≠ 软 0.5） | `test_extra_b_dual_coverage_metrics_separated` |

---

## 8. 顶层 API

### 8.1 `apply_strategy_engine(run_id, version_id) -> StrategyResult`

**Step 4 独立入口**。对已有 Run（通常由 Step 3 建立）应用策略引擎。

**流程**：
1. 拉 Run，改 `status=STRATEGIZING`
2. 拉 items（version_id 下全部 RequirementItem）
3. `engine.derive_obligations(items, run_id)` → obligations
4. 持久化 obligations（**幂等**：按 natural key 复用旧 id）
5. `deriver.derive_testpoints_from_obligations` → strategy TestPoints（含 fingerprint）
6. 注入 run_id → 持久化 TestPoints（Repository 按 fingerprint upsert 幂等）
7. `add_coverage` 登记：每个 obligation 被其派生的 TestPoint 覆盖
8. 计算 **Strategy Obligation Coverage**（硬指标，`repo.obligation_coverage_ratio`）
9. 计算 **RequirementItem Coverage**（软指标，仅 strategy 覆盖部分）
10. 更新 `Run.status=DONE` + `counts` 累加
11. 返回 `StrategyResult`

### 8.2 `generate_test_points_full(client, *, version_id, user_id, ...) -> FullGenerationResult`

**Step 3 + Step 4 一站式入口**。走完整 Run 状态机：`PARSING → GENERATING → STRATEGIZING → DONE`。

**流程**：
1. 调 Step 3 `generate_test_points` → LLM TestPoints + Run
2. 调 Step 4 `apply_strategy_engine(run_id, version_id)` → strategy TestPoints + obligations
3. 合流 `all_points`（从 DB 查，含两种 provenance）
4. 计算整体 **RequirementItem Coverage**（基于 all_points）
5. 返回 `FullGenerationResult`

### 8.3 数据类

```python
@dataclass
class StrategyResult:
    run_id: str
    version_id: str
    obligations: list[CoverageObligation]
    points: list[TestPoint]
    strategy_obligation_coverage: float      # 硬指标，应=1.0
    requirement_item_coverage: CoverageReport | None  # 软指标（仅 strategy 部分）
    issues: list[str]

@dataclass
class FullGenerationResult:
    run_id: str
    version_id: str
    llm_points: list[TestPoint]              # Step 3 产物
    strategy_points: list[TestPoint]         # Step 4 产物
    all_points: list[TestPoint]              # 合流
    obligations: list[CoverageObligation]
    strategy_obligation_coverage: float      # Step 4 硬指标
    requirement_item_coverage: CoverageReport | None  # 整体软指标（基于 all_points）
    phase_a_calls: int                       # Step 3 透传
    phase_b_calls: int
    warnings: list[str]
    issues: list[str]
```

---

## 9. 关键决策记录（ADR 风格）

### D1. 为什么 Obligation 是"设计要求"而非"一条测试点"？

用户补充的核心语义澄清。Obligation 表示"对 age 字段的边界进行覆盖"这一**要求**，TestPoint 才是"age=0"这一**具体点**。层次清晰：需求规则 → Obligation → TestPoint。

### D2. 为什么 Strategy Engine 不生成真实测试数据？

用户补充。非法邮箱 "abc" 这样的具体数据应由 Step 5 TestDataGenerator 生成。Step 4 只产出抽象类标识（`class="invalid_pattern"`）。分层更干净：Strategy 负责"测什么类"，TestCase 负责"用什么数据测"。

### D3. 为什么 fingerprint 用 strategy_params 而非 title？

用户补充。同一 obligation 下多个 TestPoint 若 title 相同（如都叫"验证年龄边界"），`title + target` 无法区分。`strategy_params` 结构化描述每个点的具体身份（`boundary_type=min_minus_1, value=0`），天然唯一。

### D4. 为什么 obligation 需要 natural key 对齐？

obligation.id 是 ULID 每次新生成，若 fingerprint 含 obligation_id，重跑会产生新 TestPoint（不幂等）。按 natural key `(run_id, item_id, technique, target)` 复用旧 id → 下游 fingerprint 稳定 → 幂等。

### D5. 为什么覆盖率要分两个指标？

用户修订。纯功能类 item（无 fields/permissions）Step 4 无法派生 obligation，但可能被 Step 3 LLM 覆盖。若混为一谈，会误判 Step 4 未完成职责。分离后：Strategy Obligation Coverage 是 Step 4 自身硬指标（必须 1.0），RequirementItem Coverage 是整体软指标（反映 Step 3+4 合流效果）。

### D6. 为什么 permission 的 strategy_params 不含 condition？

condition 是自然语言（如"仅本人订单"），参与 fingerprint 会导致不稳定（LLM 或人工修改 condition 文字 → fingerprint 变 → 幂等失效）。仅在 description 里提及，留给 Step 5 LLM 处理。

### D7. 为什么 min≤0 时只加 warn 不跳过 min-1 点？

用户说"Step 4 第一版可以采用简单规则，但需要测试异常输入"。跳过会丢失覆盖（某些业务确实允许负数，如温度/财务亏损）；加 warn 标记 + description 提示"需人工确认"，既保留覆盖又提醒风险。复杂业务语义判断（data_type/unit）留待迭代。

---

## 10. 与 Step 3 / Step 5 的衔接

### 10.1 与 Step 3 的衔接（上游合流）

- Step 4 复用 Step 3 建立的 Run（`run_id`），状态机 `DONE → STRATEGIZING → DONE`
- 合流写入同一张 `test_points` 表，用 `provenance` + `generation_scope` 区分
- `generate_test_points_full` 一站式入口串联 Step 3 + Step 4
- Step 3 的覆盖率软报告 + Step 4 的硬指标 = 完整覆盖率视图

### 10.2 与 Step 5 的衔接（下游消费）

Step 5 Test Case Synthesizer 将：
1. 消费合流后的 `TestPoint[]`（LLM + STRATEGY 两种 provenance）
2. 对 strategy TestPoint 的抽象类标识（如 `class="invalid_pattern"`），由 **TestDataGenerator** 生成真实测试数据
3. 派生 `TestCase`（含 `steps: list[TestStep]` / `expected` / `precondition`）
4. TestCase 的 fingerprint 公式可能复用 Step 3/4 的思路（待 Step 5 讨论）
5. TestCase 状态机 `GENERATED → VALIDATED → REVIEWED → CONFIRMED`

---

## 11. 诚实边界（首批简单规则，留待迭代）

1. **边界值 float precision**：首批步长固定=1，float 字段（如 price min=0.01）的精细边界（0.00/0.01/0.02）留待迭代
2. **复杂正则样例**：`_pattern_examples` 只做简单启发式（`\d{N}` / `[A-Za-z0-9_]`），复杂正则的精确样例由 Step 5 TestDataGenerator 生成
3. **permission condition**：自然语言 condition 不参与代码派生，留给 Step 5 LLM
4. **纯功能类 item**：无 fields/permissions 的 item Step 4 无法派生 obligation，依赖 Step 3 LLM 覆盖（软指标报告列出）
5. **BusinessRule**：首批不消费（DECISION_TABLE 延后）
6. **覆盖语义是「结构性」而非「语义性」**（★关键，见 §5.4）：`Strategy Obligation Coverage == 1.0` 仅保证「每个 obligation 都派生了 TestPoint 并登记回链」，**不保证** TestPoint 在语义上真正满足 obligation 的测试设计意图。后者是 Step 5（TestCase 合成时检验可执行性）与 Step 7（AI Reviewer coverage 维度）的职责。Step 4 有意到此为止。

---

## 12. 验证命令（PowerShell）

```powershell
# 全量测试（期望 497 passed = Step 3 后 362 + Step 4 新增 135）
.\.venv\Scripts\python.exe -m pytest -q

# Step 4 专项（期望 135 passed = 42+29+15+18+16+15）
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_boundary.py tests/test_strategy_equivalence.py tests/test_strategy_permission.py tests/test_tp_strategy_deriver.py tests/test_strategy_orchestrator.py tests/test_step4_acceptance.py -v

# 代码质量
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

---

## 13. 下一步 = Step 5「重构 测试点 → 测试用例」

见 `docs/v2/PROGRESS.md` §9。开工前需与用户讨论确认：
- TestCase 与 TestPoint 的派生关系（1:1 还是 1:N）
- strategy TestPoint 的具体测试数据生成（代码规则 vs LLM）
- TestCase 状态机转移触发时机
- TestCase fingerprint 公式
- TestStep 结构化生成粒度
- Run 状态机复用策略
