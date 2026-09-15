# Step 3 设计文档：测试点生成引擎（TestPoint Generator）

> 对应 `docs/v2/PROGRESS.md` §5.5 与蓝图 Step 3。
> 本文档是 Step 3 的**单一设计真相源**，新会话接手 Step 4 前建议先读本文 + `step1-data-model.md`。

---

## 0. 一句话

**Step 3 = LLM 侧的测试点生成引擎**：消费 Step 2 的 `RequirementVersion + RequirementItem[]`，两阶段调用 LLM 产出 `TestPoint(provenance=LLM)`，代码侧强制覆写 Step 3 硬性约束（`technique=None` / `obligation_id=None` / `module` 来自 IR / `generation_scope` / `fingerprint`），去重后 upsert 到 `data_v2.db`。

---

## 1. 定位与边界

### 1.1 在 V2 蓝图中的位置

```
Step 2 IR ─┬─→ Step 3 TestPoint Generator (LLM 语义派生)  ─┐
           │                                                ├─→ Step 4/5 合成用例
           └─→ Step 4 策略引擎 (代码派生 CoverageObligation → TestPoint) ─┘
```

Step 3 与 Step 4 **写入同一张 `test_points` 表**，用 `provenance` 字段区分：
- Step 3：`provenance=LLM`，`technique=None`，`obligation_id=None`
- Step 4：`provenance=STRATEGY`，`technique=<对应技术>`，`obligation_id=<回链>`

### 1.2 严格不做（Step 3 边界外）

| 项 | 归属 |
|---|---|
| CoverageObligation 派生 | Step 4 |
| `provenance=STRATEGY` 的 TestPoint | Step 4 |
| TestPoint → TestCase 合成 | Step 5 |
| 变更影响分析、追溯链渲染 | Step 6 |
| AI Reviewer 6 维评审 | Step 7 |
| Web API / 前端 `static/app.js` | Step 8+ |
| 改 V1 `TEST_POINTS_PROMPT` / `core/generator.py` | 永不做 |

---

## 2. 两阶段生成策略（用户确认）

### 2.1 Phase A：逐 item 独立生成（`test-point-generator-v1`）

**输入**：单个 `RequirementItem` 的 JSON（剔除内部审计字段 `version_id/seq/provenance/status/confidence_level/schema_version/id`）。
**输出**：该 item 对应的 2~6 个基础 TestPoint 候选。
**代码侧强制**：
- `item_ids = [当前 item.id]`（忽略 LLM 给的，LLM 有时会漏或写错）
- `module = item.module`（一字不差，不接受 LLM 改写）
- `generation_scope = ITEM`

**调用次数**：items 数量 N 就是 N 次 LLM 调用（默认串行；并行优化留到 Step 10）。

### 2.2 Phase B：跨项补漏（`test-point-completer-v1`）

**输入**：
1. 全量 items 摘要（每项单行：`id | type | module | statement | priority_hint | confidence | fields=[...] | rules=[...] | permissions=N条`）
2. Phase A 已产出的 TestPoint 摘要（单行：`[dimension] module / subcategory / title (items=id1,id2)`）

**输出**：0~8 个跨项 TestPoint 候选，每个必须关联 ≥ 2 个 item。
**代码侧强制**：
- 过滤 `item_ids` 里不在 items 集合的非法 id；过滤后 `len < 2` → **丢弃**
- `module` 必须 ∈ items.module 集合；否则取第一个 item 的 module 兜底
- `generation_scope = CROSS_ITEM`

**分批策略**：items 数量 > `PHASE_B_BATCH_THRESHOLD`（默认 30）→ 按 `module` 分组分批，每批独立调用 LLM（每批都能看到 Phase A 全貌，避免重复补漏），代码侧合并结果。

### 2.3 Phase B 跳过条件

若 Phase A 无产出（`phase_a_points == []`），**跳过 Phase B**（无参照系，LLM 无法判断"已覆盖 vs 需补漏"）。此时 `phase_b.issues` 记录 `"Phase A 无产出，跳过 Phase B"`。

---

## 3. Step 3 硬性约束（Validator 代码强制覆写）

| 字段 | 约束 | 违反处理 |
|---|---|---|
| `provenance` | 必须 = `LLM` | 代码覆写，不接受 LLM 给的值 |
| `technique` | 必须 = `None` | 代码清空，Step 4 策略引擎才会赋值 |
| `obligation_id` | 必须 = `None` | 代码清空，Step 4 策略引擎才会赋值 |
| `status` | 必须 = `DRAFT` | 代码覆写 |
| `generation_scope` | Phase A=`ITEM`，Phase B=`CROSS_ITEM` | 代码按 phase 参数强制指定 |
| `module` | 必须来自关联 `RequirementItem.module` | Phase A 直接覆写；Phase B 校验 ∈ items.module 集合，否则取第一个 item 的 module 兜底 |
| `subcategory` | 允许 LLM 在 IR 语义范围内合理归纳 | 只做非空校验，缺失时兜底 `"未分类"` |
| `item_ids` | Phase A=`[当前 item.id]`；Phase B 长度 ≥ 2 | 见 §2 |
| `dimension` | 必须 ∈ `TestDimension` 枚举（12 值） | 非法值兜底为 `FUNCTIONAL` |
| `priority` | 必须 ∈ `Priority` 枚举（P0/P1/P2/P3） | 非法值兜底为 `P1` |
| `fingerprint` | 必须非空 | Repository 入库前兜底计算（见 §4） |

**"不臆造"边界**：`subcategory` 不得引入 IR 中不存在的业务实体/业务规则——靠 **Prompt 约束**（`tp_prompts.py` 里显式声明），代码侧只校验非空；真实 LLM 是否越界需真实 API 抽查（mock 无法确定性验证）。

---

## 4. 业务确定性 fingerprint（幂等身份）

### 4.1 为什么不用 ULID

ULID 每次生成都是新的，跨轮次调用 orchestrator 会产生不同 ULID 的"业务上同一个" TestPoint → DB 层重复行。

### 4.2 公式

```python
fingerprint = "tp_" + sha256(
    "|".join([
        version_id or "",
        generation_scope,               # "item" 或 "cross_item"
        ",".join(sorted(item_ids)),     # 排序后拼接，保证顺序无关
        normalize_text(module),
        normalize_text(subcategory),
        normalize_text(title),
    ])
).hexdigest()[:32]
```

`normalize_text`：strip + 内部空白（含全角空格）压缩为单空格 + 小写化。不做标点归一（不同标点在标题里可能表达不同语义，保守处理）。

**返回格式**：`tp_<32 位十六进制>`，共 35 字符。

### 4.3 六元组构成业务身份的理由

| 字段 | 为什么参与身份 |
|---|---|
| `version_id` | 不同 RequirementVersion 的 TestPoint 天然隔离（需求变更后旧版 TestPoint 保留） |
| `generation_scope` | Phase A 单点与 Phase B 跨点即使 title 巧合相同也是不同业务身份（一个强调"该 item 自身"，一个强调"多个 item 的联动"） |
| `sorted(item_ids)` | M:N 关联集合，排序后拼接保证顺序无关 |
| `module / subcategory / normalize(title)` | 内容三元组，业务语义唯一标识 |

### 4.4 DB 层唯一约束

```sql
CREATE UNIQUE INDEX ux_test_points_fingerprint ON test_points(fingerprint);
```

SQLite 的 UNIQUE 索引对 NULL 宽松（多个 NULL 不冲突），但 `Repository.save_test_point` **入库前兜底计算**，保证每行 fingerprint 非空 → 唯一约束真实生效。

### 4.5 Repository 层幂等 upsert

```python
def save_test_point(tp):
    if not tp.fingerprint:
        tp.fingerprint = compute_testpoint_fingerprint(...)
    with v2_conn() as conn:
        # 幂等身份对齐：若已有同 fingerprint 行但 id 不同，复用旧 id
        existing = conn.execute("SELECT id FROM test_points WHERE fingerprint = ?", (tp.fingerprint,)).fetchone()
        if existing and existing["id"] != tp.id:
            tp.id = existing["id"]
        # 走 upsert（ON CONFLICT(id) DO UPDATE）
        ...
```

**复用旧 ULID 的理由**：保证下游 `test_case_points` / `obligation_coverage` 链接不断（若每次生成新 ULID，旧链接会指向"已不存在"的 TestPoint）。

---

## 5. 去重策略

### 5.1 严格去重（fingerprint 唯一）

同 fingerprint 的多个候选 → 保留首个、后续丢弃并记入 `issues`。

### 5.2 不做跨 phase 语义合并（关键设计决策）

**用户原方案**曾提议"Phase A 与 Phase B 冲突时，保留 Phase A 主体，合并 Phase B 的 item_ids 到并集"。**实施时决定不做**，理由：

1. Phase A 的 `generation_scope=item`、`item_ids` 长度必为 1；Phase B 的 `generation_scope=cross_item`、`item_ids` 长度必 ≥ 2。
2. fingerprint 公式包含 `generation_scope + sorted(item_ids)` → **两 phase 天然不会撞 fingerprint**。
3. 若强行合并（把 Phase B 的 item_ids 并到 Phase A），会破坏 Phase A 的"单点追溯"语义，并使 `generation_scope=item` 与 `len(item_ids)>=2` 矛盾，违反 Schema 约束。
4. 语义相似但 generation_scope 不同的 TestPoint **保留两者是合理的**：单点强调"该 item 自身的验证"，跨点强调"多个 item 的联动关系"，业务价值不同。

### 5.3 语义相似 warning（软提示，非阻塞）

`warn_semantic_duplicates` 按 `(normalize(module), normalize(subcategory), normalize(title))` 检查跨 phase 语义相似，**只记录 warning 不合并**，供 orchestrator 返回给调用方（Step 5 评审阶段可据此提示用户）。

---

## 6. 覆盖率报告（软指标）

```python
@dataclass
class CoverageReport:
    total_items: int
    covered_item_ids: list[str]      # 所有 TestPoint.item_ids 的并集
    uncovered_item_ids: list[str]    # items - covered
    coverage_ratio: float            # len(covered) / len(all_items)
```

**软指标**：不阻塞入库。未覆盖的 item 由 **Step 4 策略引擎硬兜底**（例如 `field_spec` 有 min/max 但 Phase A 没生成 boundary TestPoint，Step 4 会派生 obligation → strategy TestPoint）。

---

## 7. Run 与 GenerationConfig 绑定

### 7.1 强制建 Run

每次调用 `generate_test_points` 都**强制建 Run**（`run.status: PARSING → GENERATING → DONE`），落 `requirement_version_id` + `doc_id` + `user_id` + `generation_config_id`。

Phase A 与 Phase B **共用同一个 Run**（不开两个 Run），阶段信息落到 `GenerationResult.phase_a` / `phase_b` 的 `PhaseStats` 结构里（不写 TestPoint 字段，Schema 已冻结）。

### 7.2 GenerationConfig 快照

| 字段 | 值来源 |
|---|---|
| `model_provider` | `config.yaml` 的 `generate.api_type`（兜底 `"openai"`） |
| `model_name` | `config.yaml` 的 `generate.model`（兜底 `"unknown"`） |
| `temperature` | `config.yaml` 的 `generate.temperature`（兜底 `0.3`） |
| `max_tokens` | `config.yaml` 的 `generate.max_tokens`（可选） |
| `enable_thinking` | `config.yaml` 的 `generate.enable_thinking`（可选） |
| `prompt_version` | `"test-point-generator-v1,test-point-completer-v1"`（两 phase 版本号拼接） |
| `generator_version` | `"tp-gen-v1"` |
| `reviewer_version` | `None`（Step 7 才用） |

---

## 8. 交付文件清单

### 8.1 新增（`core/v2/`）

| 文件 | 行数 | 职责 |
|---|---|---|
| `fingerprint.py` | 61 | `compute_testpoint_fingerprint` + `normalize_text` |
| `tp_prompts.py` | 133 | Phase A/B Prompt + 版本号 + user prompt 模板 |
| `tp_generator.py` | 241 | LLM 调用 + 鲁棒 JSON 提取 + Phase B 按 module 分批 |
| `tp_validator.py` | 321 | 白名单过滤 + Pydantic 校验 + Step 3 硬性覆写 + item_ids 归一 + 枚举兜底 + fingerprint 计算 + 去重 + 覆盖率 + 语义 warning |
| `tp_orchestrator.py` | 287 | 顶层编排：Run+Config → Phase A/B → Validator → dedupe → upsert → coverage → Run.DONE；便捷入口 `generate_test_points_from_files` |

### 8.2 升级（Schema/DDL/Repository/Migration）

| 文件 | 变更 |
|---|---|
| `core/schemas/common.py` | 新增 `GenerationScope(StrEnum)` = ITEM / CROSS_ITEM |
| `core/schemas/testpoint.py` | TestPoint 新增 `generation_scope`（默认 ITEM）+ `fingerprint`（可选，Repository 兜底计算） |
| `core/schemas/__init__.py` | 导出 `GenerationScope` |
| `core/v2/ddl.py` | `SCHEMA_VERSION=3`；`test_points` 加两列 + `UNIQUE(fingerprint)` 索引；`create_v2_schema()` 含 v2→v3 自动升级分支（ALTER TABLE 补列 + 已有行补算 fingerprint） |
| `core/v2/repository.py` | `save_test_point` 按 fingerprint upsert（同 fingerprint 复用旧 ULID）；新增 `get_test_point_by_fingerprint` / `list_test_points_by_version` / `list_test_points_by_run` |
| `core/v2/migrate_v1_to_v2.py` | V1 迁移过来的 TestPoint 显式设 `generation_scope=ITEM` + 补算 fingerprint |

### 8.3 测试（`tests/`）

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `test_tp_generator.py` | 17 | Phase A/B JSON 提取、异常处理、分批、prompt 内容验证 |
| `test_tp_validator.py` | 27 | 硬性覆写、item_ids 归一、枚举兜底、去重、覆盖率、语义 warning |
| `test_tp_orchestrator.py` | 13 | 端到端、Run/Config 落库、幂等重跑、关联表、边界情况 |
| `test_step3_acceptance.py` | 15 | 12 条主门槛 + 3 条附加（generation_scope / fingerprint / V1 零回归） |
| **合计** | **72** | |

### 8.4 Step 1 层回归测试更新

| 文件 | 变更 |
|---|---|
| `test_schemas.py` | 补 `GenerationScope` 枚举测试 + TestPoint 默认字段断言 |
| `test_v2_repository.py` | 补 fingerprint 幂等测试（同 fingerprint 复用旧 ULID）+ scope 不同不撞 fingerprint + `schema_version==3` 断言 |
| `test_v2_roundtrip.py` | `test_testpoint_all_fields` 补 `generation_scope` + `fingerprint` 往返保真 |

---

## 9. 12 条主验收门槛 + 3 条附加（全部 PASSED）

| # | 门槛 | 对应测试 |
|---|---|---|
| 1 | orchestrator 能生成 TestPoint[] 并全部通过 Pydantic 严格校验 | `test_01_orchestrator_produces_pydantic_valid_points` |
| 2 | 每个 TestPoint 的 `item_ids` 全部指向真实存在的 RequirementItem | `test_02_item_ids_all_reference_real_items` |
| 3 | Phase A 的 `item_ids` 严格等于 `[输入 item.id]`，LLM 乱写被代码覆写 | `test_03_phase_a_item_ids_forced_overwrite` |
| 4 | Phase B 的 `len(item_ids) >= 2`；过滤非法 id 后 <2 的被丢弃 | `test_04_phase_b_requires_two_or_more_items` |
| 5 | dimension/priority 非法值被代码兜底；`technique=None`、`obligation_id=None`、`provenance=LLM` 硬性覆写 | `test_05_enum_fallback_and_step3_hard_constraints` |
| 6 | 按 fingerprint 严格去重；跨 phase 不做语义合并 | `test_06_dedupe_by_fingerprint` |
| 7 | items 过多时 Phase B 能按 module 分批 + 合并结果不重不漏 | `test_07_phase_b_batches_by_module` |
| 8 | Run + GenerationConfig 正确落库；`run.status` 终态 DONE | `test_08_run_and_generation_config_persisted` |
| 9 | `test_point_items` 关联表 M:N 写入正确；删 TestPoint 不级联删 Item | `test_09_test_point_items_link_and_no_reverse_cascade` |
| 10 | 幂等：同 version 重复调用不产生孤儿/重复（fingerprint upsert 复用 ULID） | `test_10_idempotent_rerun` |
| 11 | 覆盖率报告能列出未被任何 TestPoint 引用的 RequirementItem（软指标，不阻塞入库） | `test_11_coverage_report_lists_uncovered_items` |
| 12 | module 必须来自 IR items；subcategory 允许 LLM 语义归纳 | `test_12_module_from_ir_subcategory_free` |
| 附加 A | `generation_scope` 正确赋值（Phase A=item, Phase B=cross_item） | `test_extra_generation_scope_correctly_assigned` |
| 附加 B | `fingerprint` 非空、格式正确（`tp_` + 32 hex）、DB UNIQUE 约束生效 | `test_extra_fingerprint_nonempty_and_unique` |
| 附加 C | V1 零回归（`web/data.py`、`core/generator.py`、`data.db` 不动） | `test_extra_v1_untouched` |

---

## 10. 顶层 API

### 10.1 `generate_test_points(client, *, version_id, user_id=None, generation_config=None, phase_b_batch_threshold=None) -> GenerationResult`

**Step 3 主入口**。给定 `RequirementVersion.id`，两阶段生成 TestPoint 并持久化。

**流程**：
1. 拉 Version 与 items（items 为空则直接返回带 issue 的空结果）
2. `user_id` 兜底：从 Version → Doc → user_id 反查
3. 建 `GenerationConfig`（从 `config.yaml` 读取模型配置）+ `Run`（status=PARSING → GENERATING）
4. **Phase A**：逐 item 调 LLM → `validate_phase_a` → 汇总
5. **Phase B**：全量 items + Phase A 摘要 → `complete_cross_item`（items > 阈值时按 module 分批）→ `validate_phase_b` → 汇总
6. `dedupe_points`（按 fingerprint）+ `warn_semantic_duplicates`
7. 注入 `run_id` → upsert TestPoint（Repository 层按 fingerprint 幂等）
8. `build_coverage_report` → 更新 `Run.status=DONE` + `counts`
9. 返回 `GenerationResult`

### 10.2 `generate_test_points_from_files(client, *, user_id, title, text=None, paths=None, source_type="text") -> GenerationResult`

**便捷链式入口**：Step 2 `ingest_and_build_ir` + Step 3 `generate_test_points` 一步到 TestPoint。

### 10.3 `GenerationResult` 数据类

```python
@dataclass
class GenerationResult:
    run_id: str
    version_id: str
    points: list[TestPoint]
    coverage: CoverageReport | None
    phase_a: PhaseStats       # calls / raw_count / valid_count / dropped_count / issues
    phase_b: PhaseStats
    warnings: list[str]       # 语义相似等软提示
    issues: list[str]         # 全局问题（Run/Version 层面 + dedupe 丢弃）
```

---

## 11. 与 Step 2 / Step 4 的衔接

### 11.1 与 Step 2 的衔接（上游）

- Step 3 消费 Step 2 的 `RequirementVersion.id`，通过 `repo.list_items(version_id)` 拉 IR items。
- Step 3 **复用** Step 2 的 `parser.extract_json`（鲁棒 JSON 提取），不重复造轮子。
- Step 3 **不修改** Step 2 的任何文件（`prompts.py` / `ingestion.py` / `parser.py` / `validator.py` / `ir.py`）。

### 11.2 与 Step 4 的衔接（下游）

Step 4 策略引擎将：
1. 消费 Step 2 IR 的 `FieldSpec / BusinessRule / PermissionRule`，用代码推导 `CoverageObligation`。
2. 派生 `TestPoint(provenance=STRATEGY, technique=<对应技术>, obligation_id=<回链>)`，写入**同一张 `test_points` 表**。
3. Step 4 的 TestPoint 的 `generation_scope` 语义待 Step 4 讨论时确定（可能新增 `STRATEGY` 值或复用 `ITEM`）。
4. Step 4 的 TestPoint 的 `fingerprint` 公式是否需含 `technique + obligation_id` 待 Step 4 讨论时确定。
5. Step 3 的覆盖率软报告 → Step 4 升级为硬约束（`obligation_coverage` 关系表 100%）。

---

## 12. 关键决策记录（ADR 风格）

### D1. 为什么 Phase A 强制覆写 `item_ids`，不让 LLM 自己给？

LLM 有时会漏给、写错、或给出"看起来合理但实际不存在"的 ULID。Phase A 的语义是"针对当前这一个 item 生成测试点"，`item_ids` 必然等于 `[当前 item.id]`，让 LLM 给是多余且不可靠的。代码强制覆写 = 100% 追溯精准。

### D2. 为什么 Phase B 的 `module` 允许 LLM 选，但必须 ∈ items.module 集合？

跨项测试点可能关联多个 module 的 items（例如"登录"的手机号 item + "订单"的金额 item）。让 LLM 选"业务重心所在的 module"更贴近语义，但必须校验 ∈ items.module 集合防止臆造新 module。

### D3. 为什么 `subcategory` 允许 LLM 自由归纳？

`subcategory` 是测试点的**测试分类**（如"输入校验"/"边界条件"/"字段联动"），不一定直接对应 IR 里的字段。让 LLM 在 IR 语义范围内合理归纳是必要的（否则测试点分类会过于僵化）。代码侧只做非空校验；"不得引入 IR 中不存在的业务实体/业务规则"靠 Prompt 约束 + 真实 API 抽查。

### D4. 为什么 fingerprint 公式包含 `generation_scope`？

避免 Phase A 的单点（`item_ids=[x]`）与 Phase B 的跨点（`item_ids=[x,y]`）因 title 巧合相同而撞 fingerprint。两者业务身份不同（一个强调"该 item 自身"，一个强调"x 与 y 的联动"），必须分开。

### D5. 为什么 fingerprint 公式**不**包含 `run_id`？

同一 `version_id` 下多次调用 orchestrator（不同 Run）应视为同一业务身份，幂等复用旧 ULID。若 fingerprint 含 `run_id`，每次 Run 都会产生新 ULID 的"业务上同一个" TestPoint → DB 层重复行 + 下游 `test_case_points` 断链。

### D6. 为什么不做跨 phase 语义合并？

见 §5.2。核心矛盾：合并会破坏 `generation_scope` 与 `item_ids` 长度的 Schema 约束（`item` 必须长度 1，`cross_item` 必须长度 ≥ 2）。保留两者是合理的业务语义。

### D7. 为什么 Phase A 默认串行，不并行？

Step 3 只做后端 + mock 测试，性能不是首要目标。并行优化留到 Step 10（性能优化）统一做，避免 Step 3 引入并发复杂度（LLM 客户端线程安全、错误重试、rate limit 等）。

---

## 13. 诚实边界（mock 无法确定性验证的项）

1. **Prompt 约束的有效性**：门槛 12 "subcategory 不得引入 IR 中不存在的业务实体/业务规则" 靠 Prompt 约束，代码侧只校验非空。真实 LLM 是否越界需真实 API 抽查。
2. **Phase B 的跨项洞察质量**：mock 测试只验证"代码侧正确处理 LLM 给的候选"，不验证"LLM 是否真能洞察跨项联动"。后者需真实 API + 人工评估。
3. **覆盖率软报告的"未覆盖"含义**：只表示"没有 LLM 派生的 TestPoint 引用该 item"，不表示"该 item 没被测试覆盖"——Step 4 策略引擎会硬兜底（例如 field_spec 有 min/max 就会派生 boundary obligation）。

---

## 14. 验证命令（PowerShell）

```powershell
# 全量测试（期望 345 passed = Step 1 层 290 + Step 3 新增 55）
.\.venv\Scripts\python.exe -m pytest -q

# Step 3 专项（期望 72 passed = 17 + 27 + 13 + 15）
.\.venv\Scripts\python.exe -m pytest tests/test_tp_generator.py tests/test_tp_validator.py tests/test_tp_orchestrator.py tests/test_step3_acceptance.py -v

# 代码质量
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

---

## 15. 下一步 = Step 4「测试策略引擎」

见 `docs/v2/PROGRESS.md` §9。开工前需与用户讨论确认：
- 覆盖义务推导的技术选型优先级（boundary_value / equivalence_class / decision_table / state_transition / permission_matrix / error_guessing / scenario）
- CoverageObligation 与 TestPoint 的派生关系（1:1 还是 1:N）
- 覆盖率硬指标（Step 3 软报告 → Step 4 硬约束）
- Step 4 TestPoint 的 fingerprint 公式是否需含 `technique + obligation_id`
- Step 3 未覆盖的 item 在 Step 4 如何硬兜底
