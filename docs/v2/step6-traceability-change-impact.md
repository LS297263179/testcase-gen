# Step 6 设计文档：Traceability 追溯链 + 变更影响分析

> 对应 `docs/v2/PROGRESS.md` §5.8 与蓝图 Step 6。
> 本文档是 Step 6 的**单一设计真相源**，新会话接手 Step 7 前建议先读本文 + `step5-testcase-synthesizer.md`。

---

## 0. 一句话

**Step 6 = 追溯链查询 + 需求变更影响分析**：基于 Step 1~5 已落地的关联表，提供正/反向追溯查询；给 `RequirementItem` 加**双 hash**（identity fingerprint + content_hash），diff 同一 Doc 的两个 `RequirementVersion`，识别 UNCHANGED/MODIFIED/ADDED/DELETED 四态，沿追溯链定位受影响的 TestPoint/TestCase，产出**只读**的 `ChangeImpactReport`（含 affected_reason + impact_level + recommended_actions）。

---

## 1. 定位与边界

### 1.1 在 V2 蓝图中的位置

```
Step 5 TestCase[] ─┐
                   ├─→ Step 6 Traceability（正/反向追溯）+ Change Impact（版本 diff）
Step 2 IR items ───┘         ↓
                     ChangeImpactReport（只读）
                             ↓
                   Step 7 AI Review → Step 9 人工确认
```

### 1.2 严格不做（Step 6 边界外，用户强调）

| 项 | 归属 | 原因 |
|---|---|---|
| 修改 TestCase/TestPoint 状态（如 RE_REVIEW_REQUIRED） | Step 9 | Step 6 只发现影响，不参与状态机/编辑闭环 |
| 重新生成测试资产（对受影响 item 重跑 Step 3/4/5） | 未来能力 | 会把 Change Detection + Regeneration + Dedup + Review + State 缠在一起 |
| 6 维 AI Reviewer 评审 | Step 7 | Step 6 只做代码化 diff + 追溯 |
| ChangeImpactReport 持久化 / 前端可视化 | Step 13 | Step 6 只返回内存 dataclass |
| 语义级影响判断（超越代码规则） | Step 7 AI | 首批 impact_level 用代码规则 |
| 改 V1 运行时 | 永不做 | — |

> **Step 6 要非常纯粹**："发生了什么变化？哪些测试资产受影响？" 就够了。

---

## 2. 6 条核心原则（用户冻结）

1. **纯只读**：只调 `repo.get_*/list_*`，绝不 `save_*`、不改状态、不重生成。执行前后 test_points/test_cases/statuses 零业务变化。
2. **RequirementItem 双 hash**（对齐 Step 5 TestCase）：
   - `fingerprint`（identity，判"是不是同一项"）= `sha256(normalize(module) | type | normalize(statement))[:32]`，**不含 doc_id / version_id**
   - `content_hash`（判"内容变没变"）= `sha256(normalize(statement) | canonical(fields) | canonical(rules) | canonical(permissions) | canonical(acceptance_criteria))[:32]`
3. **匹配逻辑**：identity 同 → 比 content_hash（同=UNCHANGED / 异=MODIFIED）；identity 异 → statement 相似度兜底（ratio≥阈值=MODIFIED，否则 old=DELETED / new=ADDED）。
4. **职责边界守住**：Step 6 发现影响 / Step 7 AI Review / Step 9 人工确认。
5. **影响清单可解释**：每条变更带 `affected_reason`（细分原因）+ `impact_level`（HIGH/MEDIUM/LOW）+ `recommended_actions`。
6. **V1 运行时完全不修改**。

---

## 3. ★ 核心难点与解法（用户反馈）

### 3.1 难点：statement 没改，但 FieldSpec 改了

```
V1: 年龄 18~60     statement = "年龄必须符合规定范围"
V2: 年龄 18~65     statement = "年龄必须符合规定范围"（一字未改）
```

若只比 statement → identity fingerprint 完全不变 → 误判 UNCHANGED。但需求实际已修改。

### 3.2 解法：identity 与 content 分离（两个 hash）

| hash | 判什么 | 输入 |
|---|---|---|
| `fingerprint`（identity） | 是不是同一个 RequirementItem | module + type + normalize(statement) |
| `content_hash`（内容） | 这个 item 的内容有没有变化 | normalize(statement) + fields + rules + permissions + acceptance_criteria |

- identity 同 + content 同 → **UNCHANGED**
- identity 同 + content 异 → **MODIFIED**（18~60→18~65 走这条：identity 不变，content_hash 因 fields 变而变）

### 3.3 为何 identity fingerprint 不含 doc_id

- doc_id 是**文档实体身份**，非 item **逻辑身份本体**。
- 同一 RequirementDoc 的 v1/v2/v3 共用同一 doc_id，item 要跨版本互相匹配 → identity 只需 module+type+statement。
- 需区分不同文档的同名需求时，在**数据库查询范围**里限定 doc_id（`analyze_change_impact` 只在同 doc 的两个 version 间匹配），而非让 doc_id 进入 identity 公式。
- 前提约束：不出现"不同版本新建 doc_id"（RequirementVersion 天然隶属同一 Doc）。

---

## 4. 匹配与影响算法

### 4.1 ItemMatcher（`change_impact.py`）

```
1. 校验 version_old.doc_id == version_new.doc_id（否则返回带 issue 的空报告，不可比）
2. 按 identity fingerprint 建 old/new 索引：
   - fingerprint 交集 → 比 content_hash：同=UNCHANGED，异=MODIFIED
3. old/new 各自未匹配项 → statement 相似度兜底：
   - difflib.SequenceMatcher(None, norm_old, norm_new).ratio()
   - 取 ratio ≥ 阈值（默认 0.6，可配）的最优配对（贪心，每项只用一次）→ MODIFIED（identity 漂移但语义相近，带 similarity）
4. 仍剩余：old → DELETED，new → ADDED
```

### 4.2 ImpactResolver

- **MODIFIED / DELETED**：从 `old_item_id` 经 `trace_forward_from_item` 追溯受影响的 TP/TC（基于旧版本的资产需复审）。
- **ADDED**：affected 为空，`recommended_actions` 标注"需新增测试覆盖"（仅建议，不触发 Step 3/4/5）。
- **affected_reasons**（分量比对 old/new）：
  - canonical(fields) 异 → `FIELD_CONSTRAINT_CHANGED`
  - canonical(rules) 异 → `BUSINESS_RULE_CHANGED`
  - canonical(permissions) 异 → `PERMISSION_CHANGED`
  - canonical(acceptance) 异 → `ACCEPTANCE_CHANGED`
  - 四者皆同（仅 statement 措辞）→ `REQUIREMENT_MODIFIED`
  - ADDED/DELETED → `REQUIREMENT_ADDED` / `REQUIREMENT_DELETED`
- **impact_level**（首批代码规则）：
  - ADDED / DELETED → HIGH
  - FIELD_CONSTRAINT_CHANGED / PERMISSION_CHANGED / BUSINESS_RULE_CHANGED → HIGH
  - ACCEPTANCE_CHANGED → MEDIUM
  - 仅措辞（REQUIREMENT_MODIFIED）→ LOW

### 4.3 ChangeImpactReport（只读返回值，不持久化）

```python
@dataclass
class ItemChange:
    change_type: ChangeType
    old_item_id: str | None; new_item_id: str | None
    old_statement: str; new_statement: str
    similarity: float | None          # identity 漂移时的相似度
    affected_reasons: list[AffectedReason]
    impact_level: ImpactLevel
    affected_test_points: list[str]   # 追溯 old_item 的 TP（ADDED 为空）
    affected_test_cases: list[str]    # 追溯 TP 的 TC
    recommended_actions: dict         # {"test_points":"review","test_cases":"review"} / {"..":"generate_new"}

@dataclass
class ChangeImpactReport:
    doc_id: str; version_old_id: str; version_new_id: str
    changes: list[ItemChange]         # 排序：MODIFIED/DELETED/ADDED 在前，UNCHANGED 靠后；再按 impact_level
    summary: dict                     # {unchanged, modified, added, deleted, affected_test_point_count, affected_test_case_count}
    issues: list[str]
```

---

## 5. Schema / DDL 变更（schema_version 5 → 6）

### 5.1 新增枚举（`core/schemas/common.py`）

```python
class ChangeType(StrEnum):     UNCHANGED / MODIFIED / ADDED / DELETED
class AffectedReason(StrEnum): REQUIREMENT_MODIFIED / REQUIREMENT_DELETED / REQUIREMENT_ADDED
                               / FIELD_CONSTRAINT_CHANGED / BUSINESS_RULE_CHANGED
                               / PERMISSION_CHANGED / ACCEPTANCE_CHANGED
class ImpactLevel(StrEnum):    HIGH / MEDIUM / LOW
```

### 5.2 RequirementItem 新增 2 字段（`core/schemas/requirement.py`）

```python
fingerprint: str | None = None    # identity（Repository 入库前兜底计算）
content_hash: str | None = None   # 内容
```

### 5.3 fingerprint.py 新增

- `compute_item_identity_fingerprint(*, module, type, statement) -> "ri_"+32hex`
- `compute_item_content_hash(*, statement, fields, rules, permissions, acceptance_criteria) -> "rich_"+32hex`
- `_canonical_list`：Pydantic 模型先 `model_dump(mode="json")` 再 `json.dumps(sort_keys, 紧凑)`，保证 content_hash 分量确定性

### 5.4 DDL 升级（`core/v2/ddl.py`）

- `requirement_items` 加列 `fingerprint TEXT` + `content_hash TEXT`
- **普通索引** `idx_items_fingerprint`（★ **不 UNIQUE**：同 doc 的 v1/v2 同一 item identity fingerprint 相同，UNIQUE 会冲突；这与 TestPoint/TestCase 的 fingerprint 含 version_id 故可 UNIQUE 不同）
- `_migrate_v5_to_v6`：补 2 列 + 已有行回填两 hash（回填 content_hash 时用 `repository._load_fields` 重建 FieldSpec，保证与 `save_item` 计算一致）

### 5.5 repository.py

- `save_item`：入库前若 fingerprint/content_hash 为空则兜底计算 + upsert 加 2 列
- `_row_to_item`：反序列化两字段
- 新增 `list_test_points_by_item(item_id)`（经 test_point_items）、`list_test_cases_by_test_point(tp_id)`（经 test_case_points）
- **migrate_v1_to_v2.py 无需改**：V1 迁移不产生 RequirementItem（遵"不猜历史关系"铁律），已有 item 行的回填由 `_migrate_v5_to_v6` 负责

---

## 6. 追溯链查询（`traceability.py`）

| 函数 | 链路 | 用途 |
|---|---|---|
| `trace_forward_from_item(item_id)` | item → TestPoint[]（test_point_items）→ TestCase[]（test_case_points，去重）→ Obligation[]（strategy TP.obligation_id） | 变更影响分析定位受影响资产 |
| `trace_backward_from_case(case_id)` | TestCase → TestPoint[] → RequirementItem[] → Version → Doc → source_ref | 从用例回溯"基于哪个版本哪些需求项、原文在哪" |

- 复用 `resolver.TargetResolver.exists` 做输入存在性校验（不存在 → 返回带 issue 的空 trace）。
- 纯只读；一个 TC 关联多个 TP、一个 TP 关联多个 item 均正确去重处理。

---

## 7. 交付文件清单

### 7.1 新增（`core/v2/`）

| 文件 | 职责 |
|---|---|
| `traceability.py` | 正向/反向追溯查询（ForwardTrace / BackwardTrace） |
| `change_impact.py` | ItemMatcher + ImpactResolver + analyze_change_impact + ChangeImpactReport/ItemChange |

### 7.2 升级

common.py（3 枚举）、requirement.py（2 字段）、__init__.py（导出）、fingerprint.py（2 函数 + _canonical_list）、ddl.py（v6 + 迁移）、repository.py（save_item 兜底 + _row_to_item + 2 追溯查询）

### 7.3 测试（`tests/`，全代码不调 LLM）

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `test_traceability.py` | 11 | 正向/反向追溯、多对多、去重、断链防御、source_ref、resolver 复用 |
| `test_change_impact.py` | 19 | 四态、content 分量、相似度兜底、reason/level、跨 doc、只读、identity 不含 doc_id |
| `test_step6_acceptance.py` | 13 | 13 门槛逐条对应 |
| 更新 `test_schemas.py`/`test_v2_roundtrip.py` | +4 | 3 枚举 + RequirementItem 双 hash 往返保真 |

---

## 8. 13 条验收门槛（全部 PASSED）

| # | 门槛 |
|---|---|
| 1 | 版本不变：V_old 的 items/TP/TC 执行前后不被 Step 6 修改（只读） |
| 2 | 正确识别四种变化 UNCHANGED / MODIFIED / ADDED / DELETED |
| 3 | FieldSpec 隐性变更可发现：statement 不变但 min/max 18~60→18~65 判 MODIFIED |
| 4 | 追溯影响正确：modified RI → affected TP → affected TC 一个不漏 |
| 5 | Step 6 只读：执行前后 test_points/test_cases/statuses 无业务状态变化 |
| 6 | identity fingerprint 不含 doc_id：同 doc 跨版本同一 item fingerprint 相同 |
| 7 | 跨 doc 的两个 version 不可比 → 返回带 issue 的空报告，不崩溃 |
| 8 | 相似度兜底：statement 微调致 identity 漂移，ratio≥阈值判 MODIFIED，否则 ADDED/DELETED |
| 9 | affected_reason 正确细分（field/rule/permission/acceptance/added/deleted） |
| 10 | impact_level 代码化判定正确（fields/permission/rule 变=HIGH，措辞=LOW） |
| 11 | content_hash 分量比对：仅 fields 变（statement 同）也能定位并判 MODIFIED |
| 12 | schema_version=6；requirement_items.fingerprint 非 UNIQUE（跨版本可重复），普通索引生效 |
| 13 | V1 零回归（core/db.py、web/、data.db 不动） |

---

## 9. 关键决策记录（ADR 风格）

### D1. 为什么 RequirementItem 要双 hash 而非单 fingerprint？
单 fingerprint（含 statement）无法发现"statement 未改但 FieldSpec 改了"的隐性变更。identity（匹配键）与 content（变化判定）分离后，MODIFIED = identity 同 + content 异，语义清晰。与 Step 5 TestCase 双指纹同思路。

### D2. 为什么 identity fingerprint 不含 doc_id？
doc_id 是文档实体身份，非 item 逻辑身份本体。跨版本匹配需 item 逻辑身份稳定；doc 隔离通过"匹配只在同 doc 两版本间进行"保证，不污染 identity 公式。

### D3. 为什么 requirement_items.fingerprint 不建 UNIQUE？
与 TestPoint/TestCase 不同：后者 fingerprint 含 version_id，跨版本天然不同，可 UNIQUE 做幂等身份。RequirementItem 的 identity fingerprint **故意跨版本相同**（这正是变更影响匹配的键），故只能普通索引，UNIQUE 会导致 v2 插入同 fingerprint item 冲突。

### D4. 为什么 Step 6 只读、不改状态、不重生成？
职责边界：Step 6 发现影响 / Step 7 Review / Step 9 人工确认。若 Step 6 直接改 TC 状态（RE_REVIEW_REQUIRED）会侵入状态机与编辑闭环；若重生成会把 Change Detection + Regeneration + Dedup + Review + State 缠到一起。纯只读报告是最干净的边界。

### D5. 为什么相似度兜底用贪心最优配对？
identity 漂移（statement 微调）的 old/new 项需二次匹配。贪心取 ratio 最高配对（每项只用一次）简单且够用；阈值可配（默认 0.6）。复杂的最优二分图匹配（匈牙利算法）留待确有需求再升级。

### D6. 为什么 impact_level 用代码规则而非 LLM？
首批确定性优先：fields/permission/rule 变=HIGH、acceptance=MEDIUM、措辞=LOW 是可解释的代码规则。更细的语义级影响判断（如"这个字段变更对哪些业务场景影响大"）留给 Step 7 AI Reviewer。

---

## 10. 诚实边界

1. 相似度阈值（0.6）为启发式，边界 case（改了一半的 statement）可能误判为 MODIFIED 或 ADDED/DELETED；阈值可配。
2. impact_level 首批代码规则；语义级影响权重留 Step 7。
3. ChangeImpactReport 不持久化（返回值）；历史留存/趋势/前端可视化留 Step 13。
4. ADDED item 仅建议"需新增覆盖"，不自动触发 Step 3/4/5（重生成是未来能力）。
5. 追溯基于 Step 1~5 已落地的关联表；若历史数据缺 link（如 V1 迁移的 TP 无 item_ids），追溯链在该处自然截断（返回空，不报错）。

---

## 11. 验证命令（PowerShell）

```powershell
# 全量测试（期望 679 passed = Step 5 后 632 + Step 6 新增 47）
.\.venv\Scripts\python.exe -m pytest -q

# Step 6 专项（期望 43 passed = 11 + 19 + 13）
.\.venv\Scripts\python.exe -m pytest tests/test_traceability.py tests/test_change_impact.py tests/test_step6_acceptance.py -v

# 代码质量
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

---

## 12. 下一步 = Step 7「AI Reviewer 6 维结构化评审」

见 `docs/v2/PROGRESS.md` §9。开工前需与用户讨论确认：
- 6 维硬/软分工（coverage/duplication 代码算 vs accuracy/missing_risk LLM 判）
- ReviewScores 评分机制 + overall_score 加权
- 多轮评审 revision + trigger_type（与 Step 8/9 衔接）
- finding.target 多态写入校验（复用 ReferentialValidator）
- auto_fixable finding 与 Step 8 Optimizer 边界
- 如何消费 Step 6 ChangeImpactReport 对受影响资产优先复审
- TestCase 状态机 VALIDATED → REVIEWED 转移触发
